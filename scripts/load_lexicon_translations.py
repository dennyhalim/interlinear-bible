#!/usr/bin/env python3
"""
Load lexicon gloss translations from a CSV (produced by
export_lexicon_csv.py, filled by translate_lexicon.py, and optionally
hand-edited in a spreadsheet) into the lexicon_gloss_translation table.

Separate from translate_lexicon.py deliberately: lets the CSV be
opened and reviewed/edited in Excel/Sheets before anything touches the
database, and re-running this loader is safe/idempotent (upserts on
the lexicon_id+language unique constraint).

Rows with an empty gloss in the target column are skipped (not every
row may be filled in yet if translation/review is still in progress).

Usage:
  load_lexicon_translations.py --db output/interlinear.sqlite --csv output/staging/lexicon_id.csv --language id
"""
import argparse
import csv
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--language", default="id", help="ISO 639-1 target language code (must match the gloss_<lang> column name)")
    ap.add_argument("--model", default="unknown", help="Recorded in the model column; use 'manual' if the CSV is entirely hand-translated")
    ap.add_argument("--prompt-version", default="v1")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found", file=sys.stderr)
        sys.exit(1)

    gloss_col = f"gloss_{args.language}"

    with args.csv.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if gloss_col not in reader.fieldnames:
            print(f"ERROR: column '{gloss_col}' not found in {args.csv}. Found columns: {reader.fieldnames}", file=sys.stderr)
            sys.exit(1)
        rows = list(reader)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    # Validate lexicon_id values actually exist before inserting, so a
    # corrupted/hand-edited CSV (e.g. a row accidentally deleted/reordered
    # in a spreadsheet) fails loudly instead of silently inserting
    # orphaned rows.
    cur.execute("SELECT lexicon_id FROM lexicon_entry")
    valid_ids = {r[0] for r in cur.fetchall()}

    loaded = 0
    skipped_empty = 0
    skipped_invalid = []
    now = datetime.now(timezone.utc).isoformat()

    for row in rows:
        gloss = row.get(gloss_col, "").strip()
        if not gloss:
            skipped_empty += 1
            continue

        try:
            lexicon_id = int(row["lexicon_id"])
        except (KeyError, ValueError):
            skipped_invalid.append(row.get("lexicon_id"))
            continue

        if lexicon_id not in valid_ids:
            skipped_invalid.append(lexicon_id)
            continue

        cur.execute(
            """
            INSERT INTO lexicon_gloss_translation
                (lexicon_id, language, gloss, model, prompt_version, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(lexicon_id, language) DO UPDATE SET
                gloss = excluded.gloss,
                model = excluded.model,
                prompt_version = excluded.prompt_version,
                generated_at = excluded.generated_at
            """,
            (lexicon_id, args.language, gloss, args.model, args.prompt_version, now),
        )
        loaded += 1

    conn.commit()

    if skipped_invalid:
        print(f"WARN: skipped {len(skipped_invalid)} rows with unknown/invalid lexicon_id: {skipped_invalid[:20]}{'...' if len(skipped_invalid) > 20 else ''}", file=sys.stderr)
    if skipped_empty:
        print(f"Skipped {skipped_empty} rows with an empty '{gloss_col}' (not yet translated)", file=sys.stderr)

    cur.execute("SELECT COUNT(*) FROM lexicon_gloss_translation WHERE language = ?", (args.language,))
    total = cur.fetchone()[0]
    print(f"Loaded/updated {loaded} rows this run. Total '{args.language}' translations in DB: {total}", file=sys.stderr)

    cur.execute(
        """
        SELECT COUNT(*) FROM lexicon_entry le
        WHERE le.gloss != '' AND le.gloss IS NOT NULL
        AND NOT EXISTS (
            SELECT 1 FROM lexicon_gloss_translation lgt
            WHERE lgt.lexicon_id = le.lexicon_id AND lgt.language = ?
        )
        """,
        (args.language,),
    )
    missing = cur.fetchone()[0]
    print(f"Lexicon entries still missing a '{args.language}' translation: {missing}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()