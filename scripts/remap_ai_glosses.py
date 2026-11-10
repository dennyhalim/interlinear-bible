#!/usr/bin/env python3
"""
Remap ai_gloss JSONL word_ids from an OLD database to a REBUILT one, so
glosses generated before a rebuild can still be loaded.

word_id changes when build_db.py inserts new verses, but each word keeps
the same (book, chapter, verse, position). A gloss is remapped only if
the word's surface text is identical in both databases; anything else is
counted and skipped, never guessed.

Usage:
  python3 scripts/remap_ai_glosses.py --old-db output/interlinear_old.sqlite \
    --new-db output/interlinear.sqlite \
    --in output/staging/ai_glosses_old.jsonl --out output/staging/ai_glosses_remapped.jsonl
Then: load_ai_glosses.py --db output/interlinear.sqlite --jsonl <remapped file>
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

WORDS_SQL = """
    SELECT w.word_id, b.code, v.chapter, v.verse, w.position, w.surface
    FROM word w
    JOIN verse v ON v.verse_id = w.verse_id
    JOIN book b ON b.book_id = v.book_id
"""


def load(path):
    if not Path(path).is_file():
        sys.exit(f"ERROR: database not found: {path}")
    conn = sqlite3.connect(path)
    rows = conn.execute(WORDS_SQL).fetchall()
    conn.close()
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old-db", required=True, type=Path)
    ap.add_argument("--new-db", required=True, type=Path)
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    old = {wid: (key, surface) for wid, *key, surface in
           ((r[0], *r[1:5], r[5]) for r in load(a.old_db))}
    new = {tuple(r[1:5]): (r[0], r[5]) for r in load(a.new_db)}

    stats = dict(read=0, written=0, unknown_old=0, missing_new=0, text_differs=0)
    chapters, verses = set(), set()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.inp.open(encoding="utf-8") as fin, a.out.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            row = json.loads(line)
            stats["read"] += 1
            hit = old.get(row["word_id"])
            if hit is None:
                stats["unknown_old"] += 1
                continue
            key, surface = tuple(hit[0]), hit[1]
            target = new.get(key)
            if target is None:
                stats["missing_new"] += 1
            elif target[1] != surface:
                stats["text_differs"] += 1
            else:
                row["word_id"] = target[0]
                fout.write(json.dumps(row, ensure_ascii=False) + "\n")
                stats["written"] += 1
                chapters.add(key[:2])
                verses.add(key[:3])

    print(f"Read {stats['read']} glosses, remapped {stats['written']} "
          f"({len(chapters)} chapters, {len(verses)} verses) -> {a.out}", file=sys.stderr)
    for name in ("unknown_old", "missing_new", "text_differs"):
        if stats[name]:
            print(f"  WARNING: {stats[name]} skipped ({name.replace('_', ' ')})", file=sys.stderr)


if __name__ == "__main__":
    main()
