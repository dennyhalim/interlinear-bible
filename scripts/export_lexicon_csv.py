#!/usr/bin/env python3
"""
Export lexicon_entry rows to a CSV suitable for translation (by the
API or by hand) and manual review/editing in a spreadsheet, before
being loaded back into the database.

Columns:
  lexicon_id   - primary key, used to join back on load (don't edit)
  language     - "greek" or "hebrew" (don't edit)
  dstrong      - Strong's number, e.g. G3056, H7225G (don't edit)
  word_form    - original-language word, for translator/reviewer context
  translit     - transliteration, for translator/reviewer context
  gloss_en     - the existing English gloss, for translator/reviewer context
  gloss_id     - target column: Indonesian gloss. Empty in a fresh
                 export; filled in either by translate_lexicon.py
                 (writing back to this same CSV) or by hand in a
                 spreadsheet, or both (spreadsheet edits after an AI
                 first pass).

The "_id" in gloss_id is the ISO 639-1 language code suffix, not a
row identifier -- if exporting for a different target language, pass
--language and the target column is named accordingly (e.g. gloss_es
for Spanish), so multiple language exports can coexist as separate
files without column-name collisions.

Skips entries with an empty English gloss (there's exactly one in the
current dataset) since there's nothing meaningful to translate.

Usage:
  export_lexicon_csv.py --db output/interlinear.sqlite --out output/staging/lexicon_id.csv --language id
"""
import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id", help="ISO 639-1 target language code (used only to name the gloss column)")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()
    # Sorted by actual usage frequency in the text (most-used first), not
    # by lexicon_id, so manual review time goes to words that affect the
    # most verses first. ~27% of lexicon entries never actually occur in
    # this TR/TAHOT text at all (likely covering word-senses/forms in the
    # source lexicons that aren't attested here, or proper-noun variant
    # entries) -- those sort to the bottom, correctly deprioritized.
    cur.execute(
        """
        SELECT le.lexicon_id, le.language, le.dstrong, le.word_form, le.translit, le.gloss,
               COUNT(w.word_id) as usage_count
        FROM lexicon_entry le
        LEFT JOIN word w ON w.strongs = le.dstrong OR w.strongs = le.estrong
        WHERE le.gloss != '' AND le.gloss IS NOT NULL
        GROUP BY le.lexicon_id
        ORDER BY usage_count DESC, le.lexicon_id ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    gloss_col = f"gloss_{args.language}"
    fieldnames = ["lexicon_id", "usage_count", "language", "dstrong", "word_form", "translit", "gloss_en", gloss_col]

    with args.out.open("w", encoding="utf-8-sig", newline="") as f:
        # utf-8-sig (BOM) so Excel opens Hebrew/Greek/Indonesian text
        # correctly rather than mis-detecting encoding -- a plain utf-8
        # CSV frequently gets misread by Excel on Windows otherwise.
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for lexicon_id, language, dstrong, word_form, translit, gloss, usage_count in rows:
            writer.writerow({
                "lexicon_id": lexicon_id,
                "usage_count": usage_count,
                "language": language,
                "dstrong": dstrong,
                "word_form": word_form,
                "translit": translit,
                "gloss_en": gloss,
                gloss_col: "",
            })

    print(f"Wrote {len(rows)} rows -> {args.out} (sorted by usage frequency, most-used first)", file=sys.stderr)
    print(f"Fill in the '{gloss_col}' column (by hand or via translate_lexicon.py --csv), then load with load_lexicon_translations.py --csv", file=sys.stderr)


if __name__ == "__main__":
    main()