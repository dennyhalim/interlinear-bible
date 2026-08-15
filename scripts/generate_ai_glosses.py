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


def get_chapters(cur, book_code=None, limit_chapters=None):
    query = """
        SELECT DISTINCT v.book_id, b.code, b.name, v.chapter
        FROM verse v JOIN book b ON v.book_id = b.book_id
    """
    params = []
    if book_code:
        query += " WHERE b.code = ?"
        params.append(book_code)
    query += " ORDER BY b.ordinal, v.chapter"
    cur.execute(query, params)
    chapters = cur.fetchall()
    if limit_chapters:
        chapters = chapters[:limit_chapters]
    return chapters


def get_chapter_words(cur, book_id, chapter, language, already_done_ids):
    cur.execute(
        """
        SELECT w.word_id, v.verse, w.position, w.surface, w.translit, w.strongs,
               w.morph_code, w.gloss_source,
               lx.gloss as lexicon_gloss_en, lgt.gloss as lexicon_gloss_target
        FROM word w
        JOIN verse v ON w.verse_id = v.verse_id
        LEFT JOIN lexicon_entry lx ON lx.dstrong = w.strongs OR lx.estrong = w.strongs
        LEFT JOIN lexicon_gloss_translation lgt
            ON lgt.lexicon_id = lx.lexicon_id AND lgt.language = ?
        WHERE v.book_id = ? AND v.chapter = ?
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--model", default="claude-opus-4-5")
    ap.add_argument("--prompt-version", default="v1")
    ap.add_argument("--max-words-per-batch", type=int, default=700,
                     help="Split a chapter into multiple calls if it exceeds this many words (conservative headroom under the largest real chapter, John 6 at 1,285 words)")
    ap.add_argument("--book", help="Only process one book (canonical 3-letter code, e.g. GEN)")
    ap.add_argument("--limit-chapters", type=int, help="Only process this many chapters (for testing)")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    already_done = load_already_done(args.out)
    print(f"Already glossed: {len(already_done)} words (resuming)", file=sys.stderr)

    chapters = get_chapters(cur, book_code=args.book, limit_chapters=args.limit_chapters)
    print(f"Chapters to process: {len(chapters)}", file=sys.stderr)

    client = anthropic.Anthropic()

    total_words_done = 0
    total_batches = 0

    with args.out.open("a", encoding="utf-8") as out_f:
        for chapter_idx, (book_id, code, name, chapter) in enumerate(chapters, 1):
            words = get_chapter_words(cur, book_id, chapter, args.language, already_done)
            if not words:
                continue  # entire chapter already done, or no words (shouldn't happen)

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


if __name__ == "__main__":
    main()