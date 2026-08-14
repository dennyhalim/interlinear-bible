#!/usr/bin/env bash
# Deploy the interlinear database to a Cloudflare D1 database, safely
# re-runnable (drops and recreates all tables first).
#
# Usage:
#   ./scripts/deploy_to_d1.sh <d1-database-name> [--fresh]
#
#   --fresh   Also run reset.sql first, dropping all existing tables.
#             Use this for every deploy after the first, since our
#             exported CREATE TABLE statements don't use
#             IF NOT EXISTS (they're copied verbatim from schema.sql)
#             and will fail with "table already exists" otherwise.
#
# Prerequisites:
#   - wrangler CLI installed (npm install -g wrangler) and authenticated
#     (wrangler login), or CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
#     env vars set for non-interactive/CI use.
#   - The target D1 database already created once:
#       wrangler d1 create <d1-database-name>
#   - output/interlinear.sqlite already built (parse_*.py + build_db.py
#     + resolve_proper_nouns.py already run).
#
# What this script does, in order:
#   1. Regenerates output/d1_export/*.sql from the current
#      output/interlinear.sqlite (always fresh, never stale).
#   2. If --fresh: applies reset.sql to drop all existing tables.
#   3. Applies each table's .sql file in dependency order.
#   4. Verifies row counts in D1 match the source .sqlite file exactly,
#      table by table, and fails loudly if any table doesn't match
#      rather than silently reporting success.

set -euo pipefail

DB_NAME="${1:-}"
FRESH="${2:-}"

if [ -z "$DB_NAME" ]; then
  echo "ERROR: no database name provided." >&2
  echo "" >&2
  echo "Usage: deploy_to_d1.sh <d1-database-name> [--fresh]" >&2
  echo "" >&2
  echo "If running via GitHub Actions, this usually means the" >&2
  echo "D1_DATABASE_NAME repository variable is unset or empty." >&2
  echo "Set it at: repo Settings -> Secrets and variables -> Actions" >&2
  echo "-> Variables tab (not Secrets) -> New repository variable" >&2
  echo "  Name:  D1_DATABASE_NAME" >&2
  echo "  Value: the name you gave 'wrangler d1 create <name>'" >&2
  exit 1
fi

SRC_DB="output/interlinear.sqlite"
EXPORT_DIR="output/d1_export"
SCHEMA="schema.sql"

if [ ! -f "$SRC_DB" ]; then
  echo "ERROR: $SRC_DB not found. Run the build pipeline first (parse_*.py, build_db.py, resolve_proper_nouns.py)." >&2
  exit 1
fi

command -v wrangler >/dev/null 2>&1 || {
  echo "ERROR: wrangler CLI not found. Install with: npm install -g wrangler" >&2
  exit 1
}

echo "== Step 1: exporting $SRC_DB to $EXPORT_DIR =="
python3 scripts/export_to_d1.py --db "$SRC_DB" --split-dir "$EXPORT_DIR"

if [ "$FRESH" = "--fresh" ]; then
  echo "== Step 2: generating and applying reset.sql (dropping existing tables) =="
  python3 scripts/generate_d1_reset.py --schema "$SCHEMA" --out "$EXPORT_DIR/reset.sql"
  wrangler d1 execute "$DB_NAME" --remote --file="$EXPORT_DIR/reset.sql"
else
  echo "== Step 2: skipped (no --fresh flag; assuming target tables don't already exist) =="
fi

echo "== Step 3: applying table files in dependency order =="
shopt -s nullglob
files=("$EXPORT_DIR"/[0-9][0-9]_*.sql)
if [ ${#files[@]} -eq 0 ]; then
  echo "ERROR: no numbered table files found in $EXPORT_DIR" >&2
  exit 1
fi

for f in "${files[@]}"; do
  echo "-> $(basename "$f")"
  wrangler d1 execute "$DB_NAME" --remote --file="$f"
done

echo "== Step 4: verifying row counts (D1 vs source) =="
FAILED=0
for f in "${files[@]}"; do
  table=$(basename "$f" .sql | sed -E 's/^[0-9]+_//')

  src_count=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$SRC_DB')
cur = conn.cursor()
cur.execute('SELECT COUNT(*) FROM $table')
print(cur.fetchone()[0])
")

  d1_result=$(wrangler d1 execute "$DB_NAME" --remote --command "SELECT COUNT(*) as c FROM $table;" --json)
  d1_count=$(echo "$d1_result" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data[0]['results'][0]['c'])
")

  if [ "$src_count" = "$d1_count" ]; then
    echo "  $table: OK ($d1_count rows)"
  else
    echo "  $table: MISMATCH (source=$src_count, D1=$d1_count)" >&2
    FAILED=1
  fi
done

if [ "$FAILED" = "1" ]; then
  echo "== FAILED: one or more tables have row-count mismatches. See above. ==" >&2
  exit 1
fi

echo "== Done. All tables verified. =="