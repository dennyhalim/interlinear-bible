#!/usr/bin/env python3
"""
Extract the row count from `wrangler d1 execute --json` output, read
from stdin. Expects the confirmed real shape:
  [{"results": [{"c": <count>}], "success": true, "meta": {...}}]

Extracted out of deploy_to_d1.sh's inline `python3 -c "..."` block for
the same reason as get_table_row_count.py -- avoids fragile quote
nesting inside bash, and gives real file/line numbers if it ever fails.

Usage:
  wrangler d1 execute ... --json | parse_d1_count.py
"""
import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"ERROR: could not parse wrangler output as JSON: {e}", file=sys.stderr)
        print("Raw stdin was:", file=sys.stderr)
        sys.exit(1)

    try:
        count = data[0]["results"][0]["c"]
    except (KeyError, IndexError, TypeError) as e:
        print(f"ERROR: unexpected JSON shape from wrangler: {e}", file=sys.stderr)
        print(f"Got: {json.dumps(data, indent=2)}", file=sys.stderr)
        sys.exit(1)

    print(count)


if __name__ == "__main__":
    main()