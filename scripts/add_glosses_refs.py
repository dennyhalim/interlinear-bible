#!/usr/bin/env python3
"""
Add position fields (book, chapter, verse, position, surface) to an older
ai_glosses JSONL that only has word_id, so the file survives database
rebuilds (load_ai_glosses.py then matches by position).

Run it against a database whose word_ids MATCH the file (the one the file
was generated or remapped for) and BEFORE any rebuild that renumbers words.
Records that already have a position are copied unchanged; records whose
word_id is not in the database are skipped and counted.

Usage:
  add_glosses_refs.py --db output/interlinear.sqlite \
    --in output/staging/aigloss-remapped.jsonl --out output/staging/aigloss-refs.jsonl
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--in", dest="inp", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()

    if not a.db.is_file() or a.db.stat().st_size == 0:
        sys.exit(f"ERROR: database missing or empty: {a.db.resolve()}")
    conn = sqlite3.connect(a.db)
    refs = {wid: (b, c, v, p, s) for wid, b, c, v, p, s in conn.execute(
        "SELECT w.word_id, b.code, v.chapter, v.verse, w.position, w.surface FROM word w "
        "JOIN verse v ON v.verse_id = w.verse_id JOIN book b ON b.book_id = v.book_id")}
    conn.close()

    read = added = kept = unknown = 0
    chapters, verses = set(), set()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    with a.inp.open(encoding="utf-8") as fin, a.out.open("w", encoding="utf-8") as fout:
        for line in fin:
            if not line.strip():
                continue
            row = json.loads(line)
            read += 1
            if row.get("book") is not None:
                kept += 1
            elif row.get("word_id") in refs:
                b, c, v, p, s = refs[row["word_id"]]
                row = {"word_id": row["word_id"], "book": b, "chapter": c, "verse": v,
                       "position": p, "surface": s, **{k: x for k, x in row.items() if k != "word_id"}}
                added += 1
            else:
                unknown += 1
                continue
            fout.write(json.dumps(row, ensure_ascii=False) + "\n")
            chapters.add((row["book"], row["chapter"]))
            verses.add((row["book"], row["chapter"], row["verse"]))

    print(f"Read {read} records: {added} given positions, {kept} already had them, {unknown} skipped "
          f"({len(chapters)} chapters, {len(verses)} verses) -> {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
