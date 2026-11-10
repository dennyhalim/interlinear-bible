#!/usr/bin/env python3
"""
Generate contextual (verse-specific) Indonesian glosses for every word,
one chapter per API call (large chapters split at verse boundaries), grounded in the
already-translated lexicon (lexicon_gloss_translation).

Modes:
  sync      call the API now using Google Gemini
  submit    (Not applicable for standard Gemini REST; stubbed or run via sync)
  retrieve  (Not applicable for standard Gemini REST; stubbed or run via sync)

Usage:
  export GEMINI_API_KEY=your-api-key
  python3 scripts/generate_ai_glosses_gemini.py sync \
    --db output/interlinear.sqlite \
    --out output/staging/glosses/GEN.jsonl \
    --book GEN --limit-chapters 1
"""

import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from google import genai
    from google.genai import types
except ImportError:
    print(
        "ERROR: google-genai package not installed.\n"
        "Run: pip install google-genai --break-system-packages",
        file=sys.stderr,
    )
    sys.exit(1)

SYSTEM_PROMPT_TEMPLATE = """You are producing word-by-word contextual Indonesian glosses for an interlinear Bible covering the Hebrew Masoretic Text and Greek Textus Receptus.

For each word in a verse, you are given:
- The original-language surface form and transliteration
- Its Strong's number and morphology code
- Its lexicon gloss in Indonesian (the DEFAULT/DICTIONARY sense of this word, already reviewed) -- use this as your starting point
- Where available, an English contextual note for the whole verse (from the source dataset) as an additional cross-check, not the primary source

Your task: for each word, choose or lightly adapt the Indonesian gloss that fits THIS SPECIFIC VERSE'S context -- not just repeat the dictionary sense if the verse calls for a different one. For example, the same Greek word can mean "word" in one context and "matter/case" (a legal proceeding) in another; pick the sense that fits.

Guidelines:
- Prefer the lexicon gloss you were given whenever it already fits the context -- most words most of the time.
- Only deviate from the lexicon gloss when the verse context clearly calls for a different sense of the same word.
- Keep each gloss SHORT (a word or short phrase), matching interlinear conventions, not a full sentence explanation.
- The lexicon gloss is authoritative for terminology. If it uses an Indonesian term where Bibles usually use a transliterated or borrowed form (for example "benam" instead of "baptis"), use the lexicon's term in every grammatical form (membenamkan, dibenamkan, pembenaman) and never substitute the transliteration or the conventional loanword. Follow standard Bible conventions only for terms where the lexicon gives no Indonesian word, and for proper names.
- Write one gloss per word, in Indonesian only. A word shown with parts joined by "/" (a prefix and its stem) gets a single gloss: fuse the parts naturally ("di bumi"), leave out unrendered parts such as the article, and never use "/" or a "-/" fragment. Never copy English words or markers from the hints (such as <obj.> or [Obj.]).
- Lexicon glosses may look like "general sense: specific sense" (e.g. "perempuan: istri"), or "a/b" for equivalent alternatives. The part after the colon is the sense assigned to this word's disambiguated Strong's number; prefer it when the context fits, otherwise use the general sense before the colon. Words in parentheses are optional or usage hints: an ALL-CAPS hint such as (WAKTU) names the kind of object and must not appear in your gloss (write "menghabiskan", not "menghabiskan (WAKTU)").
- The Hebrew direct-object marker (Strong's H0853, אֵת/אֶת) has no translation: gloss it exactly "(tanda objek)". If it carries a pronoun suffix (e.g. אֹתוֹ), gloss "(tanda objek) ", e.g. "(tanda objek) dia".
- A word with no rendering in context (e.g. an untranslated article) is glossed "-" (a single hyphen), never left blank.
- Finite verbs: always show the subject given by the morphology (person/number), either as a free pronoun ("aku mengenal", "dia berkata") or as the standard clitic ("kukenal", "kaulihat"). Do not add a subject for participles, infinitives or imperatives.
- Pronouns referring to God (YHWH, Elohim, the Lord, the Spirit) are capitalized, following the Indonesian Bible convention: Ia/Dia, -Nya, Engkau/-Mu (addressed to God), Aku/-Ku (God speaking). The same pronouns for people, angels or idols stay lowercase: ia/dia, -nya, engkau/kamu, -mu, aku/-ku. Decide from the chapter context who the referent is. Apply this to free pronouns, pronoun suffixes on nouns, prepositions and verbs, and to the subject shown on finite verbs ("Ia berfirman" for God, "ia berkata" for a person).
- If you're genuinely uncertain between two senses, put your primary choice in "gloss" and the alternative in "alt_gloss", with a brief "note" explaining the ambiguity. Otherwise omit those keys -- don't manufacture uncertainty that isn't there.

Respond with ONLY a JSON array: the reply starts with [ and ends with ], the objects are separated by commas, one object per line, no indentation, no other text, no markdown code fences.
Each element: {{"word_id": <int>, "gloss": "<str>"}}
Add "alt_gloss" and "note" keys to an element only when that word is genuinely ambiguous.
The array must contain exactly one object per input word, in the same order, with the same word_id values.

Target language: {language}."""

TARGET_NOTE = """
TARGETED MODE: some words carry "context_only": true and an existing "gloss". They are already glossed and are shown only so you can read the verse in context. Do NOT output them. The array must contain exactly one object per word WITHOUT "context_only", in the same order."""

TERM_RULES = {
    "H5002": 'ne\'um ("utterance, declares"): "firman" when God speaks (typically right before a divine name: "firman \n',
    "H113": 'adon: "tuan" for a human lord or master ("tuanku" = my lord); "Tuhan" only when it refers to God.\n',
    "H136": 'Adonai: always "Tuhan" (God), never "tuan". With a suffix, "Tuhanku".\n',
    "G2962": 'kyrios: "Tuhan" when it refers to God or Jesus as Lord; "tuan" for a human master, owner or polite address (sir).\n',
    "G1203": 'despotes: "tuan" for a human master; "Tuhan" when it names or addresses God.\n',
}

BOOK_GROUPS = {
    "torah": ["GEN", "EXO", "LEV", "NUM", "DEU"],
    "historical": [
        "JOS",
        "JDG",
        "RUT",
        "1SA",
        "2SA",
        "1KI",
        "2KI",
        "1CH",
        "2CH",
        "EZR",
        "NEH",
        "EST",
    ],
    "wisdom": ["JOB", "PSA", "PRO", "ECC", "SNG"],
    "major_prophets": ["ISA", "JER", "LAM", "EZK", "DAN"],
    "minor_prophets": [
        "HOS",
        "JOL",
        "AMO",
        "OBA",
        "JON",
        "MIC",
        "NAM",
        "HAB",
        "ZEP",
        "HAG",
        "ZEC",
        "MAL",
    ],
    "gospels_acts": ["MAT", "MRK", "LUK", "JHN", "ACT"],
    "pauline_epistles": [
        "ROM",
        "1CO",
        "2CO",
        "GAL",
        "EPH",
        "PHP",
        "COL",
        "1TH",
        "2TH",
        "1TI",
        "2TI",
        "TIT",
        "PHM",
    ],
    "general_epistles_revelation": [
        "HEB",
        "JAS",
        "1PE",
        "2PE",
        "1JN",
        "2JN",
        "3JN",
        "JUD",
        "REV",
    ],
}


def resolve_scope(book, group, testament):
    given = [
        name
        for name, val in [
            ("--book", book),
            ("--group", group),
            ("--testament", testament),
        ]
        if val
    ]
    if len(given) > 1:
        print(
            f"ERROR: only one of --book/--group/--testament may be given at once, got: {', '.join(given)}",
            file=sys.stderr,
        )
        sys.exit(1)
    if group:
        keys = [g.strip().lower() for g in group.split(",") if g.strip()]
        bad = [k for k in keys if k not in BOOK_GROUPS]
        if bad:
            print(
                f"ERROR: unknown --group {', '.join(bad)}. Valid groups: {', '.join(BOOK_GROUPS.keys())}",
                file=sys.stderr,
            )
            sys.exit(1)
        return {"book_codes": [c for k in keys for c in BOOK_GROUPS[k]]}
    if book:
        return {"book_codes": [b.strip().upper() for b in book.split(",") if b.strip()]}
    if testament:
        testament_code = testament.upper()
        if testament_code not in ("OT", "NT"):
            print(
                f"ERROR: --testament must be 'OT' or 'NT', got '{testament}'",
                file=sys.stderr,
            )
            sys.exit(1)
        return {"testament": testament_code}
    return {}


def base_strongs(s):
    m = re.match(r"([HG])0*(\d+)", s or "")
    return f"{m[1]}{m[2]}" if m else None


def parse_targets(spec):
    if not spec:
        return None
    out = {base_strongs(s.strip().upper()) for s in spec.split(",") if s.strip()}
    if not out or None in out:
        print(
            "ERROR: --strongs needs Strong's numbers like H5002,G3056", file=sys.stderr
        )
        sys.exit(1)
    return out


def term_rules_for(words):
    present = {base_strongs(w[5]) for w in words}
    rules = [f"- {k}: {v}" for k, v in TERM_RULES.items() if k in present]
    return (
        ("\n\nTerminology rules for words in this batch (follow them):\n" + "\n".join(rules))
        if rules
        else ""
    )


def get_chapters(cur, scope=None, limit_chapters=None):
    query = """
    SELECT DISTINCT v.book_id, b.code, b.name, v.chapter
    FROM verse v JOIN book b ON v.book_id = b.book_id
    """
    params = []
    scope = scope or {}
    if "book_codes" in scope:
        placeholders = ",".join("?" * len(scope["book_codes"]))
        query += f" WHERE b.code IN ({placeholders})"
        params.extend(scope["book_codes"])
    elif "testament" in scope:
        query += " WHERE b.testament = ?"
        params.append(scope["testament"])
    query += " ORDER BY b.ordinal, v.chapter"
    cur.execute(query, params)
    chapters = cur.fetchall()
    if limit_chapters:
        chapters = chapters[:limit_chapters]
    return chapters


def get_chapter_words(cur, book_id, chapter, language, already_done_ids):
    cur.execute(
        """
        WITH ranked_lexicon AS (
            SELECT w2.word_id AS w_word_id, le.lexicon_id, le.gloss,
                   ROW_NUMBER() OVER (
                       PARTITION BY w2.word_id
                       ORDER BY (le.dstrong = w2.strongs) DESC, le.lexicon_id ASC
                   ) AS rn
            FROM word w2
            JOIN lexicon_entry le ON le.dstrong = w2.strongs OR le.estrong = w2.strongs
            WHERE w2.verse_id IN (SELECT verse_id FROM verse WHERE book_id = ?2 AND chapter = ?3)
        )
        SELECT w.word_id, v.verse, w.position, w.surface, w.translit, w.strongs, w.morph_code,
               w.gloss_source, rl.gloss as lexicon_gloss_en, lgt.gloss as lexicon_gloss_target
        FROM word w
        JOIN verse v ON w.verse_id = v.verse_id
        LEFT JOIN ranked_lexicon rl ON rl.w_word_id = w.word_id AND rl.rn = 1
        LEFT JOIN lexicon_gloss_translation lgt ON lgt.lexicon_id = rl.lexicon_id AND lgt.language = ?1
        WHERE v.book_id = ?2 AND v.chapter = ?3
        ORDER BY v.verse, w.position
        """,
        (language, book_id, chapter),
    )
    rows = cur.fetchall()
    return [r for r in rows if r[0] not in already_done_ids]


def split_into_batches(words, max_words_per_batch):
    if len(words) <= max_words_per_batch:
        return [words]
    batches = []
    current = []
    current_verse = None
    for w in words:
        verse = w[1]
        if current and verse != current_verse and len(current) >= max_words_per_batch:
            batches.append(current)
            current = []
        current.append(w)
        current_verse = verse
    if current:
        batches.append(current)
    return batches


def load_ref_map(cur):
    cur.execute(
        "SELECT w.word_id, b.code, v.chapter, v.verse, w.position, w.surface FROM word w "
        "JOIN verse v ON v.verse_id = w.verse_id JOIN book b ON b.book_id = v.book_id"
    )
    return {(b, c, v, p): (wid, s) for wid, b, c, v, p, s in cur.fetchall()}


def load_already_done(out_path, ref_map=None):
    done = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("book") is not None:
                    hit = (
                        ref_map.get(
                            (
                                row["book"],
                                row.get("chapter"),
                                row.get("verse"),
                                row.get("position"),
                            )
                        )
                        if ref_map
                        else None
                    )
                    if hit and row.get("surface") in (None, hit[1]):
                        done.add(hit[0])
                elif "word_id" in row:
                    done.add(row["word_id"])
    return done


def plan_batches(cur, book_id, chapter, args, already_done, targets):
    if targets is None:
        words = get_chapter_words(cur, book_id, chapter, args.language, already_done)
        return (
            [(b, None) for b in split_into_batches(words, args.max_words_per_batch)]
            if words
            else []
        )

    words = get_chapter_words(cur, book_id, chapter, args.language, set())
    target_ids = {
        w[0]
        for w in words
        if base_strongs(w[5]) in targets and w[0] not in already_done
    }
    if not target_ids:
        return []

    verses = sorted({w[1] for w in words})
    target_verses = {w[1] for w in words if w[0] in target_ids}
    keep = set()
    for i, v in enumerate(verses):
        if v in target_verses:
            keep.update(
                verses[
                    max(0, i - args.context_verses) : i + args.context_verses + 1
                ]
            )

    selected = [w for w in words if w[1] in keep]
    cur.execute(
        "SELECT ag.word_id, ag.gloss FROM ai_gloss ag JOIN word w ON w.word_id = ag.word_id "
        "JOIN verse v ON v.verse_id = w.verse_id WHERE v.book_id = ? AND v.chapter = ?",
        (book_id, chapter),
    )
    ai = dict(cur.fetchall())
    context = {
        w[0]: ai.get(w[0]) or w[9] or w[8] or ""
        for w in selected
        if w[0] not in target_ids
    }
    return [
        (b, context)
        for b in split_into_batches(selected, args.max_words_per_batch)
        if any(w[0] in target_ids for w in b)
    ]


def expected_ids(batch, context):
    return {w[0] for w in batch if context is None or w[0] not in context}


def word_meta(code, chapter, w):
    return {
        "book": code,
        "chapter": chapter,
        "verse": w[1],
        "position": w[2],
        "surface": w[3],
    }


def make_record(r, meta, language, model, prompt_version, now):
    return {
        "word_id": r["word_id"],
        **meta,
        "gloss": r["gloss"],
        "alt_gloss": r.get("alt_gloss") or None,
        "note": r.get("note") or None,
        "language": language,
        "model": model,
        "prompt_version": prompt_version,
        "generated_at": now,
    }


def system_for(language, targeted=False):
    return SYSTEM_PROMPT_TEMPLATE.format(language=language) + (
        TARGET_NOTE if targeted else ""
    )


def build_prompt(words, context=None):
    lines = []
    for (
        word_id,
        verse,
        position,
        surface,
        translit,
        strongs,
        morph,
        gloss_source,
        lex_en,
        lex_target,
    ) in words:
        parts = [
            f'"word_id": {word_id}',
            f'"verse": {verse}',
            f'"surface": "{surface}"',
            f'"translit": "{translit}"',
            f'"strongs": "{strongs or ""}"',
            f'"morph": "{morph or ""}"',
        ]
        if context is not None and word_id in context:
            parts += [f'"gloss": "{context[word_id]}"', '"context_only": true']
        else:
            parts.append(f'"lexicon_gloss": "{lex_target or lex_en or ""}"')
            if gloss_source:
                parts.append(f'"verse_context_hint": "{gloss_source}"')
        lines.append("{" + ", ".join(parts) + "}")

    head = (
        "Generate contextual glosses for these words:"
        if context is None
        else "Generate contextual glosses ONLY for the words without context_only:"
    )
    to_gloss = [w for w in words if context is None or w[0] not in context]
    return head + "\n[\n" + ",\n".join(lines) + "\n]" + term_rules_for(to_gloss)


def parse_json_response(text):
    if not text:
        return None
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: attempt to extract objects line by line
        results = []
        for line in text.splitlines():
            line = line.strip().rstrip(",")
            if line.startswith("{") and line.endswith("}"):
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return results if results else None


def call_model(client, model, words, language, context=None, max_retries=3):
    system_prompt = system_for(language, context is not None)
    prompt = build_prompt(words, context)

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                    temperature=0.2,
                ),
            )
            text = response.text.strip() if response.text else ""
            parsed = parse_json_response(text)
            if parsed is None:
                raise json.JSONDecodeError("no JSON objects in reply", text, 0)
            return parsed
        except Exception as e:
            print(
                f" attempt {attempt}/{max_retries} failed: {e}", file=sys.stderr
            )
            if attempt == max_retries:
                raise
            time.sleep(2**attempt)
    return None


def open_db(path):
    if not Path(path).is_file() or Path(path).stat().st_size == 0:
        print(
            f"ERROR: database missing or empty: {Path(path).resolve()}",
            file=sys.stderr,
        )
        sys.exit(1)
    return sqlite3.connect(path)


def cmd_sync(args):
    if not args.dry_run:
        args.out.parent.mkdir(parents=True, exist_ok=True)
    conn = open_db(args.db)
    cur = conn.cursor()
    ref_map = load_ref_map(cur)
    already_done = load_already_done(args.out, ref_map)
    print(
        f"Already glossed: {len(already_done)} words (resuming)", file=sys.stderr
    )

    targets = parse_targets(args.strongs)
    chapters = get_chapters(
        cur,
        scope=resolve_scope(args.book, args.group, args.testament),
        limit_chapters=args.limit_chapters,
    )
    print(
        f"Chapters to consider: {len(chapters)}"
        + (f" (targeted: {', '.join(sorted(targets))})" if targets else ""),
        file=sys.stderr,
    )

    client = None if args.dry_run else genai.Client()
    words_done = calls = 0
    seen_chapters, seen_verses = set(), set()

    with (
        contextlib.nullcontext()
        if args.dry_run
        else args.out.open("a", encoding="utf-8")
    ) as out_f:
        for chapter_idx, (book_id, code, name, chapter) in enumerate(
            chapters, 1
        ):
            plan = plan_batches(cur, book_id, chapter, args, already_done, targets)
            if not plan:
                continue

            label = f"{name} {chapter}"
            print(
                f"[{chapter_idx}/{len(chapters)}] {label} ({len(plan)} request(s)) ....",
                end=" ",
                flush=True,
                file=sys.stderr,
            )
            chapter_ok = True

            for batch_num, (batch, context) in enumerate(plan, 1):
                expected = expected_ids(batch, context)
                if args.dry_run:
                    words_done += len(expected)
                    calls += 1
                    seen_chapters.add((code, chapter))
                    seen_verses.update(
                        (code, chapter, w[1]) for w in batch if w[0] in expected
                    )
                    continue

                try:
                    results = call_model(
                        client, args.model, batch, args.language, context
                    )
                except Exception as e:
                    chapter_ok = False
                    print("FAILED", file=sys.stderr)
                    print(
                        f"  {label} batch {batch_num}/{len(plan)} failed after retries: {e}",
                        file=sys.stderr,
                    )
                    continue

                calls += 1
                results = [
                    r
                    for r in results
                    if isinstance(r, dict)
                    and "word_id" in r
                    and isinstance(r.get("gloss"), str)
                ]
                missing = expected - {r["word_id"] for r in results}
                if missing:
                    print(
                        f"\n  WARNING: {label} response missing {len(missing)} words: {sorted(missing)[:20]}",
                        file=sys.stderr,
                    )

                meta = {w[0]: word_meta(code, chapter, w) for w in batch}
                now = datetime.now(timezone.utc).isoformat()

                for r in results:
                    if r["word_id"] not in expected:
                        continue
                    out_f.write(
                        json.dumps(
                            make_record(
                                r,
                                meta[r["word_id"]],
                                args.language,
                                args.model,
                                args.prompt_version,
                                now,
                            ),
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    words_done += 1
                    m = meta[r["word_id"]]
                    seen_chapters.add((code, chapter))
                    seen_verses.add((code, chapter, m["verse"]))

                out_f.flush()

            print("done" if chapter_ok else "(with failures)", file=sys.stderr)

    verb = "would be sent" if args.dry_run else "glossed"
    print(
        f"\nFinished. {words_done} words {verb} in {len(seen_chapters)} chapters, {len(seen_verses)} verses, "
        f"{calls} API calls -> {args.out}",
        file=sys.stderr,
    )
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    common_args = {
        "--language": dict(default="id"),
        "--prompt-version": dict(default="v2"),
        "--max-words-per-batch": dict(
            type=int,
            default=150,
            help="Split a chapter into multiple requests if it exceeds this many words",
        ),
        "--book": dict(
            help="Only these books (canonical codes, comma-separated, e.g. GEN,JHN)"
        ),
        "--group": dict(
            help=f"Only these thematic groups (comma-separated). Valid: {', '.join(BOOK_GROUPS.keys())}"
        ),
        "--testament": dict(help="Only one testament: OT or NT"),
        "--limit-chapters": dict(
            type=int, help="Only process this many chapters (for testing)"
        ),
        "--strongs": dict(
            help="Targeted mode: only (re)gloss words with these Strong's numbers, e.g. H5002,H113"
        ),
        "--context-verses": dict(
            type=int,
            default=1,
            help="Targeted mode: verses of context on each side",
        ),
        "--dry-run": dict(
            action="store_true", help="Report what would be sent, but call nothing"
        ),
    }

    sync_p = sub.add_parser("sync", help="Synchronous calls using Gemini API")
    sync_p.add_argument("--db", required=True, type=Path)
    sync_p.add_argument("--out", required=True, type=Path)
    sync_p.add_argument("--model", default="gemini-2.5-flash")
    for flag, kwargs in common_args.items():
        sync_p.add_argument(flag, **kwargs)
    sync_p.set_defaults(func=cmd_sync)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
