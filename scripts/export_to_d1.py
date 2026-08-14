#!/usr/bin/env python3
"""
Export interlinear.sqlite into a D1-compatible .sql file for
`wrangler d1 execute --remote --file=...`.

Why not just `sqlite3 interlinear.sqlite .dump`:
  - D1 rejects BEGIN TRANSACTION / COMMIT wrapping (D1 docs: "cannot
    start a transaction within a transaction").
  - D1 has a 100,000 byte SQL statement length limit. A plain .dump
    emits one INSERT per row (fine) but some tools/dumps batch multi-
    row VALUES (...), (...), (...) into one statement -- and even
    per-row INSERTs from a raw dump aren't guaranteed batched
    efficiently for the wrangler upload step. This script explicitly
    batches multiple rows into each INSERT, sized safely under the
    limit by actual byte length (not just a fixed row count), since
    fields like lexicon_entry.meaning vary from a few bytes to ~7KB.
  - D1 doesn't want a `_cf_KV` table definition if present (reserved).

Usage:
  export_to_d1.py --db output/interlinear.sqlite --out output/d1_import.sql
  # then:
  wrangler d1 execute <db-name> --remote --file=output/d1_import.sql

For very large tables this also supports --split-dir to write one file
per table (useful for staying under wrangler's 5GiB file limit and for
resumable imports -- if one table's import fails partway, only that
table's file needs re-running rather than the whole database).
"""
import argparse
import sqlite3
import sys
from pathlib import Path

# Conservative ceiling well under D1's 100,000 byte statement limit,
# leaving headroom for the "INSERT INTO t (...) VALUES " prefix and
# per-row punctuation overhead.
MAX_STATEMENT_BYTES = 80_000

# Table order matters for foreign keys: parents before children.
TABLE_ORDER = [
    "book",
    "verse",
    "word",
    "word_morph_part",
    "lexicon_entry",
    "morphology_code",
    "proper_noun",
    "proper_noun_variant",
    "proper_noun_occurrence",
    "word_proper_noun",
    "ai_gloss",
    "versification_note",
    "metadata",
]


def sql_quote(value):
    """Render a Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, bytes):
        return "X'" + value.hex() + "'"
    # string: escape single quotes by doubling
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def get_create_table_sql(cur, table):
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,))
    row = cur.fetchone()
    return row[0] if row else None


def get_create_index_sql(cur, table):
    cur.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def export_table(cur, table, out_f):
    create_sql = get_create_table_sql(cur, table)
    if create_sql is None:
        print(f"WARN: table '{table}' not found in source db, skipping", file=sys.stderr)
        return 0

    out_f.write(create_sql.rstrip(";") + ";\n")

    cur.execute(f"SELECT * FROM {table}")
    col_names = [d[0] for d in cur.description]
    col_list = ", ".join(f'"{c}"' for c in col_names)
    prefix = f'INSERT INTO "{table}" ({col_list}) VALUES '

    row_count = 0
    batch_rows = []
    batch_bytes = len(prefix.encode("utf-8"))

    def flush():
        nonlocal batch_rows, batch_bytes
        if batch_rows:
            out_f.write(prefix + ",\n".join(batch_rows) + ";\n")
            batch_rows = []
            batch_bytes = len(prefix.encode("utf-8"))

    for row in cur.fetchall():
        values_sql = "(" + ", ".join(sql_quote(v) for v in row) + ")"
        row_bytes = len(values_sql.encode("utf-8")) + 2  # +2 for ",\n" join overhead

        if batch_rows and batch_bytes + row_bytes > MAX_STATEMENT_BYTES:
            flush()

        batch_rows.append(values_sql)
        batch_bytes += row_bytes
        row_count += 1

    flush()

    for idx_sql in get_create_index_sql(cur, table):
        out_f.write(idx_sql.rstrip(";") + ";\n")

    return row_count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="Single combined output .sql file")
    ap.add_argument("--split-dir", type=Path, help="Write one .sql file per table into this directory instead")
    args = ap.parse_args()

    if not args.out and not args.split_dir:
        print("Specify --out (single file) or --split-dir (one file per table)", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    actual_tables = {r[0] for r in cur.fetchall()}
    ordered_tables = [t for t in TABLE_ORDER if t in actual_tables]
    leftover = actual_tables - set(ordered_tables)
    if leftover:
        print(f"WARN: tables present but not in TABLE_ORDER, appending at end: {leftover}", file=sys.stderr)
        ordered_tables += sorted(leftover)

    total_rows = 0

    if args.split_dir:
        args.split_dir.mkdir(parents=True, exist_ok=True)
        for i, table in enumerate(ordered_tables):
            out_path = args.split_dir / f"{i:02d}_{table}.sql"
            with out_path.open("w", encoding="utf-8") as f:
                n = export_table(cur, table, f)
            total_rows += n
            print(f"{table}: {n} rows -> {out_path}", file=sys.stderr)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as f:
            for table in ordered_tables:
                n = export_table(cur, table, f)
                total_rows += n
                print(f"{table}: {n} rows", file=sys.stderr)
        print(f"-> {args.out}", file=sys.stderr)

    print(f"TOTAL rows exported: {total_rows}", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()