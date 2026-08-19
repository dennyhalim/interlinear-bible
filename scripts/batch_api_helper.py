#!/usr/bin/env python3
"""
Shared helpers for using Anthropic's Message Batches API instead of
synchronous calls -- 50% off both input and output tokens, in exchange
for async processing (results typically available well within 24
hours, often much faster). Used by both translate_lexicon.py and
generate_ai_glosses.py's --batch-api mode.

Verified against the installed anthropic SDK (0.122.0)'s actual type
definitions, not just documentation -- confirmed real request/response
shapes:
  - client.messages.batches.create(requests=[Request(custom_id=...,
    params=MessageCreateParamsNonStreaming(...)), ...]) -> MessageBatch
  - MessageBatch.processing_status: "in_progress" | "ended" | "canceling"
  - client.messages.batches.results(batch_id) -> iterator of
    MessageBatchIndividualResponse(custom_id, result), where result is
    one of MessageBatchSucceededResult(message=<same Message shape as
    sync API>) / ...ErroredResult / ...CanceledResult / ...ExpiredResult

Each of our own logical "batches" (e.g. 50 lexicon entries, or one
chapter's words) becomes ONE request within a single Anthropic batch
job -- e.g. 455 lexicon sub-batches = 455 requests in 1 batch job
(well under the 100,000-requests-per-job limit), or ~1,199 chapter
requests = 1 batch job. So "batch" is used at two levels here: our own
prompt-batching (grouping many lexicon entries/words into one
request's prompt) and Anthropic's batch JOB (grouping many requests
into one submission) -- distinct concepts, described explicitly in
docstrings/variable names below to avoid confusion.
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
    max_tokens) tuples as ONE Anthropic batch job. Saves the returned
    batch ID (plus a custom_id -> request mapping, for context on
    retrieval) to batch_job_file so a later `retrieve` run doesn't need
    to recompute anything -- just needs the batch ID to poll/fetch.
    """
    sdk_requests = []
    for custom_id, system_prompt, user_prompt, model, max_tokens in requests:
        sdk_requests.append(
            Request(
                custom_id=custom_id,
                params=MessageCreateParamsNonStreaming(
                    model=model,
                    max_tokens=max_tokens,
                    system=system_prompt,
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
    """Poll a batch job's status until it's done ("ended"), printing
    progress. Anthropic's SLA is 24 hours but small/medium jobs
    (hundreds to low thousands of requests, our actual scale) often
    finish much faster in practice -- no guaranteed number to rely on,
    so this just polls patiently rather than assuming a duration."""
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
    """Returns a dict: custom_id -> parsed response text (or None if
    that request errored/canceled/expired, with the reason printed)."""
    results = {}
    error_count = 0
    for item in client.messages.batches.results(batch_id):
        custom_id = item.custom_id
        result = item.result
        if result.type == "succeeded":
            message = result.message
            text = "".join(block.text for block in message.content if block.type == "text").strip()
            results[custom_id] = text
        else:
            error_count += 1
            print(f"  WARNING: request '{custom_id}' did not succeed (type={result.type})", file=sys.stderr)
            results[custom_id] = None

    if error_count:
        print(f"  {error_count} of {len(results)} requests did not succeed -- see warnings above.", file=sys.stderr)
    return results


def parse_json_response(text):
    """Strip markdown fences if present (defensive, same as the
    synchronous path) and parse as JSON. Returns None on failure rather
    than raising, since one bad response in a large batch shouldn't
    crash processing of the rest."""
    if text is None:
        return None
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        print(f"  WARNING: could not parse response as JSON: {e}", file=sys.stderr)
        return None
