#!/usr/bin/env python3
"""
Load AI-generated contextual glosses (from generate_ai_glosses.py) from
JSONL into the ai_gloss table.

Separate from generate_ai_glosses.py deliberately, same reasoning as
the lexicon translation scripts: lets the JSONL be spot-checked before
touching the database, and re-running this loader is safe/idempotent
(upserts on word_id, which is UNIQUE in the ai_gloss schema).

Usage:
  load_ai_glosses.py --db output/interlinear.sqlite --jsonl output/staging/ai_glosses_id.jsonl
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

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    cur.execute("SELECT word_id FROM word")
    valid_ids = {r[0] for r in cur.fetchall()}

    loaded = 0
    skipped_invalid = []

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

            word_id = row.get("word_id")
            if word_id not in valid_ids:
                skipped_invalid.append(word_id)
                continue

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
                (
                    word_id, row["gloss"], alt_glosses, row.get("note"),
                    row["prompt_version"], row["model"], row["generated_at"],
                ),
            )
            loaded += 1

    conn.commit()

    if skipped_invalid:
        print(f"WARN: skipped {len(skipped_invalid)} rows with unknown word_id: {skipped_invalid[:20]}{'...' if len(skipped_invalid) > 20 else ''}", file=sys.stderr)

    cur.execute("SELECT COUNT(*) FROM ai_gloss")
    total = cur.fetchone()[0]
    print(f"Loaded/updated {loaded} rows this run. Total ai_gloss rows in DB: {total}", file=sys.stderr)

    cur.execute("SELECT COUNT(*) FROM word")
    total_words = cur.fetchone()[0]
    print(f"Coverage: {total}/{total_words} words have an AI gloss ({100*total/total_words:.1f}%)", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()