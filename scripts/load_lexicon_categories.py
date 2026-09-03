#!/usr/bin/env python3
"""
Load semantic domain categories (crizin/bible-db's strong_categories.jsonl,
e.g. "person_role>personal_name", "deity_spirit>divine_name") into
lexicon_category, joined against lexicon_entry.

Source format (verified against the real uploaded file, 14,197 rows):
  {"strong": "H430", "primary": "deity_spirit>divine_name",
   "categories": ["deity_spirit>divine_name", "deity_spirit>demon_idol",
                  "person_role>rank_status"]}

Key format mismatch this loader handles: the source file's "strong"
field is BARE (no zero-padding, no disambiguation letter -- "H430",
"G1"), while this project's lexicon_entry.estrong is zero-padded
("H0430") and .dstrong is further disambiguated ("H0430G"). This
loader normalizes the source key to our zero-padded estrong format
and joins on that, applying the category to EVERY lexicon_entry row
sharing that base Strong's number (verified: 1,057 of this project's
base Strong's numbers have multiple disambiguated entries, e.g. all
three "H0430G/H/I" Elohim senses correctly receive the same semantic
domain, since the domain doesn't depend on which specific disambiguated
sense a given occurrence resolves to).

Usage:
  python3 scripts/load_lexicon_categories.py \
    --db output/interlinear.sqlite \
    --jsonl strong_categories.jsonl
"""
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path


def normalize_to_estrong(bare_strong):
    """'H430' -> 'H0430', 'G1' -> 'G0001'. Matches this project's
    zero-padded estrong convention (verified: 4-digit padding, e.g.
    lexicon_entry rows show 'H0430', 'G0001')."""
    m = re.match(r"^([HG])(\d+)$", bare_strong.strip())
    if not m:
        return None
    letter, digits = m.groups()
    return f"{letter}{int(digits):04d}"


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

    # Build estrong-base -> [lexicon_id, ...] index. Our lexicon's own
    # estrong column is not perfectly uniform: most rows are a clean
    # zero-padded number ("H0430"), but 1,164 rows carry a trailing
    # lowercase disambiguation letter ("H0122a", "H0122b") -- a
    # different convention from dstrong's uppercase letters. Stripping
    # any trailing letter (upper or lower) from estrong before indexing
    # normalizes both patterns to the same base-number key, matching
    # how the source category data is keyed (base number only, no
    # letter at all).
    cur.execute("SELECT lexicon_id, estrong FROM lexicon_entry WHERE estrong IS NOT NULL")
    estrong_to_lexicon_ids = {}
    for lexicon_id, estrong in cur.fetchall():
        base = re.sub(r"[A-Za-z]+$", "", estrong)
        estrong_to_lexicon_ids.setdefault(base, []).append(lexicon_id)

    cur.execute("DELETE FROM lexicon_category")

    loaded = 0
    unmatched_strongs = []
    malformed_lines = 0

    with args.jsonl.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"WARN: line {line_num} is not valid JSON, skipping: {e}", file=sys.stderr)
                malformed_lines += 1
                continue

            bare_strong = row.get("strong", "")
            estrong = normalize_to_estrong(bare_strong)
            if estrong is None:
                print(f"WARN: line {line_num} has unparseable strong value '{bare_strong}', skipping", file=sys.stderr)
                malformed_lines += 1
                continue

            lexicon_ids = estrong_to_lexicon_ids.get(estrong)
            if not lexicon_ids:
                unmatched_strongs.append(bare_strong)
                continue

            primary = row.get("primary")
            categories = row.get("categories", [])

            for lexicon_id in lexicon_ids:
                for category in categories:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO lexicon_category (lexicon_id, category, is_primary)
                        VALUES (?, ?, ?)
                        """,
                        (lexicon_id, category, 1 if category == primary else 0),
                    )
                    loaded += 1

    conn.commit()

    if malformed_lines:
        print(f"WARN: skipped {malformed_lines} malformed lines", file=sys.stderr)
    if unmatched_strongs:
        print(f"WARN: {len(unmatched_strongs)} Strong's numbers in the source file have no "
              f"matching lexicon_entry in this database (likely entries for words/forms not "
              f"present in this project's TR/TAHOT text): {unmatched_strongs[:20]}"
              f"{'...' if len(unmatched_strongs) > 20 else ''}", file=sys.stderr)

    cur.execute("SELECT COUNT(*) FROM lexicon_category")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT lexicon_id) FROM lexicon_category")
    distinct_lexicon = cur.fetchone()[0]
    print(f"Loaded {loaded} category assignments this run.", file=sys.stderr)
    print(f"Total lexicon_category rows: {total}, covering {distinct_lexicon} distinct lexicon entries.", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
