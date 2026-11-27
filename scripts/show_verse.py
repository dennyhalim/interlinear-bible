#!/usr/bin/env python3
"""
Show, for chosen verses, exactly what stage 4 sends the model (each word as surface|Strong's|gloss) and the stored
translation, so a bad sentence can be traced to its cause: odd glosses in, or the model's wording out.

References: BOOK.CHAPTER.VERSE, BOOK.CHAPTER.FIRST-LAST, or BOOK.CHAPTER for the whole chapter (book = canonical
3-letter code, e.g. GEN, PSA, JHN). The translation is shown with its Strong's tags, as stored.

Usage:
  show_verse.py --db output/interlinear.sqlite GEN.1.1 PSA.23.1-3 JHN.3
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec
from generate_verse_translations import strong_nums

REF = re.compile(r"^([1-3]?[A-Za-z]{2,3})\.(\d+)(?:\.(\d+)(?:-(\d+))?)?$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("refs", nargs="+", help="e.g. GEN.1.1  PSA.23.1-3  JHN.3")
    a = ap.parse_args()

    conn = bec.connect(a.db)
    shown = translated = 0
    for ref in a.refs:
        m = REF.match(ref)
        if not m:
            print(f"WARN: '{ref}' is not BOOK.CHAPTER[.VERSE[-LAST]]; skipped", file=sys.stderr)
            continue
        book, chapter = m[1].upper(), int(m[2])
        first = int(m[3]) if m[3] else 0
        last = int(m[4]) if m[4] else (first if m[3] else 10 ** 6)
        rows = conn.execute(
            "SELECT v.verse_id, v.verse FROM verse v JOIN book b ON b.book_id = v.book_id "
            "WHERE b.code = ? AND v.chapter = ? AND v.verse BETWEEN ? AND ? ORDER BY v.verse",
            (book, chapter, first, last)).fetchall()
        if not rows:
            print(f"WARN: no verses found for {ref}", file=sys.stderr)
            continue
        for verse_id, verse in rows:
            words = bec.get_words_for_verse(conn, verse_id, a.language)
            text = bec.get_verse_translation(conn, verse_id, a.language, tagged=True)
            print(f"== {book} {chapter}:{verse} (verse_id {verse_id}, {len(words)} words) ==")
            print("INPUT: " + " ".join(f'{w["surface"]}|{",".join(strong_nums(w["strongs_parts"]))}|{w["gloss"]}' for w in words))
            print("TEXT : " + (text if text else "(no translation stored yet)"))
            print()
            shown += 1
            translated += bool(text)
    print(f"{shown} verse(s) shown, {translated} with a stored translation", file=sys.stderr)


if __name__ == "__main__":
    main()
