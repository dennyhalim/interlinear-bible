#!/usr/bin/env python3
"""
Generate a reset.sql that drops every table in schema.sql, in reverse
dependency order (children before parents, so foreign keys don't block
the drops). Used before a fresh D1 deploy so re-running the deploy
script is idempotent instead of failing on "table already exists".

Usage:
  generate_d1_reset.py --schema schema.sql --out output/d1_export/reset.sql
"""
import argparse
import re
import sys
from pathlib import Path

# Same order as export_to_d1.py's TABLE_ORDER, reversed for safe dropping
# (children before the parents they reference).
DROP_ORDER = [
    "ai_gloss",
    "versification_note",
    "word_proper_noun",
    "proper_noun_occurrence",
    "proper_noun_variant",
    "proper_noun",
    "morphology_code",
    "lexicon_entry",
    "word_morph_part",
    "word",
    "verse",
    "book",
    "metadata",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    schema_text = args.schema.read_text(encoding="utf-8")
    declared_tables = set(re.findall(r"CREATE TABLE (\w+)", schema_text))

    ordered = [t for t in DROP_ORDER if t in declared_tables]
    leftover = declared_tables - set(ordered)
    if leftover:
        print(f"WARN: tables in schema.sql not in DROP_ORDER, appending: {leftover}", file=sys.stderr)
        ordered += sorted(leftover)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("PRAGMA defer_foreign_keys = true;\n")
        for t in ordered:
            f.write(f"DROP TABLE IF EXISTS {t};\n")

    print(f"Wrote reset script for {len(ordered)} tables -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()