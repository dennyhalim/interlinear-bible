#!/usr/bin/env python3
"""
Shared helpers for using Anthropic's Message Batches API instead of
synchronous calls -- 50% off both input and output tokens, in exchange for
async processing (results typically within 24 hours, often much faster).
Used by translate_lexicon.py, generate_ai_glosses.py,
generate_verse_translations.py and import_batch_results.py.

Verified against the installed anthropic SDK's type definitions:
  - client.messages.batches.create(requests=[Request(custom_id=...,
    params=MessageCreateParamsNonStreaming(...)), ...]) -> MessageBatch
  - MessageBatch.processing_status: "in_progress" | "ended" | "canceling"
  - client.messages.batches.results(batch_id) -> iterator of
    MessageBatchIndividualResponse(custom_id, result); result is one of
    succeeded (message = same Message shape as the sync API) / errored /
    canceled / expired

"Batch" is used at two levels: our own prompt-batching (many lexicon
entries or words in one request) and Anthropic's batch JOB (many requests in
one submission).

parse_json_response is lenient on purpose: it accepts a JSON array, or
objects one per line with or without the [ ] and commas (models sometimes
answer in JSON Lines), and keeps every complete leading object when a reply
is cut off or malformed partway.
"""
import json
import sys
import time
from pathlib import Path

import anthropic
from anthropic.types.messages.batch_create_params import Request
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming


def submit_batch_job(client, requests, batch_job_file):
    """Submit a list of (custom_id, system_prompt, user_prompt, model,
    max_tokens) tuples as ONE Anthropic batch job. Saves the returned batch
    ID to batch_job_file so a later `retrieve` run only needs the ID."""
    sdk_requests = []
    for custom_id, system_prompt, user_prompt, model, max_tokens in requests:
        sdk_requests.append(
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=max_tokens,
                    # Same cached block in every request: reads cost ~5% of base input
                    # (best effort in batches; see fetch_batch_results for the hit count)
                    system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user_prompt}],
                ),
            )
        )

    batch = client.messages.batches.create(requests=sdk_requests)

    batch_job_file.parent.mkdir(parents=True, exist_ok=True)
    batch_job_file.write_text(
        json.dumps({"batch_id": batch.id, "request_count": len(sdk_requests), "submitted_at": time.time()}, indent=2),
        encoding="utf-8",
    )

    print(f"Submitted batch job: {batch.id}", file=sys.stderr)
    print(f"  {len(sdk_requests)} requests, status: {batch.processing_status}", file=sys.stderr)
    print(f"  Job info saved to {batch_job_file} -- use this file with the 'retrieve' command later.", file=sys.stderr)
    return batch


def poll_batch_job(client, batch_id, poll_interval_seconds=30, max_wait_seconds=None):
    """Poll a batch job until it is "ended", printing progress. Small and
    medium jobs often finish well inside the 24 hour SLA, with no guarantee."""
    start = time.time()
    while True:
        batch = client.messages.batches.retrieve(batch_id)
        counts = batch.request_counts
        print(
            f"  status={batch.processing_status} "
            f"processing={counts.processing} succeeded={counts.succeeded} "
            f"errored={counts.errored} canceled={counts.canceled} expired={counts.expired}",
            file=sys.stderr,
        )
        if batch.processing_status == "ended":
            return batch
        if max_wait_seconds and (time.time() - start) > max_wait_seconds:
            print(f"  Reached --max-wait of {max_wait_seconds}s, stopping poll (job is still running server-side).", file=sys.stderr)
            return batch
        time.sleep(poll_interval_seconds)


def fetch_batch_results(client, batch_id):
    """Returns a dict: custom_id -> reply text (None if that request
    errored/canceled/expired, with the reason printed)."""
    results = {}
    error_count = 0
    fresh = cache_read = cache_written = 0
    for item in client.messages.batches.results(batch_id):
        custom_id = item.custom_id
        result = item.result
        if result.type == "succeeded":
            message = result.message
            usage = message.usage
            fresh += usage.input_tokens or 0
            cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_written += getattr(usage, "cache_creation_input_tokens", 0) or 0
            if message.stop_reason == "max_tokens":
                print(f"  WARNING: request '{custom_id}' was cut off by max_tokens", file=sys.stderr)
            results[custom_id] = "".join(block.text for block in message.content if block.type == "text").strip()
        else:
            error_count += 1
            print(f"  WARNING: request '{custom_id}' did not succeed (type={result.type})", file=sys.stderr)
            results[custom_id] = None

    if error_count:
        print(f"  {error_count} of {len(results)} requests did not succeed -- see warnings above.", file=sys.stderr)
    print(f"  input tokens: {fresh:,} uncached, {cache_read:,} read from cache, {cache_written:,} written to cache", file=sys.stderr)
    return results


def parse_json_response(text):
    """Parse a model reply into a list of objects. Accepts a JSON array, or
    objects one per line with or without the surrounding [ ] and commas.
    If the reply is cut off or malformed partway, every complete leading
    object is kept. Returns None if nothing is usable."""
    if text is None:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return [data] if isinstance(data, dict) else data
    except json.JSONDecodeError:
        pass
    brace, bracket = text.find("{"), text.find("[")
    if brace == -1:
        return None
    dec, i, items = json.JSONDecoder(), (bracket + 1 if 0 <= bracket < brace else brace), []
    while True:
        while i < len(text) and text[i] in " \t\r\n,":
            i += 1
        if i >= len(text) or text[i] != "{":
            break
        try:
            obj, i = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            break
        items.append(obj)
    if not items:
        print("  WARNING: response not parseable as JSON", file=sys.stderr)
        return None
    print(f"  WARNING: response not strict JSON (cut off or malformed); kept {len(items)} complete entries", file=sys.stderr)
    return items
