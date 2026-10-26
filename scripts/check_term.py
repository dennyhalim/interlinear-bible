#!/usr/bin/env python3
"""
For the given Strong's numbers, show what the AI was SENT as the lexicon gloss
and what gloss it RETURNED, grouped with counts. Uses the same lexicon join as
generate_ai_glosses.py (exact dstrong first, else the lowest lexicon_id), so it
shows the gloss the model really saw, which is not always the entry you edited.

Usage:
  check_term.py --db output/interlinear.sqlite G907,G908,G909,G910,G911
"""
import argparse
import collections
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import generate_ai_glosses as g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strongs", help="Comma-separated Strong's numbers, e.g. G907,H5002")
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--language", default="id")
    a = ap.parse_args()

    targets = g.parse_targets(a.strongs)
    conn = sqlite3.connect(a.db)
    cur = conn.cursor()
    pats = [f"{t[0]}{int(t[1:]):04d}%" for t in targets]
    chapters = cur.execute(
        "SELECT DISTINCT v.book_id, b.code, v.chapter FROM word w JOIN verse v ON v.verse_id = w.verse_id "
        "JOIN book b ON b.book_id = v.book_id WHERE " + " OR ".join("w.strongs LIKE ?" for _ in pats), pats).fetchall()
    ai = dict(cur.execute("SELECT word_id, gloss FROM ai_gloss"))

    seen = collections.Counter()
    words = 0
    for book_id, code, chapter in chapters:
        for w in g.get_chapter_words(cur, book_id, chapter, a.language, set()):
            if g.base_strongs(w[5]) in targets:
                words += 1
                seen[(w[5], w[9] or f"(no {a.language} lexicon gloss; English: {w[8]})", ai.get(w[0], "(no AI gloss yet)"))] += 1

    print(f"{words} words in {len(chapters)} chapters")
    print(f"{'strongs':<8} {'lexicon gloss SENT to the AI':<34} {'AI gloss returned':<28} count")
    for (st, lex, gloss), n in sorted(seen.items(), key=lambda kv: (kv[0][0], -kv[1])):
        print(f"{st:<8} {lex[:33]:<34} {gloss[:27]:<28} {n}")


if __name__ == "__main__":
    main()
