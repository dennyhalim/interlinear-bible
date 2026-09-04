#!/usr/bin/env python3
"""
Generate contextual (verse-specific) Indonesian glosses for every word,
batched one chapter per API call, grounded in the already-translated
lexicon (lexicon_gloss_translation) rather than having the model
re-derive meaning from scratch per word.

Why chapter-level batching, not per-verse or an arbitrary N-verse
window: chapters average ~390 words (largest, John 6, is 1,285), a
comfortable size for one call's structured JSON output. Chapter
boundaries are also natural discourse units -- a pronoun or ambiguous
term in verse 5 often depends on something established in verse 1 of
the same chapter, so keeping a whole chapter in one call's context
avoids splitting that dependency across separate calls the way an
arbitrary verse-count window could. 1,090 total chapters -> 1,090 base
API calls (before any oversized-chapter splitting, see below).

Why lexicon-grounded: each word's prompt includes the ALREADY
TRANSLATED Indonesian lexicon gloss (from lexicon_gloss_translation,
itself reviewed/edited by a human per the CSV workflow) as the
starting point, and the model's job is to pick/adapt the right sense
for this verse's context, not invent a translation independently. This
keeps the same underlying word terminologically consistent across its
424,654 occurrences instead of drifting per-verse.

Safety valve for oversized chapters: if a chapter's word count exceeds
--max-words-per-batch (default 700, conservative headroom under
John 6's 1,285-word maximum), it's split into multiple calls at verse
boundaries (never splitting a verse's words across two calls).

Resumable: skips words that already have an ai_gloss row. Writes
results to a JSONL file per chapter processed (not accumulated in
memory for the whole Bible), so an interrupted run only needs to
resume from wherever it stopped, and partial progress is never lost.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 scripts/generate_ai_glosses.py \
    --db output/interlinear.sqlite \
    --out output/staging/ai_glosses_id.jsonl \
    --language id \
    --model claude-opus-4-5

  # Test against a single book first:
  python3 scripts/generate_ai_glosses.py --db output/interlinear.sqlite \
    --out output/staging/ai_glosses_id_test.jsonl --language id --book GEN --limit-chapters 1
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic --break-system-packages", file=sys.stderr)
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
- Maintain terminological consistency with standard Indonesian Bible translation conventions for well-known theological terms.
- If you're genuinely uncertain between two senses, put your primary choice in "gloss" and the alternative in "alt_gloss", with a brief "note" explaining the ambiguity. Leave alt_gloss and note as empty strings when there's no meaningful ambiguity -- don't manufacture uncertainty that isn't there.

Respond with ONLY a JSON array, no other text, no markdown code fences. Each element:
{{"word_id": <int>, "gloss": "<indonesian gloss>", "alt_gloss": "<optional alternative, or empty string>", "note": "<optional short note, or empty string>"}}

The array must contain exactly one object per input word, in the same order, with the same word_id values. Target language: {language}."""


# Same thematic groupings used by export_to_markdown.py's NotebookLM
# export -- reused here rather than redefined, so "the Torah" or "the
# Gospels" means the same set of books everywhere in this project.
BOOK_GROUPS = {
    "torah": ["GEN", "EXO", "LEV", "NUM", "DEU"],
    "historical": ["JOS", "JDG", "RUT", "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST"],
    "wisdom": ["JOB", "PSA", "PRO", "ECC", "SNG"],
    "major_prophets": ["ISA", "JER", "LAM", "EZK", "DAN"],
    "minor_prophets": ["HOS", "JOL", "AMO", "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL"],
    "gospels_acts": ["MAT", "MRK", "LUK", "JHN", "ACT"],
    "pauline_epistles": ["ROM", "1CO", "2CO", "GAL", "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM"],
    "general_epistles_revelation": ["HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV"],
}


def resolve_scope(book, group, testament):
    """Resolve the mutually-exclusive --book/--group/--testament flags
    into a single filter usable by get_chapters: either a list of book
    codes, a testament code, or None (meaning: everything). Exits with
    a clear error if more than one scope flag was given, or if --group
    named something unrecognized."""
    given = [name for name, val in [("--book", book), ("--group", group), ("--testament", testament)] if val]
    if len(given) > 1:
        print(f"ERROR: only one of --book/--group/--testament may be given at once, got: {', '.join(given)}", file=sys.stderr)
        sys.exit(1)

    if group:
        group_key = group.lower()
        if group_key not in BOOK_GROUPS:
            print(f"ERROR: unknown --group '{group}'. Valid groups: {', '.join(BOOK_GROUPS.keys())}", file=sys.stderr)
            sys.exit(1)
        return {"book_codes": BOOK_GROUPS[group_key]}

    if book:
        return {"book_codes": [book.upper()]}

    if testament:
        testament_code = testament.upper()
        if testament_code not in ("OT", "NT"):
            print(f"ERROR: --testament must be 'OT' or 'NT', got '{testament}'", file=sys.stderr)
            sys.exit(1)
        return {"testament": testament_code}

    return {}  # no scope given -- everything


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
    # Lexicon join must resolve to AT MOST ONE row per word. A plain
    # "lx.dstrong = w.strongs OR lx.estrong = w.strongs" join can match
    # multiple lexicon rows when a base Strong's number has several
    # disambiguated entries sharing that estrong (e.g. G2491 "John" has
    # four entries G2491G/H/I/J for different Johns -- same pattern as
    # the Herod proper-noun disambiguation case elsewhere in this
    # project), silently duplicating the word in the result set. Fixed
    # with ROW_NUMBER() to pick exactly one lexicon row per word: prefer
    # an exact dstrong match (unambiguous -- the word's own strongs
    # value IS a specific disambiguated entry), falling back to the
    # lowest-lexicon_id estrong match only when no dstrong match exists.
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
        SELECT w.word_id, v.verse, w.position, w.surface, w.translit, w.strongs,
               w.morph_code, w.gloss_source,
               rl.gloss as lexicon_gloss_en, lgt.gloss as lexicon_gloss_target
        FROM word w
        JOIN verse v ON w.verse_id = v.verse_id
        LEFT JOIN ranked_lexicon rl ON rl.w_word_id = w.word_id AND rl.rn = 1
        LEFT JOIN lexicon_gloss_translation lgt
            ON lgt.lexicon_id = rl.lexicon_id AND lgt.language = ?1
        WHERE v.book_id = ?2 AND v.chapter = ?3
        ORDER BY v.verse, w.position
        """,
        (language, book_id, chapter),
    )
    rows = cur.fetchall()
    return [r for r in rows if r[0] not in already_done_ids]


def split_into_batches(words, max_words_per_batch):
    """Split a chapter's words into batches at verse boundaries, never
    splitting a single verse's words across two batches."""
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


def build_prompt(words):
    lines = []
    for word_id, verse, position, surface, translit, strongs, morph, gloss_source, lex_en, lex_target in words:
        parts = [
            f'"word_id": {word_id}',
            f'"verse": {verse}',
            f'"surface": "{surface}"',
            f'"translit": "{translit}"',
            f'"strongs": "{strongs or ""}"',
            f'"morph": "{morph or ""}"',
            f'"lexicon_gloss": "{lex_target or lex_en or ""}"',
        ]
        if gloss_source:
            parts.append(f'"verse_context_hint": "{gloss_source}"')
        lines.append("{" + ", ".join(parts) + "}")
    return "Generate contextual glosses for these words:\n[\n" + ",\n".join(lines) + "\n]"


def call_model(client, model, words, language, max_retries=3):
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(language=language)
    prompt = build_prompt(words)
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except (json.JSONDecodeError, anthropic.APIError) as e:
            print(f"    attempt {attempt}/{max_retries} failed: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    return None


def load_already_done(out_path):
    done = set()
    if out_path.exists():
        with out_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    done.add(row["word_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return done


def cmd_sync(args):
    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    already_done = load_already_done(args.out)
    print(f"Already glossed: {len(already_done)} words (resuming)", file=sys.stderr)

    scope = resolve_scope(args.book, args.group, args.testament)
    chapters = get_chapters(cur, scope=scope, limit_chapters=args.limit_chapters)
    print(f"Chapters to process: {len(chapters)}", file=sys.stderr)

    client = anthropic.Anthropic()

    total_words_done = 0
    total_batches = 0

    with args.out.open("a", encoding="utf-8") as out_f:
        for chapter_idx, (book_id, code, name, chapter) in enumerate(chapters, 1):
            words = get_chapter_words(cur, book_id, chapter, args.language, already_done)
            if not words:
                continue

            chapter_label = f"{name} {chapter}"
            print(f"[{chapter_idx}/{len(chapters)}] translate {chapter_label} ....", end=" ", flush=True, file=sys.stderr)

            batches = split_into_batches(words, args.max_words_per_batch)
            chapter_ok = True

            for batch_num, batch in enumerate(batches, 1):
                total_batches += 1
                try:
                    results = call_model(client, args.model, batch, args.language)
                except Exception as e:
                    chapter_ok = False
                    print(f"FAILED", file=sys.stderr)
                    print(f"  {chapter_label} batch {batch_num}/{len(batches)} failed after retries: {e}", file=sys.stderr)
                    print(f"  Re-run later to retry (already-done words are skipped automatically).", file=sys.stderr)
                    continue

                batch_ids = {w[0] for w in batch}
                result_ids = {r["word_id"] for r in results}
                missing = batch_ids - result_ids
                if missing:
                    print(f"\n  WARNING: {chapter_label} response missing {len(missing)} words: {sorted(missing)}", file=sys.stderr)

                now = datetime.now(timezone.utc).isoformat()
                for r in results:
                    if r["word_id"] not in batch_ids:
                        print(f"  WARNING: unexpected word_id {r['word_id']} in response, skipping", file=sys.stderr)
                        continue
                    out_f.write(json.dumps({
                        "word_id": r["word_id"],
                        "gloss": r["gloss"],
                        "alt_gloss": r.get("alt_gloss") or None,
                        "note": r.get("note") or None,
                        "language": args.language,
                        "model": args.model,
                        "prompt_version": args.prompt_version,
                        "generated_at": now,
                    }, ensure_ascii=False) + "\n")
                    total_words_done += 1
                out_f.flush()

            if chapter_ok:
                print("done", file=sys.stderr)

    print(f"\nFinished. {total_words_done} words glossed across {total_batches} API calls -> {args.out}", file=sys.stderr)
    conn.close()


def cmd_submit(args):
    """Batch API flow, step 1: build one request per chapter-batch
    (same units as the sync path's per-call batches -- a whole chapter,
    or a chapter split at verse boundaries if oversized) and submit
    them all as ONE Anthropic batch job. ~1,090-1,200 requests total,
    well under the 100,000-per-job limit."""
    import batch_api_helper as bah

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    already_done = load_already_done(args.out)
    print(f"Already glossed: {len(already_done)} words", file=sys.stderr)

    scope = resolve_scope(args.book, args.group, args.testament)
    chapters = get_chapters(cur, scope=scope, limit_chapters=args.limit_chapters)
    print(f"Chapters to consider: {len(chapters)}", file=sys.stderr)

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(language=args.language)

    requests = []
    membership = {}  # custom_id -> list of word_ids, for matching results back on retrieve
    request_idx = 0

    for book_id, code, name, chapter in chapters:
        words = get_chapter_words(cur, book_id, chapter, args.language, already_done)
        if not words:
            continue
        batches = split_into_batches(words, args.max_words_per_batch)
        for batch in batches:
            custom_id = f"gloss-{request_idx:05d}"
            prompt = build_prompt(batch)
            requests.append((custom_id, system_prompt, prompt, args.model, 8192))
            membership[custom_id] = [w[0] for w in batch]  # w[0] is word_id
            request_idx += 1

    conn.close()

    if not requests:
        print("Nothing to do -- all words already glossed.", file=sys.stderr)
        return

    print(f"Built {len(requests)} requests across {len(chapters)} chapters", file=sys.stderr)
    if len(requests) > 100_000:
        print(f"ERROR: {len(requests)} requests exceeds Anthropic's 100,000-per-batch-job limit. "
              f"Use --book/--limit-chapters to split this into multiple submissions.", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    bah.submit_batch_job(client, requests, args.batch_job_file)

    membership_file = args.batch_job_file.with_suffix(".membership.json")
    membership_file.write_text(json.dumps(membership, ensure_ascii=False), encoding="utf-8")
    print(f"Saved request membership -> {membership_file}", file=sys.stderr)
    print(f"\nRun this again with 'retrieve' once the job is done:", file=sys.stderr)
    print(f"  python3 scripts/generate_ai_glosses.py retrieve --out {args.out} --language {args.language} --batch-job-file {args.batch_job_file}", file=sys.stderr)


def cmd_retrieve(args):
    """Batch API flow, step 2: poll until done, fetch results, append
    to the JSONL output (same format/location as the sync path, so
    load_ai_glosses.py works identically regardless of which mode
    produced the file)."""
    import batch_api_helper as bah

    if not args.batch_job_file.exists():
        print(f"ERROR: {args.batch_job_file} not found. Run 'submit' first.", file=sys.stderr)
        sys.exit(1)
    membership_file = args.batch_job_file.with_suffix(".membership.json")
    if not membership_file.exists():
        print(f"ERROR: {membership_file} not found (should have been created by 'submit').", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    job_info = json.loads(args.batch_job_file.read_text())
    membership = json.loads(membership_file.read_text())
    batch_id = job_info["batch_id"]

    client = anthropic.Anthropic(api_key=api_key)

    print(f"Polling batch job {batch_id}...", file=sys.stderr)
    batch = bah.poll_batch_job(client, batch_id, poll_interval_seconds=args.poll_interval, max_wait_seconds=args.max_wait)

    if batch.processing_status != "ended":
        print("Job not yet finished (see --max-wait). Run 'retrieve' again later to check.", file=sys.stderr)
        return

    print("Job ended, fetching results...", file=sys.stderr)
    raw_results = bah.fetch_batch_results(client, batch_id)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    total_words_done = 0

    with args.out.open("a", encoding="utf-8") as out_f:
        for custom_id, word_ids in membership.items():
            text = raw_results.get(custom_id)
            parsed = bah.parse_json_response(text)
            if parsed is None:
                print(f"  WARNING: request '{custom_id}' has no usable result, its {len(word_ids)} words remain unglossed (re-submit a follow-up batch for just these if needed).", file=sys.stderr)
                continue
            parsed_by_id = {item["word_id"]: item for item in parsed}
            for wid in word_ids:
                if wid not in parsed_by_id:
                    print(f"  WARNING: word_id {wid} missing from response for '{custom_id}'", file=sys.stderr)
                    continue
                r = parsed_by_id[wid]
                out_f.write(json.dumps({
                    "word_id": r["word_id"],
                    "gloss": r["gloss"],
                    "alt_gloss": r.get("alt_gloss") or None,
                    "note": r.get("note") or None,
                    "language": args.language,
                    "model": job_info.get("model", "unknown"),
                    "prompt_version": args.prompt_version,
                    "generated_at": now,
                }, ensure_ascii=False) + "\n")
                total_words_done += 1

    print(f"\nWrote {total_words_done} word glosses -> {args.out}", file=sys.stderr)
    print(f"Load into the database with: python3 scripts/load_ai_glosses.py --db <db> --jsonl {args.out}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    common_args = {
        "--language": dict(default="id"),
        "--prompt-version": dict(default="v1"),
        "--max-words-per-batch": dict(type=int, default=700,
            help="Split a chapter into multiple calls if it exceeds this many words"),
        "--book": dict(help="Only process one book (canonical 3-letter code, e.g. GEN)"),
        "--group": dict(help=f"Only process one thematic group of books. Valid: {', '.join(BOOK_GROUPS.keys())}"),
        "--testament": dict(help="Only process one testament: OT or NT"),
        "--limit-chapters": dict(type=int, help="Only process this many chapters (for testing)"),
    }

    sync_p = sub.add_parser("sync", help="Synchronous calls (original behavior)")
    sync_p.add_argument("--db", required=True, type=Path)
    sync_p.add_argument("--out", required=True, type=Path)
    sync_p.add_argument("--model", default="claude-opus-4-5")
    for flag, kwargs in common_args.items():
        sync_p.add_argument(flag, **kwargs)
    sync_p.set_defaults(func=cmd_sync)

    submit_p = sub.add_parser("submit", help="Submit a batch job via Anthropic's Batch API (50%% cheaper, async)")
    submit_p.add_argument("--db", required=True, type=Path)
    submit_p.add_argument("--out", required=True, type=Path, help="Where results will eventually be written by 'retrieve' (same JSONL format as sync)")
    submit_p.add_argument("--model", default="claude-opus-4-5")
    submit_p.add_argument("--batch-job-file", type=Path, default=Path("output/staging/gloss_batch_job.json"))
    for flag, kwargs in common_args.items():
        submit_p.add_argument(flag, **kwargs)
    submit_p.set_defaults(func=cmd_submit)

    retrieve_p = sub.add_parser("retrieve", help="Poll and retrieve results from a previously submitted batch job")
    retrieve_p.add_argument("--out", required=True, type=Path)
    retrieve_p.add_argument("--language", default="id")
    retrieve_p.add_argument("--prompt-version", default="v1")
    retrieve_p.add_argument("--batch-job-file", type=Path, default=Path("output/staging/gloss_batch_job.json"))
    retrieve_p.add_argument("--poll-interval", type=int, default=30)
    retrieve_p.add_argument("--max-wait", type=int, default=None)
    retrieve_p.set_defaults(func=cmd_retrieve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
