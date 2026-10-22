#!/usr/bin/env python3
"""
Load AI-generated contextual glosses (from generate_ai_glosses.py or
import_batch_results.py) from JSONL into the ai_gloss table.

Records that carry book/chapter/verse/position(/surface) are matched to the
CURRENT database by position, so a file survives a rebuild that renumbers
word_ids; a record whose surface text no longer matches is skipped, never
guessed. Older records without a position fall back to their word_id.

Separate from the generator deliberately: the JSONL can be spot-checked
before touching the database, and re-running is safe/idempotent (upsert on
word_id, UNIQUE in ai_gloss). Targeted re-gloss files load the same way and
overwrite just their words.

Usage:
  load_ai_glosses.py --db output/interlinear.sqlite --jsonl output/staging/glosses/GEN.jsonl
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--jsonl", required=True, type=Path)
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"ERROR: {args.jsonl} not found", file=sys.stderr)
        sys.exit(1)
    if not args.db.is_file() or args.db.stat().st_size == 0:
        print(f"ERROR: database missing or empty: {args.db.resolve()}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    cur.execute(
        "SELECT w.word_id, b.code, v.chapter, v.verse, w.position, w.surface FROM word w "
        "JOIN verse v ON v.verse_id = w.verse_id JOIN book b ON b.book_id = v.book_id"
    )
    ref_map, id_to_key = {}, {}
    for wid, book, chapter, verse, position, surface in cur.fetchall():
        ref_map[(book, chapter, verse, position)] = (wid, surface)
        id_to_key[wid] = (book, chapter, verse)

    by_ref = by_id = unknown = text_differs = 0
    chapters, verses = set(), set()

    with args.jsonl.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARN: line {line_num} is not valid JSON, skipping: {e}", file=sys.stderr)
                continue

            if row.get("book") is not None:
                hit = ref_map.get((row["book"], row.get("chapter"), row.get("verse"), row.get("position")))
                if hit is None:
                    unknown += 1
                    continue
                if row.get("surface") not in (None, hit[1]):
                    text_differs += 1
                    continue
                word_id = hit[0]
                by_ref += 1
            else:
                word_id = row.get("word_id")
                if word_id not in id_to_key:
                    unknown += 1
                    continue
                by_id += 1

            alt_glosses = json.dumps([row["alt_gloss"]], ensure_ascii=False) if row.get("alt_gloss") else None
            cur.execute(
                """
                INSERT INTO ai_gloss
                    (word_id, gloss, alt_glosses, note, prompt_version, model, generated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(word_id) DO UPDATE SET
                    gloss = excluded.gloss,
                    alt_glosses = excluded.alt_glosses,
                    note = excluded.note,
                    prompt_version = excluded.prompt_version,
                    model = excluded.model,
                    generated_at = excluded.generated_at
                """,
                (word_id, row["gloss"], alt_glosses, row.get("note"),
                 row["prompt_version"], row["model"], row["generated_at"]),
            )
            book, chapter, verse = id_to_key[word_id]
            chapters.add((book, chapter))
            verses.add((book, chapter, verse))

    conn.commit()

    loaded = by_ref + by_id
    print(f"Loaded/updated {loaded} rows this run ({by_ref} matched by position, {by_id} by word_id) "
          f"in {len(chapters)} chapters, {len(verses)} verses.", file=sys.stderr)
    if unknown:
        print(f"WARN: skipped {unknown} rows whose word is not in this database", file=sys.stderr)
    if text_differs:
        print(f"WARN: skipped {text_differs} rows whose surface text differs from the database", file=sys.stderr)

    cur.execute("SELECT COUNT(*) FROM ai_gloss")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM word")
    total_words = cur.fetchone()[0]
    print(f"Total ai_gloss rows in DB: {total}", file=sys.stderr)
    print(f"Coverage: {total}/{total_words} words have an AI gloss ({100 * total / total_words:.1f}%)", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
