#!/usr/bin/env python3
"""
Find AI glosses that exist only in the database, not in the working JSONL, and optionally append them to it.

Why: build_db.py empties ai_gloss, and the JSONL is what gets reloaded. A gloss that was loaded from some other
file, or written straight into the database, is lost on the next rebuild unless it is in the working file too.
Records are matched by position (book, chapter, verse, position), so word_ids do not matter. Default is a report
only; nothing is written without --append. Re-running is safe: only missing words are appended.

Also reports rows that are in both but differ after the loader's cleaning (database-only edits such as the
H5002/H136 SQL fixes). Those are never written, so the JSONL stays the model's own output.

Usage:
  export_ai_glosses.py --db output/interlinear.sqlite --jsonl output/staging/aigloss-refs2.jsonl
  export_ai_glosses.py --db output/interlinear.sqlite --jsonl output/staging/aigloss-refs2.jsonl --append
"""
import argparse
import collections
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from load_ai_glosses import clean_gloss

SQL = """
SELECT ag.word_id, b.code, v.chapter, v.verse, w.position, w.surface, ag.gloss, ag.alt_glosses, ag.note,
       ag.prompt_version, ag.model, ag.generated_at
FROM ai_gloss ag
JOIN word w ON w.word_id = ag.word_id
JOIN verse v ON v.verse_id = w.verse_id
JOIN book b ON b.book_id = v.book_id
ORDER BY b.ordinal, v.chapter, v.verse, w.position
"""


def read_jsonl(path):
    """(by_position: {(book, chapter, verse, position): gloss}, legacy_ids: word_ids of records without a position)."""
    by_pos, legacy = {}, set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("book") is not None:
                by_pos[(r["book"], r.get("chapter"), r.get("verse"), r.get("position"))] = r.get("gloss", "")
            elif "word_id" in r:
                legacy.add(r["word_id"])
    return by_pos, legacy


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--jsonl", required=True, type=Path, help="The working gloss file to compare with (and append to)")
    ap.add_argument("--append", action="store_true", help="Append the database-only glosses to --jsonl")
    ap.add_argument("--language", default="id")
    a = ap.parse_args()

    if not a.db.is_file() or a.db.stat().st_size == 0:
        sys.exit(f"ERROR: database missing or empty: {a.db.resolve()}")
    conn = sqlite3.connect(a.db)
    rows = conn.execute(SQL).fetchall()
    conn.close()
    by_pos, legacy = read_jsonl(a.jsonl)
    if legacy:
        print(f"NOTE: {len(legacy)} records in the file have no position and are matched by word_id.", file=sys.stderr)

    missing, differ = [], 0
    for r in rows:
        key = r[1:5]
        if key in by_pos:
            differ += clean_gloss(by_pos[key]) != r[6]
        elif r[0] not in legacy:
            missing.append(r)

    print(f"Database: {len(rows)} glosses. File: {len(by_pos) + len(legacy)} records.", file=sys.stderr)
    print(f"In the database but not in the file: {len(missing)}", file=sys.stderr)
    for (version, model), n in sorted(collections.Counter((r[9], r[10]) for r in missing).items()):
        print(f"  {version} / {model}: {n}", file=sys.stderr)
    chapters, verses = {r[1:3] for r in missing}, {r[1:4] for r in missing}
    if missing:
        print(f"  covering {len(chapters)} chapters, {len(verses)} verses; first: "
              + ", ".join(f"{r[1]} {r[2]}:{r[3]} {r[5]} -> {r[6]}" for r in missing[:3]), file=sys.stderr)
    print(f"In both but different after cleaning (database-only edits, not written): {differ}", file=sys.stderr)

    if not a.append:
        print("Report only. Add --append to write the database-only glosses to the file.", file=sys.stderr)
        return
    now = datetime.now(timezone.utc).isoformat()
    with a.jsonl.open("a", encoding="utf-8") as f:
        for wid, book, chapter, verse, position, surface, gloss, alts, note, version, model, generated in missing:
            alt = (json.loads(alts) or [None])[0] if alts else None
            f.write(json.dumps({
                "word_id": wid, "book": book, "chapter": chapter, "verse": verse, "position": position, "surface": surface,
                "gloss": gloss, "alt_gloss": alt, "note": note, "language": a.language, "model": model or "unknown",
                "prompt_version": version, "generated_at": generated or now,
            }, ensure_ascii=False) + "\n")
    print(f"Appended {len(missing)} records ({len(chapters)} chapters, {len(verses)} verses) -> {a.jsonl}", file=sys.stderr)


if __name__ == "__main__":
    main()
