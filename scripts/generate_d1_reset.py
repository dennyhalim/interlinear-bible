#!/usr/bin/env python3
"""
Generate a reset.sql that drops every table in schema.sql, in reverse dependency order (children before the
parents they reference, so foreign keys don't block the drops). Used before a fresh D1 deploy so re-running the
deploy script is idempotent instead of failing on "table already exists".

The order comes from export_to_d1.TABLE_ORDER (reversed), the single source of truth for table order, so a new
table only has to be added in one place. Tables in schema.sql that are missing from TABLE_ORDER are appended with
a warning.

Usage:
  generate_d1_reset.py --schema schema.sql --out output/d1_export/reset.sql
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from export_to_d1 import TABLE_ORDER

DROP_ORDER = list(reversed(TABLE_ORDER))


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
        print(f"WARN: tables in schema.sql not in TABLE_ORDER, appending: {sorted(leftover)}", file=sys.stderr)
        ordered += sorted(leftover)
    stale = [t for t in DROP_ORDER if t not in declared_tables]
    if stale:
        print(f"NOTE: tables in TABLE_ORDER but not declared in {args.schema.name} (not dropped): {stale}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("PRAGMA defer_foreign_keys = true;\n")
        for t in ordered:
            f.write(f"DROP TABLE IF EXISTS {t};\n")

    print(f"Wrote reset script for {len(ordered)} tables -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
