#!/usr/bin/env python3
"""
Natural verse translation step: turns each verse's word-level glosses
into one simple, literal, readable sentence (TR/MT order kept, no
paraphrase), with a Strong's tag after every rendered word, e.g.
"Pada mula{G746}". Stored in verse_translation.text (tagged form; strip
the tags for plain reading, see bible_export_common.get_verse_translation).
One row per (verse, language).

Commands:
  sync      call the API now, append results to the JSONL
  submit    send all units as ONE Batch API job (50% cheaper, async)
  retrieve  poll the job, append results to the JSONL
  load      validate tags against the verse's real Strong's numbers (unknown
            tags are dropped), then upsert into verse_translation (creates
            the table if missing, so no DB rebuild is needed)

Resumable: verses already in the JSONL or in the table are skipped.
Spelling and punctuation follow sources/eydv.md (EYD Edisi V), appended to the system prompt
(--guidelines to use another file).
Does not read ai_gloss directly -- it uses bible_export_common's gloss
priority (ai_gloss -> translated lexicon -> ...), so run the gloss step
first for best quality.

Usage:
  python3 scripts/generate_verse_translations.py sync --db output/interlinear.sqlite \
    --out output/staging/verses_id.jsonl --book JHN --chapter 1
  python3 scripts/generate_verse_translations.py submit --db output/interlinear.sqlite \
    --out output/staging/verses_id.jsonl
  python3 scripts/generate_verse_translations.py retrieve --out output/staging/verses_id.jsonl
  python3 scripts/generate_verse_translations.py load --db output/interlinear.sqlite \
    --jsonl output/staging/verses_id.jsonl
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec

DDL = """CREATE TABLE IF NOT EXISTS verse_translation (
    verse_id       INTEGER NOT NULL REFERENCES verse(verse_id),
    language       TEXT NOT NULL,
    text           TEXT NOT NULL,
    model          TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    generated_at   TEXT NOT NULL,
    reviewed       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (verse_id, language)
)"""

LANG_NAMES = {"id": "Indonesian"}
MAX_TOKENS = 12000
TAG = re.compile(r"\{([HG])0*(\d+)[A-Za-z]?\}")


def strong_nums(parts):
    """['H9003', 'H7225G', 'G0746'] -> ['H7225', 'G746']. Drops STEPBible's
    H9000+ grammatical-particle pseudo-codes (not real Strong's entries)."""
    out = []
    for p in parts:
        m = re.match(r"([HG])0*(\d+)", p or "")
        if m and not (m[1] == "H" and int(m[2]) >= 9000):
            out.append(f"{m[1]}{m[2]}")
    return out


def check_tags(text, allowed):
    """Normalize {G0746} -> {G746}; drop tags not among the verse's own
    Strong's numbers. Returns (clean_text, dropped_count, used_set)."""
    dropped, used = 0, set()

    def fix(m):
        nonlocal dropped
        num = f"{m[1]}{m[2]}"
        if num in allowed:
            used.add(num)
            return "{" + num + "}"
        dropped += 1
        return ""

    return TAG.sub(fix, text), dropped, used

SYSTEM = """You translate verses of the Hebrew Masoretic Text (OT) and the Greek Textus Receptus (NT) into simple, literal, natural {lang}.

Input: one line per verse, "<verse>: word word ...", each word as surface|Strong's|gloss. The gloss is an already reviewed, context-fitted word-level gloss. Strong's may be empty or several numbers separated by commas.

Rules:
- Render each verse as ONE fluent {lang} sentence (or the verse's own sentences), staying close to the source: keep source word order where {lang} grammar allows, no paraphrase, no commentary, no doctrinal smoothing.
- Follow the given glosses for key terms, including divine names, exactly as glossed. Do not substitute other renderings.
- Add a word absent from the source only when {lang} grammar requires it.
- Strong's tags: after each translated word that renders a source word, write that word's Strong's number in curly braces with no space, e.g. mula{{G746}}. If one translated word renders several source words, give several tags; if a source word is rendered by a phrase, tag the phrase's main word. Tag every source word that has a Strong's number, copy the numbers exactly as given, never invent one, and use only numbers from that verse's input. Words you add for grammar, and source words with an empty Strong's field, get no tag. Tags may appear in any order.
- Words glossed "-" or "(tanda objek)" are not rendered in the sentence. Put their tag right after the word they belong with (the noun an article modifies, the object an H853 marks), e.g. langit{{H8064}}{{H853}}.
- Pronouns for God stay capitalized exactly as in the glosses (Ia, Dia, -Nya, Engkau, -Mu, Aku, -Ku). Do not lowercase them.
- Paragraphs: begin the text of a verse with "¶ " when that verse starts a new paragraph (a change of speaker, scene, subject or time; usually every 3 to 8 verses). Always mark verse 1 of a chapter. Use ¶ nowhere else.
- Poetry: in poetic verses (Psalms, Proverbs, Job, Song of Songs, Lamentations, prophetic oracles, songs inside narrative), separate the lines (cola) of the verse with " | ", one per Hebrew colon, usually 2 or 3 per verse. Use the accents in the Hebrew text (e.g. etnachta) to place the breaks. In prose never use "|".
- Direct speech takes double quotation marks. The reply is JSON, so escape every double quotation mark inside the text with a backslash, e.g. Ia berkata, \\"Marilah.\\" A quotation that continues over several verses opens in its first verse and closes in its last. Use single quotation marks for a quotation inside a quotation.
- Do not output verse numbers inside the text. One entry per input verse.

Respond with ONLY a JSON array, no markdown fences: [{{"verse": <int>, "text": "<translation>"}}] in the same order as the input."""


def parse_chapters(spec):
    """'all' -> None; '1-3,5' -> {1,2,3,5}."""
    if not spec or spec.lower() == "all":
        return None
    out = set()
    for part in spec.split(","):
        lo, _, hi = part.strip().partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def done_ids(conn, out, language):
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row["language"] == language:
                    done.add(row["verse_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    try:
        done.update(r[0] for r in conn.execute(
            "SELECT verse_id FROM verse_translation WHERE language = ?", (language,)))
    except sqlite3.OperationalError:
        pass  # table not created yet
    return done


def build_units(conn, a):
    """One unit per chapter chunk (<= --max-verses, split on verse
    boundaries): {"label", "verses": {verse_no: verse_id}, "prompt"}."""
    wanted = parse_chapters(a.chapter)
    skip = done_ids(conn, a.out, a.language)
    q = ("SELECT DISTINCT v.book_id, b.code, b.name, v.chapter FROM verse v "
         "JOIN book b ON b.book_id = v.book_id")
    params = []
    if a.book:
        q += " WHERE b.code = ?"
        params.append(a.book.upper())
    cur = conn.execute(q + " ORDER BY b.ordinal, v.chapter", params)
    units = []
    for book_id, code, name, chapter in cur.fetchall():
        if wanted is not None and chapter not in wanted:
            continue
        verses = conn.execute(
            "SELECT verse_id, verse FROM verse WHERE book_id = ? AND chapter = ? ORDER BY verse",
            (book_id, chapter)).fetchall()
        verses = [(vid, v) for vid, v in verses if vid not in skip]
        for i in range(0, len(verses), a.max_verses):
            chunk = verses[i:i + a.max_verses]
            lines = []
            for vid, v in chunk:
                words = bec.get_words_for_verse(conn, vid, a.language)
                if words:
                    lines.append(f"{v}: " + " ".join(
                        f'{w["surface"]}|{",".join(strong_nums(w["strongs_parts"]))}|{w["gloss"]}' for w in words))
            if lines:
                units.append({
                    "label": f"{name} {chapter}",
                    "verses": {v: vid for vid, v in chunk},
                    "prompt": "Translate these verses:\n" + "\n".join(lines),
                })
    return units


DEFAULT_GUIDELINES = Path("sources/eydv.md")


def load_guidelines(path):
    """Spelling and punctuation rules appended to the system prompt. With no path,
    sources/eydv.md is used if it exists; an explicit path that is missing is an error."""
    p = Path(path) if path else DEFAULT_GUIDELINES
    if not p.is_file():
        if path:
            sys.exit(f"ERROR: guidelines file not found: {p}")
        print(f"NOTE: {p} not found; translating without spelling guidelines.", file=sys.stderr)
        return ""
    text = p.read_text(encoding="utf-8").strip()
    print(f"Guidelines: {p} ({len(text.split())} words, roughly {len(text) // 4}-{len(text) // 3} tokens)", file=sys.stderr)
    return text


def system_prompt(language, guidelines=""):
    prompt = SYSTEM.format(lang=LANG_NAMES.get(language, language))
    if not guidelines:
        return prompt
    head, tail = prompt.split("\n\nRespond with ONLY", 1)
    return (head + "\n\nSpelling and punctuation: follow these rules (EYD Edisi V) in every verse.\n\n"
            + guidelines + "\n\nRespond with ONLY" + tail)


def parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json")
    return json.loads(text)


def write_results(out_f, verses, items, a, model):
    """Append valid {"verse","text"} items; returns how many were written."""
    now, n = datetime.now(timezone.utc).isoformat(), 0
    for it in items or []:
        try:
            vid, text = verses[int(it["verse"])], it["text"].strip()
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        if not text:
            continue
        out_f.write(json.dumps({
            "verse_id": vid, "language": a.language, "text": text, "model": model,
            "prompt_version": a.prompt_version, "generated_at": now,
        }, ensure_ascii=False) + "\n")
        n += 1
    out_f.flush()
    if n < len(verses):
        print(f"  WARNING: {len(verses) - n} verse(s) missing from response; re-run to retry", file=sys.stderr)
    return n


def ask(client, a, prompt, retries=3):
    import batch_api_helper as bah
    for attempt in range(1, retries + 1):
        try:
            msg = client.messages.create(
                model=a.model, max_tokens=MAX_TOKENS,
                system=[{"type": "text", "text": system_prompt(a.language, a.guidelines_text),
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt}])
            parsed = bah.parse_json_response("".join(b.text for b in msg.content if b.type == "text"))
            if parsed is None:
                raise ValueError("no JSON objects in reply")
            return parsed
        except Exception as e:  # bad JSON or API error
            print(f"  attempt {attempt}/{retries} failed: {e}", file=sys.stderr)
            if attempt == retries:
                raise
            time.sleep(2 ** attempt)


def cmd_sync(a):
    import anthropic
    a.guidelines_text = load_guidelines(a.guidelines)
    conn = sqlite3.connect(a.db)
    units = build_units(conn, a)
    print(f"{len(units)} unit(s) to translate", file=sys.stderr)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    client, total = anthropic.Anthropic(), 0
    with a.out.open("a", encoding="utf-8") as f:
        for i, u in enumerate(units, 1):
            print(f"[{i}/{len(units)}] {u['label']} ....", end=" ", file=sys.stderr, flush=True)
            try:
                items = ask(client, a, u["prompt"])
            except Exception:
                print("FAILED (re-run to retry)", file=sys.stderr)
                continue
            total += write_results(f, u["verses"], items, a, a.model)
            print("done", file=sys.stderr)
    print(f"Wrote {total} verses -> {a.out}", file=sys.stderr)


def cmd_submit(a):
    import anthropic
    import batch_api_helper as bah
    conn = sqlite3.connect(a.db)
    units = build_units(conn, a)
    if not units:
        print("Nothing to do.", file=sys.stderr)
        return
    system = system_prompt(a.language, load_guidelines(a.guidelines))
    requests = [(f"vt-{i:05d}", system, u["prompt"], a.model, MAX_TOKENS) for i, u in enumerate(units)]
    bah.submit_batch_job(anthropic.Anthropic(), requests, a.batch_job_file)
    a.batch_job_file.with_suffix(".membership.json").write_text(json.dumps({
        "model": a.model,
        "units": {f"vt-{i:05d}": u["verses"] for i, u in enumerate(units)},
    }), encoding="utf-8")
    print(f"Submitted {len(requests)} requests. Next: retrieve --out {a.out}", file=sys.stderr)


def cmd_retrieve(a):
    import anthropic
    import batch_api_helper as bah
    job = json.loads(a.batch_job_file.read_text())
    mem = json.loads(a.batch_job_file.with_suffix(".membership.json").read_text())
    client = anthropic.Anthropic()
    batch = bah.poll_batch_job(client, job["batch_id"], a.poll_interval, a.max_wait)
    if batch.processing_status != "ended":
        print("Not finished yet; run retrieve again later.", file=sys.stderr)
        return
    results, total = bah.fetch_batch_results(client, job["batch_id"]), 0
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("a", encoding="utf-8") as f:
        for cid, verses in mem["units"].items():
            items = bah.parse_json_response(results.get(cid))
            if items is None:
                print(f"  WARNING: {cid} has no usable result", file=sys.stderr)
                continue
            total += write_results(f, {int(k): v for k, v in verses.items()}, items, a, mem["model"])
    print(f"Wrote {total} verses -> {a.out}. Next: load --jsonl {a.out}", file=sys.stderr)


def cmd_load(a):
    conn = sqlite3.connect(a.db)
    conn.execute(DDL)
    valid = {r[0] for r in conn.execute("SELECT verse_id FROM verse")}
    allowed = {}  # verse_id -> its real Strong's numbers
    for vid, st, parts in conn.execute("SELECT verse_id, strongs, strongs_parts FROM word"):
        allowed.setdefault(vid, set()).update(strong_nums([st] + (json.loads(parts) if parts else [])))
    rows, dropped, untagged, incomplete = [], 0, 0, 0
    for line in a.jsonl.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("verse_id") in valid and r.get("text"):
            text, bad, used = check_tags(r["text"], allowed[r["verse_id"]])
            dropped += bad
            untagged += not used
            incomplete += bool(allowed[r["verse_id"]] - used)
            rows.append((r["verse_id"], r["language"], text, r["model"],
                         r["prompt_version"], r["generated_at"]))
    conn.executemany(
        """INSERT INTO verse_translation (verse_id, language, text, model, prompt_version, generated_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(verse_id, language) DO UPDATE SET text = excluded.text,
             model = excluded.model, prompt_version = excluded.prompt_version,
             generated_at = excluded.generated_at""", rows)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM verse_translation").fetchone()[0]
    verses = len(valid)
    print(f"Loaded {len(rows)} rows. Coverage: {total}/{verses} verses ({100 * total / verses:.1f}%)", file=sys.stderr)
    print(f"Tags: {dropped} unknown tag(s) dropped; {untagged} verse(s) with no tags; "
          f"{incomplete} verse(s) missing some source Strong's (review these).", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, db=True):
        if db:
            p.add_argument("--db", required=True, type=Path)
        p.add_argument("--out", required=True, type=Path, help="JSONL results file")
        p.add_argument("--language", default="id")
        p.add_argument("--prompt-version", default="v2")

    def scope(p):
        p.add_argument("--model", default="claude-opus-5-5")
        p.add_argument("--book", help="Book code, e.g. JHN (default: all)")
        p.add_argument("--chapter", help="'all', '1', or ranges like 1-3,5 (default: all)")
        p.add_argument("--max-verses", type=int, default=40, help="Max verses per API request")
        p.add_argument("--guidelines", type=Path, help="Spelling rules appended to the prompt (default: sources/eydv.md if present)")

    jobfile = dict(type=Path, default=Path("output/staging/verse_batch_job.json"))
    p = sub.add_parser("sync", help="Call the API now"); common(p); scope(p); p.set_defaults(func=cmd_sync)
    p = sub.add_parser("submit", help="Submit one Batch API job"); common(p); scope(p)
    p.add_argument("--batch-job-file", **jobfile); p.set_defaults(func=cmd_submit)
    p = sub.add_parser("retrieve", help="Poll and fetch batch results"); common(p, db=False)
    p.add_argument("--batch-job-file", **jobfile)
    p.add_argument("--poll-interval", type=int, default=30)
    p.add_argument("--max-wait", type=int, default=None)
    p.set_defaults(func=cmd_retrieve)
    p = sub.add_parser("load", help="Load JSONL into verse_translation")
    p.add_argument("--db", required=True, type=Path)
    p.add_argument("--jsonl", required=True, type=Path)
    p.set_defaults(func=cmd_load)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()