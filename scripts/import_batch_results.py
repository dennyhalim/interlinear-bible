#!/usr/bin/env python3
"""
Turn the raw results of ANY finished gloss batch into this project's
ai_glosses JSONL, without needing the job/membership files that a later
submit may have overwritten. Each reply carries its own word_ids, so it is
self-describing. Truncated replies are salvaged (every complete entry kept).

Source, pick one:
  --batch-id msgbatch_...   fetch via API (find the ID in the Console, or with
                            client.messages.batches.list())
  --file results.jsonl      a results .jsonl downloaded from the Console

Words already in --out are skipped, so it is safe to run repeatedly. If --db
is given, word_ids that do not exist in it are skipped and the chapters and
verses covered are reported.

IMPORTANT: word_ids belong to the database the job was built from. If that
was an older build, run remap_ai_glosses.py on the output before loading.

Usage:
  python3 scripts/import_batch_results.py --batch-id msgbatch_... \
    --out output/staging/ai_glosses_id.jsonl --db output/interlinear.sqlite
  python3 scripts/import_batch_results.py --file results.jsonl --out ...
"""
import argparse
import contextlib
import io
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import batch_api_helper as bah


def from_file(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rec = json.loads(line)
            res = rec["result"]
            if res["type"] != "succeeded":
                print(f"  WARNING: {rec['custom_id']} did not succeed ({res['type']})", file=sys.stderr)
                continue
            msg = res["message"]
            text = "".join(b["text"] for b in msg["content"] if b["type"] == "text")
            yield rec["custom_id"], text, msg.get("model"), msg.get("stop_reason")


def from_api(batch_id):
    import anthropic
    for item in anthropic.Anthropic().messages.batches.results(batch_id):
        if item.result.type != "succeeded":
            print(f"  WARNING: {item.custom_id} did not succeed ({item.result.type})", file=sys.stderr)
            continue
        m = item.result.message
        yield item.custom_id, "".join(b.text for b in m.content if b.type == "text"), m.model, m.stop_reason


def done_ids(path):
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["word_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def locations(db_path):
    """word_id -> (book code, chapter, verse); empty dict if no --db."""
    if not db_path:
        return {}
    if not Path(db_path).is_file():
        sys.exit(f"ERROR: database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT w.word_id, b.code, v.chapter, v.verse FROM word w "
        "JOIN verse v ON v.verse_id = w.verse_id JOIN book b ON b.book_id = v.book_id").fetchall()
    conn.close()
    return {r[0]: r[1:] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--batch-id")
    src.add_argument("--file", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--db", type=Path, help="Optional: validate word_ids and report chapters/verses")
    ap.add_argument("--language", default="id")
    ap.add_argument("--prompt-version", default="v1")
    a = ap.parse_args()

    results = from_file(a.file) if a.file else from_api(a.batch_id)
    done, loc = done_ids(a.out), locations(a.db)
    now = datetime.now(timezone.utc).isoformat()
    written = dup = unknown = cut = replies = entries = rejected = unparseable = 0
    chapters, verses = set(), set()

    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.out.open("a", encoding="utf-8") as out:
        for custom_id, text, model, stop in results:
            replies += 1
            cut += stop == "max_tokens"
            with contextlib.redirect_stderr(io.StringIO()):  # one 'salvaged' line per reply is noise; summarized below
                items = bah.parse_json_response(text)
            if not items:
                unparseable += 1
                continue
            for it in items:
                entries += 1
                wid, gloss = it.get("word_id"), it.get("gloss")
                if not isinstance(wid, int) or not isinstance(gloss, str) or not gloss.strip():
                    rejected += 1
                    continue
                if wid in done:
                    dup += 1
                    continue
                if loc and wid not in loc:
                    unknown += 1
                    continue
                out.write(json.dumps({
                    "word_id": wid, "gloss": gloss,
                    "alt_gloss": it.get("alt_gloss") or None, "note": it.get("note") or None,
                    "language": a.language, "model": model or "unknown",
                    "prompt_version": a.prompt_version, "generated_at": now,
                }, ensure_ascii=False) + "\n")
                done.add(wid)
                written += 1
                if wid in loc:
                    chapters.add(loc[wid][:2])
                    verses.add(loc[wid])

    print(f"Read {replies} successful replies ({unparseable} unparseable): "
          f"{entries} entries, {rejected} rejected", file=sys.stderr)
    if replies == 0:
        print("  No successful replies. If this job was canceled or expired, it has no output to import.", file=sys.stderr)
    print(f"Wrote {written} new glosses -> {a.out} ({dup} already present)", file=sys.stderr)
    if loc:
        print(f"  covering {len(chapters)} chapters, {len(verses)} verses", file=sys.stderr)
    if unknown:
        print(f"  WARNING: {unknown} word_ids not in {a.db} were skipped", file=sys.stderr)
    if cut:
        print(f"  NOTE: {cut} replies were cut off by max_tokens (complete entries kept)", file=sys.stderr)


if __name__ == "__main__":
    main()
