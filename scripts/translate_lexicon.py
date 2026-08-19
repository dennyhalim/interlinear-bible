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
  # Default: Claude (Anthropic native SDK, structured JSON via prompt)
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 scripts/translate_lexicon.py \
    --csv output/staging/lexicon_id.csv \
    --language id \
    --model claude-opus-4-5 \
    --batch-size 50

  # Testing with a free model via OpenRouter (openrouter/free randomly
  # selects a currently-free model; free-tier daily/per-minute limits
  # apply and rotate, see openrouter.ai/models -- fine for testing a
  # small --limit, not intended for the full 16,576-entry run):
  export OPENAI_API_KEY=sk-or-v1-...   # an OpenRouter key, despite the env var name
  python3 scripts/translate_lexicon.py \
    --csv output/staging/lexicon_id_test.csv \
    --language id --provider openrouter --model "openrouter/free" \
    --limit 20 --batch-size 10

  # Testing with Gemini's free tier (Flash/Flash-Lite; Pro is no
  # longer free as of April 2026 per Google's own pricing page --
  # verify current limits there before relying on a specific number):
  export GEMINI_API_KEY=...
  python3 scripts/translate_lexicon.py \
    --csv output/staging/lexicon_id_test.csv \
    --language id --provider gemini --model "gemini-2.5-flash" \
    --limit 20 --batch-size 10
"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# Anthropic is the only *required* import (default provider). OpenAI-
# compatible providers (OpenRouter, Gemini) both use the `openai`
# package's client pointed at a different base_url -- imported lazily
# only when --provider selects one of them, so the default path has no
# new dependency.
try:
    import anthropic
except ImportError:
    anthropic = None

PROVIDER_DEFAULTS = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-opus-4-5",
    },
    "openrouter": {
        "env_var": "OPENAI_API_KEY",  # the `openai` SDK's default env var name; holds an OpenRouter key here
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
    },
    "gemini": {
        "env_var": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
    },
}


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


def call_model_anthropic(client, model, batch, max_retries=3):
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


def call_model_openai_compatible(client, model, batch, max_retries=3):
    """Shared path for OpenRouter and Gemini, both accessed via the
    `openai` package's client pointed at their respective OpenAI-
    compatible base_url. Same prompt/parsing logic as the Anthropic
    path -- structured JSON via prompt instructions, since relying on
    a provider-specific JSON-mode flag would break the "same code path
    for any OpenAI-compatible provider" simplicity this is going for."""
    prompt = build_batch_prompt(batch)
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            print(f"  attempt {attempt}/{max_retries} failed: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    return None


def build_client(provider):
    cfg = PROVIDER_DEFAULTS[provider]
    api_key = os.environ.get(cfg["env_var"])
    if not api_key:
        print(f"ERROR: {cfg['env_var']} environment variable not set (required for --provider {provider}).", file=sys.stderr)
        sys.exit(1)

    if provider == "anthropic":
        if anthropic is None:
            print("ERROR: anthropic package not installed. Run: pip install anthropic --break-system-packages", file=sys.stderr)
            sys.exit(1)
        return anthropic.Anthropic(api_key=api_key), call_model_anthropic

    # openrouter / gemini: both via the `openai` package pointed at a
    # different base_url. Imported lazily here so --provider anthropic
    # (the default) never requires this dependency.
    try:
        import openai
    except ImportError:
        print("ERROR: openai package not installed (required for --provider openrouter/gemini). "
              "Run: pip install openai --break-system-packages", file=sys.stderr)
        sys.exit(1)
    client = openai.OpenAI(api_key=api_key, base_url=cfg["base_url"])
    return client, call_model_openai_compatible


def cmd_sync(args):
    """Original synchronous flow: call the API immediately per batch,
    write results back to the CSV as each batch completes."""
    model = args.model or PROVIDER_DEFAULTS[args.provider]["default_model"]

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

    print(f"Provider: {args.provider}, model: {model}", file=sys.stderr)
    print(f"Total rows: {len(rows)}. Already filled: {already_filled}. Pending this run: {len(pending)}", file=sys.stderr)

    if not pending:
        print("Nothing to do -- all rows already have a gloss in this column.", file=sys.stderr)
        return

    client, call_model = build_client(args.provider)

    batches = [pending[i:i + args.batch_size] for i in range(0, len(pending), args.batch_size)]
    print(f"Processing {len(batches)} batches of up to {args.batch_size} entries each", file=sys.stderr)

    for i, batch in enumerate(batches, 1):
        print(f"Batch {i}/{len(batches)} ({len(batch)} entries)...", file=sys.stderr)
        try:
            results = call_model(client, model, batch)
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

        write_csv(args.csv, fieldnames, list(by_id.values()))

    print(f"Done. Updated -> {args.csv}", file=sys.stderr)
    print(f"Review/edit the '{gloss_col}' column in a spreadsheet, then run load_lexicon_translations.py --csv", file=sys.stderr)


def cmd_submit(args):
    """Batch API flow, step 1: build all requests and submit as ONE
    Anthropic batch job (50% cheaper than sync, results ready
    asynchronously -- typically much faster than the 24h SLA for a job
    this size, but not guaranteed). Anthropic-only: the Batch API is an
    Anthropic-specific endpoint, not something OpenRouter/Gemini expose
    identically, so --provider is not offered here."""
    import batch_api_helper as bah

    if anthropic is None:
        print("ERROR: anthropic package not installed. Run: pip install anthropic --break-system-packages", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    if not args.csv.exists():
        print(f"ERROR: {args.csv} not found. Run export_lexicon_csv.py first.", file=sys.stderr)
        sys.exit(1)

    model = args.model or PROVIDER_DEFAULTS["anthropic"]["default_model"]
    gloss_col = f"gloss_{args.language}"
    fieldnames, rows = read_csv(args.csv, gloss_col)

    pending = [r for r in rows if not r[gloss_col].strip()]
    if args.limit:
        pending = pending[: args.limit]

    print(f"Model: {model}", file=sys.stderr)
    print(f"Total rows: {len(rows)}. Pending: {len(pending)}", file=sys.stderr)

    if not pending:
        print("Nothing to do -- all rows already have a gloss in this column.", file=sys.stderr)
        return

    sub_batches = [pending[i:i + args.batch_size] for i in range(0, len(pending), args.batch_size)]
    print(f"Building {len(sub_batches)} requests (one Anthropic batch job, {args.batch_size} entries/request)", file=sys.stderr)

    requests = []
    for i, sub_batch in enumerate(sub_batches):
        custom_id = f"lex-{i:05d}"
        prompt = build_batch_prompt(sub_batch)
        requests.append((custom_id, SYSTEM_PROMPT, prompt, model, 4096))

    client = anthropic.Anthropic(api_key=api_key)
    bah.submit_batch_job(client, requests, args.batch_job_file)

    # Save the sub-batch membership (custom_id -> list of lexicon_ids)
    # alongside the job file, so `retrieve` knows which CSV rows each
    # request's response corresponds to without re-deriving it.
    membership = {f"lex-{i:05d}": [r["lexicon_id"] for r in sb] for i, sb in enumerate(sub_batches)}
    membership_file = args.batch_job_file.with_suffix(".membership.json")
    membership_file.write_text(json.dumps(membership, ensure_ascii=False), encoding="utf-8")
    print(f"Saved request membership -> {membership_file}", file=sys.stderr)
    print(f"\nRun this again with 'retrieve' once the job is done -- check status any time with:", file=sys.stderr)
    print(f"  python3 scripts/translate_lexicon.py retrieve --csv {args.csv} --language {args.language} --batch-job-file {args.batch_job_file}", file=sys.stderr)


def cmd_retrieve(args):
    """Batch API flow, step 2: poll the submitted job until done, fetch
    results, write them into the CSV (same skip-if-already-filled and
    hand-edit-preserving behavior as the sync path)."""
    import batch_api_helper as bah

    if not args.batch_job_file.exists():
        print(f"ERROR: {args.batch_job_file} not found. Run 'submit' first.", file=sys.stderr)
        sys.exit(1)
    membership_file = args.batch_job_file.with_suffix(".membership.json")
    if not membership_file.exists():
        print(f"ERROR: {membership_file} not found (should have been created by 'submit').", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    job_info = json.loads(args.batch_job_file.read_text())
    membership = json.loads(membership_file.read_text())
    batch_id = job_info["batch_id"]

    client = anthropic.Anthropic(api_key=api_key)

    print(f"Polling batch job {batch_id}...", file=sys.stderr)
    batch = bah.poll_batch_job(client, batch_id, poll_interval_seconds=args.poll_interval, max_wait_seconds=args.max_wait)

    if batch.processing_status != "ended":
        print("Job not yet finished (see --max-wait). Run 'retrieve' again later to check.", file=sys.stderr)
        return

    print("Job ended, fetching results...", file=sys.stderr)
    raw_results = bah.fetch_batch_results(client, batch_id)

    gloss_col = f"gloss_{args.language}"
    fieldnames, rows = read_csv(args.csv, gloss_col)
    by_id = {int(r["lexicon_id"]): r for r in rows}

    filled = 0
    for custom_id, lexicon_ids in membership.items():
        text = raw_results.get(custom_id)
        parsed = bah.parse_json_response(text)
        if parsed is None:
            print(f"  WARNING: request '{custom_id}' has no usable result, its {len(lexicon_ids)} entries remain untranslated (re-submit a follow-up batch for just these if needed).", file=sys.stderr)
            continue
        parsed_by_id = {str(item["lexicon_id"]): item["gloss"] for item in parsed}
        for lid_str in lexicon_ids:
            if lid_str in parsed_by_id:
                by_id[int(lid_str)][gloss_col] = parsed_by_id[lid_str]
                filled += 1
            else:
                print(f"  WARNING: lexicon_id {lid_str} missing from response for '{custom_id}'", file=sys.stderr)

    write_csv(args.csv, fieldnames, list(by_id.values()))
    print(f"\nFilled {filled} rows -> {args.csv}", file=sys.stderr)
    print(f"Review/edit the '{gloss_col}' column in a spreadsheet, then run load_lexicon_translations.py --csv", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    sync_p = sub.add_parser("sync", help="Synchronous calls (original behavior, works with any --provider)")
    sync_p.add_argument("--csv", required=True, type=Path, help="CSV from export_lexicon_csv.py; edited in place")
    sync_p.add_argument("--language", default="id")
    sync_p.add_argument("--provider", default="anthropic", choices=list(PROVIDER_DEFAULTS.keys()))
    sync_p.add_argument("--model", default=None)
    sync_p.add_argument("--batch-size", type=int, default=50)
    sync_p.add_argument("--limit", type=int)
    sync_p.set_defaults(func=cmd_sync)

    submit_p = sub.add_parser("submit", help="Submit a batch job via Anthropic's Batch API (50% cheaper, async)")
    submit_p.add_argument("--csv", required=True, type=Path)
    submit_p.add_argument("--language", default="id")
    submit_p.add_argument("--model", default=None)
    submit_p.add_argument("--batch-size", type=int, default=50)
    submit_p.add_argument("--limit", type=int)
    submit_p.add_argument("--batch-job-file", type=Path, default=Path("output/staging/lexicon_batch_job.json"))
    submit_p.set_defaults(func=cmd_submit)

    retrieve_p = sub.add_parser("retrieve", help="Poll and retrieve results from a previously submitted batch job")
    retrieve_p.add_argument("--csv", required=True, type=Path)
    retrieve_p.add_argument("--language", default="id")
    retrieve_p.add_argument("--batch-job-file", type=Path, default=Path("output/staging/lexicon_batch_job.json"))
    retrieve_p.add_argument("--poll-interval", type=int, default=30, help="Seconds between status checks")
    retrieve_p.add_argument("--max-wait", type=int, default=None, help="Give up polling after this many seconds (job keeps running server-side; just re-run retrieve later)")
    retrieve_p.set_defaults(func=cmd_retrieve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
