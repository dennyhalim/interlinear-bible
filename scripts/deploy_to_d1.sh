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

echo "== Debug: environment and file identity ==" >&2
echo "  pwd: $(pwd)" >&2
echo "  bash version: $BASH_VERSION" >&2
echo "  python3: $(command -v python3 2>&1) ($(python3 --version 2>&1))" >&2
for f in scripts/export_to_d1.py scripts/generate_d1_reset.py scripts/deploy_to_d1.sh "$SRC_DB" "$SCHEMA"; do
  if [ -f "$f" ]; then
    lines=$(wc -l < "$f" | tr -d ' ')
    bytes=$(wc -c < "$f" | tr -d ' ')
    checksum=$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1 || md5sum "$f" 2>/dev/null | cut -d' ' -f1 || echo "no-checksum-tool")
    echo "  $f: $lines lines, $bytes bytes, sha256=$checksum" >&2
  else
    echo "  $f: MISSING" >&2
  fi
done
echo "== End debug ==" >&2
echo "" >&2

if [ ! -f "$SRC_DB" ]; then
  echo "ERROR: $SRC_DB not found. Run the build pipeline first (parse_*.py, build_db.py, resolve_proper_nouns.py)." >&2
  exit 1
fi

command -v wrangler >/dev/null 2>&1 || {
  echo "ERROR: wrangler CLI not found. Install with: npm install -g wrangler" >&2
  exit 1
}

echo "== Step 1: exporting $SRC_DB to $EXPORT_DIR =="
echo "  About to run: python3 scripts/export_to_d1.py --db \"$SRC_DB\" --split-dir \"$EXPORT_DIR\"" >&2
echo "  scripts/export_to_d1.py first line: $(head -1 scripts/export_to_d1.py)" >&2
echo "  scripts/export_to_d1.py line count: $(wc -l < scripts/export_to_d1.py)" >&2
if ! python3 scripts/export_to_d1.py --db "$SRC_DB" --split-dir "$EXPORT_DIR"; then
  ec=$?
  echo "" >&2
  echo "== Step 1 FAILED (exit code $ec). Dumping scripts/export_to_d1.py for inspection: ==" >&2
  cat -n scripts/export_to_d1.py >&2
  echo "== End of file dump ==" >&2
  exit "$ec"
fi

if [ "$FRESH" = "--fresh" ]; then
  echo "== Step 2: generating and applying reset.sql (dropping existing tables) =="
  echo "  scripts/generate_d1_reset.py line count: $(wc -l < scripts/generate_d1_reset.py)" >&2
  if ! python3 scripts/generate_d1_reset.py --schema "$SCHEMA" --out "$EXPORT_DIR/reset.sql"; then
    ec=$?
    echo "== Step 2 (generate reset.sql) FAILED (exit code $ec) ==" >&2
    exit "$ec"
  fi
  if ! wrangler d1 execute "$DB_NAME" --remote --file="$EXPORT_DIR/reset.sql"; then
    ec=$?
    echo "== Step 2 (apply reset.sql via wrangler) FAILED (exit code $ec) ==" >&2
    exit "$ec"
  fi
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
echo "  found ${#files[@]} table files: ${files[*]}" >&2

for f in "${files[@]}"; do
  echo "-> $(basename "$f")"
  if ! wrangler d1 execute "$DB_NAME" --remote --file="$f"; then
    ec=$?
    echo "== Step 3 FAILED applying $f (exit code $ec) ==" >&2
    exit "$ec"
  fi
done

echo "== Step 4: verifying row counts (D1 vs source) =="
FAILED=0
for f in "${files[@]}"; do
  table=$(basename "$f" .sql | sed -E 's/^[0-9]+_//')
  echo "  checking table: $table" >&2

  src_count=$(python3 scripts/get_table_row_count.py "$SRC_DB" "$table")

  d1_result=$(wrangler d1 execute "$DB_NAME" --remote --command "SELECT COUNT(*) as c FROM $table;" --json)
  d1_count=$(echo "$d1_result" | python3 scripts/parse_d1_count.py)

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