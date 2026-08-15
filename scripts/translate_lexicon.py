#!/usr/bin/env python3
"""
Fill in the target-language gloss column of a lexicon CSV (produced by
export_lexicon_csv.py) using the Claude API, so the result can be
opened in a spreadsheet for manual review/editing before being loaded
into the database.

Workflow this supports:
  1. export_lexicon_csv.py            -> lexicon_id.csv (gloss_id column empty)
  2. translate_lexicon.py             -> same file, gloss_id column filled by AI
  3. Open in Excel/Sheets, review/edit the gloss_id column by hand
  4. load_lexicon_translations.py --csv -> loads the (possibly edited) CSV into the DB

Design:
  - Reads and writes the SAME CSV file (in place), so there's exactly
    one file to track through the whole review process -- no
    JSONL-vs-CSV format juggling.
  - Only translates rows where the target gloss column is currently
    empty, so:
      (a) it's safe to re-run if interrupted -- already-filled rows
          are left untouched
      (b) if you've hand-edited some rows already, re-running only
          fills in the rows you haven't touched yet, never overwrites
          your edits
      (c) if you want to force-regenerate specific rows, just clear
          their gloss cell first
  - Batches multiple rows per API call (default 50) for cost/speed:
    ~16,576 unique entries would mean ~16,576 separate calls at batch
    size 1.
  - Writes progress back to the CSV after every batch (not just at the
    end), so an interruption partway through only loses the current
    in-flight batch, not all prior progress.

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 scripts/translate_lexicon.py \
    --csv output/staging/lexicon_id.csv \
    --language id \
    --model claude-opus-4-5 \
    --batch-size 50
"""
import argparse
import csv
import json
import sys
import time
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic --break-system-packages", file=sys.stderr)
    sys.exit(1)


SYSTEM_PROMPT = """You are translating short biblical lexicon glosses from English into Indonesian, for an interlinear Bible study tool covering the Hebrew Masoretic Text and Greek Textus Receptus.

Each entry is a SHORT gloss (often a single word or short phrase) for a specific Hebrew or Greek word, identified by its Strong's number. You will also see the original-language word and its transliteration for context.

Guidelines:
- Produce the Indonesian gloss a reader of an Indonesian-language interlinear/study Bible would expect -- natural, theologically standard Indonesian (consistent with how terms typically appear in Indonesian Bible translations and reference works), not a mechanical word-for-word dictionary conversion.
- Keep it SHORT, matching the register of the English gloss (a word or short phrase, not a sentence or explanation).
- For well-established theological terms, prefer the term Indonesian Bible readers would recognize (e.g. common Indonesian equivalents used in standard Indonesian Bible translations) over a literal but unfamiliar alternative.
- If the English gloss has multiple senses separated by commas or semicolons, translate each sense, keeping the same separator structure.
- If you genuinely cannot produce a confident translation for an entry (extremely rare/obscure term with no clear Indonesian equivalent), still provide your best attempt rather than leaving it blank.

Respond with ONLY a JSON array, no other text, no markdown code fences. Each element:
{"lexicon_id": <int>, "gloss": "<indonesian translation>"}

The array must contain exactly one object per input entry, in the same order, with the same lexicon_id values."""


def read_csv(path, gloss_col):
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)
    if gloss_col not in fieldnames:
        print(f"ERROR: column '{gloss_col}' not found in {path}. Found columns: {fieldnames}", file=sys.stderr)
        sys.exit(1)
    return fieldnames, rows


def write_csv(path, fieldnames, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_batch_prompt(batch):
    lines = []
    for row in batch:
        lines.append(
            f'{{"lexicon_id": {row["lexicon_id"]}, "word": "{row["word_form"]}", '
            f'"translit": "{row["translit"]}", "gloss_en": "{row["gloss_en"]}"}}'
        )
    return "Translate these entries:\n[\n" + ",\n".join(lines) + "\n]"


def call_model(client, model, batch, max_retries=3):
    prompt = build_batch_prompt(batch)
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(block.text for block in response.content if block.type == "text").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except (json.JSONDecodeError, anthropic.APIError) as e:
            print(f"  attempt {attempt}/{max_retries} failed: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, type=Path, help="CSV from export_lexicon_csv.py; edited in place")
    ap.add_argument("--language", default="id", help="ISO 639-1 target language code (determines the gloss_<lang> column name)")
    ap.add_argument("--model", default="claude-opus-4-5")
    ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--limit", type=int, help="Only process this many rows (for testing)")
    args = ap.parse_args()

    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found. Run export_lexicon_csv.py first.", file=sys.stderr)
        sys.exit(1)

    gloss_col = f"gloss_{args.language}"
    fieldnames, rows = read_csv(args.csv, gloss_col)

    by_id = {int(r["lexicon_id"]): r for r in rows}
    already_filled = len([r for r in rows if r[gloss_col].strip()])
    pending = [r for r in rows if not r[gloss_col].strip()]
    if args.limit:
        pending = pending[: args.limit]

    print(f"Total rows: {len(rows)}. Already filled: {already_filled}. Pending this run: {len(pending)}", file=sys.stderr)

    if not pending:
        print("Nothing to do -- all rows already have a gloss in this column.", file=sys.stderr)
        return

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    batches = [pending[i:i + args.batch_size] for i in range(0, len(pending), args.batch_size)]
    print(f"Processing {len(batches)} batches of up to {args.batch_size} entries each", file=sys.stderr)

    for i, batch in enumerate(batches, 1):
        print(f"Batch {i}/{len(batches)} ({len(batch)} entries)...", file=sys.stderr)
        try:
            results = call_model(client, args.model, batch)
        except Exception as e:
            print(f"  BATCH FAILED after retries: {e}", file=sys.stderr)
            print(f"  Skipping this batch; re-run later to retry (already-filled rows are skipped automatically).", file=sys.stderr)
            continue

        batch_ids = {int(r["lexicon_id"]) for r in batch}
        result_ids = {r["lexicon_id"] for r in results}
        missing = batch_ids - result_ids
        if missing:
            print(f"  WARNING: model response missing {len(missing)} entries from this batch: {sorted(missing)}", file=sys.stderr)

        for r in results:
            lid = r["lexicon_id"]
            if lid not in batch_ids:
                print(f"  WARNING: model returned unexpected lexicon_id {lid}, skipping", file=sys.stderr)
                continue
            by_id[lid][gloss_col] = r["gloss"]

        # Write progress back after every batch, not just at the end,
        # so an interruption partway through doesn't lose prior batches.
        write_csv(args.csv, fieldnames, list(by_id.values()))

    print(f"Done. Updated -> {args.csv}", file=sys.stderr)
    print(f"Review/edit the '{gloss_col}' column in a spreadsheet, then run load_lexicon_translations.py --csv", file=sys.stderr)


if __name__ == "__main__":
    main()