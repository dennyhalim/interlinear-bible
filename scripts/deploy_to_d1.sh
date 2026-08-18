#!/usr/bin/env bash
# Deploy the interlinear database to a Cloudflare D1 database.
#
# Usage:
#   ./scripts/deploy_to_d1.sh <d1-database-name> [--fresh] [--tables t1,t2,...]
#
#   --fresh          Drop and recreate ALL tables first (via reset.sql).
#                     Only needed for the very first deploy, or after a
#                     schema change. NOT meant to run on every deploy --
#                     see the row-budget note below.
#   --tables t1,t2   Only export/apply these specific tables (comma-
#                     separated, matching the numbered file's table
#                     name, e.g. "lexicon_gloss_translation,ai_gloss").
#                     Default (no --tables): all tables.
#
# WHY THIS MATTERS -- D1's free tier is 100,000 ROWS WRITTEN PER DAY
# (resets 00:00 UTC), not per deploy. This project's *static* tables
# (book, verse, word, word_morph_part, lexicon_entry, morphology_code,
# proper_noun*, versification_mapping) total over 1.1 million rows --
# re-deploying all of them in one run would need ~12 days spread out
# even done perfectly, just to write data that essentially never
# changes once the source parsing pipeline has run. Two tables DO
# change incrementally as work progresses (lexicon_gloss_translation,
# ai_gloss) -- those are the ones worth redeploying regularly; the rest
# should be deployed ONCE (or rarely, e.g. after a schema/source
# update), not on every push.
#
# Practical usage pattern:
#   First deploy (all static + dynamic tables, uses most of a day's
#   budget -- expect this to take multiple days if done sequentially,
#   or split further with --tables if a single day's 100K limit is hit
#   mid-run):
#     ./scripts/deploy_to_d1.sh mydb --fresh
#
#   Routine incremental deploys after that (translation/gloss work in
#   progress, small enough to comfortably fit one day's budget):
#     ./scripts/deploy_to_d1.sh mydb --tables lexicon_gloss_translation,ai_gloss
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
#   2. If --fresh: applies reset.sql to drop tables (only the selected
#      ones if --tables was also given, otherwise all of them).
#   3. Applies each selected table's .sql file, in dependency order.
#   4. Verifies row counts in D1 match the source .sqlite file exactly
#      for each selected table, and fails loudly on any mismatch
#      rather than silently reporting success.

set -euo pipefail

DB_NAME="${1:-}"
shift || true

FRESH=""
TABLES_FILTER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --fresh)
      FRESH="--fresh"
      shift
      ;;
    --tables)
      TABLES_FILTER="${2:-}"
      shift 2
      ;;
    *)
      echo "WARN: unrecognized argument '$1', ignoring" >&2
      shift
      ;;
  esac
done

if [ -z "$DB_NAME" ]; then
  echo "ERROR: no database name provided." >&2
  echo "" >&2
  echo "Usage: deploy_to_d1.sh <d1-database-name> [--fresh] [--tables t1,t2,...]" >&2
  echo "" >&2
  echo "If running via GitHub Actions, this usually means the" >&2
  echo "D1_DATABASE_NAME repository variable is unset or empty." >&2
  echo "Set it at: repo Settings -> Secrets and variables -> Actions" >&2
  echo "-> Variables tab (not Secrets) -> New repository variable" >&2
  echo "  Name:  D1_DATABASE_NAME" >&2
  echo "  Value: the name you gave 'wrangler d1 create <n>'" >&2
  exit 1
fi

SRC_DB="output/interlinear.sqlite"
EXPORT_DIR="output/d1_export"
SCHEMA="schema.sql"

echo "== Debug: environment and file identity ==" >&2
echo "  pwd: $(pwd)" >&2
echo "  bash version: $BASH_VERSION" >&2
echo "  python3: $(command -v python3 2>&1) ($(python3 --version 2>&1))" >&2
echo "  requested tables filter: ${TABLES_FILTER:-<all tables>}" >&2
echo "  fresh (drop first): ${FRESH:-no}" >&2
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
if ! python3 scripts/export_to_d1.py --db "$SRC_DB" --split-dir "$EXPORT_DIR"; then
  ec=$?
  echo "" >&2
  echo "== Step 1 FAILED (exit code $ec). Dumping scripts/export_to_d1.py for inspection: ==" >&2
  cat -n scripts/export_to_d1.py >&2
  echo "== End of file dump ==" >&2
  exit "$ec"
fi

# Build the list of table files to actually operate on, honoring
# --tables if given.
shopt -s nullglob
all_files=("$EXPORT_DIR"/[0-9][0-9]_*.sql)
if [ ${#all_files[@]} -eq 0 ]; then
  echo "ERROR: no numbered table files found in $EXPORT_DIR" >&2
  exit 1
fi

files=()
if [ -n "$TABLES_FILTER" ]; then
  IFS=',' read -ra WANTED <<< "$TABLES_FILTER"
  for f in "${all_files[@]}"; do
    table=$(basename "$f" .sql | sed -E 's/^[0-9]+_//')
    for w in "${WANTED[@]}"; do
      if [ "$table" = "$w" ]; then
        files+=("$f")
      fi
    done
  done
  if [ ${#files[@]} -eq 0 ]; then
    echo "ERROR: none of the requested tables ($TABLES_FILTER) matched any exported file." >&2
    echo "Available tables: $(for f in "${all_files[@]}"; do basename "$f" .sql | sed -E 's/^[0-9]+_//'; done | tr '\n' ' ')" >&2
    exit 1
  fi
else
  files=("${all_files[@]}")
fi

echo "  selected ${#files[@]} of ${#all_files[@]} table files: ${files[*]}" >&2

# Rough row-count estimate for the selected tables, so a mistake (e.g.
# forgetting --tables and about to push 1.1M+ rows) is visible before
# it burns most of a day's free-tier budget or fails partway through.
echo "" >&2
echo "== Estimated row counts for selected tables (source .sqlite) ==" >&2
total_estimate=0
for f in "${files[@]}"; do
  table=$(basename "$f" .sql | sed -E 's/^[0-9]+_//')
  cnt=$(python3 scripts/get_table_row_count.py "$SRC_DB" "$table" 2>/dev/null || echo "?")
  echo "  $table: $cnt" >&2
  if [ "$cnt" != "?" ]; then
    total_estimate=$((total_estimate + cnt))
  fi
done
echo "  TOTAL estimated rows to write: $total_estimate" >&2
if [ "$total_estimate" -gt 90000 ]; then
  echo "" >&2
  echo "  WARNING: this exceeds (or is close to) D1's free-tier 100,000" >&2
  echo "  rows-written-per-day limit in a single run. Consider using" >&2
  echo "  --tables to split this across multiple days, or confirm you" >&2
  echo "  intend to run this on a paid plan." >&2
fi
echo "" >&2

if [ "$FRESH" = "--fresh" ]; then
  echo "== Step 2: generating and applying reset.sql (dropping selected tables) =="
  if ! python3 scripts/generate_d1_reset.py --schema "$SCHEMA" --out "$EXPORT_DIR/reset.sql"; then
    ec=$?
    echo "== Step 2 (generate reset.sql) FAILED (exit code $ec) ==" >&2
    exit "$ec"
  fi

  if [ -n "$TABLES_FILTER" ]; then
    # Filter reset.sql down to only the selected tables' DROP statements,
    # so --fresh --tables doesn't drop tables we're not about to reload.
    filtered_reset=$(mktemp)
    echo "PRAGMA defer_foreign_keys = true;" > "$filtered_reset"
    for f in "${files[@]}"; do
      table=$(basename "$f" .sql | sed -E 's/^[0-9]+_//')
      echo "DROP TABLE IF EXISTS $table;" >> "$filtered_reset"
    done
    reset_file="$filtered_reset"
  else
    reset_file="$EXPORT_DIR/reset.sql"
  fi

  if ! wrangler d1 execute "$DB_NAME" --remote --file="$reset_file"; then
    ec=$?
    echo "== Step 2 (apply reset) FAILED (exit code $ec) ==" >&2
    exit "$ec"
  fi
else
  echo "== Step 2: skipped (no --fresh flag; assuming target tables don't already exist) =="
fi

echo "== Step 3: applying selected table files in order =="
for f in "${files[@]}"; do
  echo "-> $(basename "$f")"
  if ! wrangler d1 execute "$DB_NAME" --remote --file="$f"; then
    ec=$?
    echo "== Step 3 FAILED applying $f (exit code $ec) ==" >&2
    exit "$ec"
  fi
done

echo "== Step 4: verifying row counts (D1 vs source) for selected tables =="
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

echo "== Done. All selected tables verified. =="