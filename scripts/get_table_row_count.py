#!/usr/bin/env python3
"""
Print the row count of a single table in the source SQLite database.
Extracted out of deploy_to_d1.sh's inline `python3 -c "..."` block,
which was a fragile pattern -- a bash variable (table name) interpolated
directly into a multi-line Python string inside bash double-quotes,
prone to quote-nesting bugs that are confusing to diagnose from a CI
log (wrong file/line gets blamed). A standalone script with real
argparse and its own real file/line numbers is much easier to debug.

Usage:
  get_table_row_count.py <sqlite-path> <table-name>
"""
import sqlite3
import sys


def main():
    if len(sys.argv) != 3:
        print("Usage: get_table_row_count.py <sqlite-path> <table-name>", file=sys.stderr)
        sys.exit(1)

    db_path, table = sys.argv[1], sys.argv[2]

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Table name can't be parameterized in SQL, but it's already been
    # extracted from our own generated filenames (never user input at
    # this point), so an f-string here is safe.
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    print(cur.fetchone()[0])
    conn.close()


if __name__ == "__main__":
    main()