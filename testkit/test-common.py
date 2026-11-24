#!/usr/bin/env python3
"""
Tests for bible_export_common.py on a toy database (no network): gloss priority, the one-row-per-word lexicon
join, first_sense, verse translations and connect().

Usage, from the package root:   python testkit/test_common.py
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "testkit")]
import mkdb                      # noqa: E402
import bible_export_common as bec  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


os.chdir(tempfile.mkdtemp())
check("first_sense: separators, parentheses and the colon",
      all(bec.first_sense(a) == b for a, b in [
          ("the; this, who", "the"), ("in/on/among", "in"), ("(to or with or by, etc.) these", "(to or with or by, etc.) these"),
          ("woman: wife", "woman"), ("to come (in): bring", "to come (in)"), ("", ""), (None, "")]))
check("66 books in canonical order, GEN first and REV last",
      len(bec.CANONICAL_BOOK_ORDER) == 66 and bec.CANONICAL_BOOK_ORDER[0] == "GEN" and bec.CANONICAL_BOOK_ORDER[-1] == "REV")

# ---- a database with one verse covering every gloss source, and a base number with four lexicon entries ----
mkdb.build("db.sqlite")
c = sqlite3.connect("db.sqlite")
c.executescript("""
create table verse_translation(verse_id integer, language text, text text, primary key(verse_id, language));
insert into verse values (50, 1, 7, 1);
insert into lexicon_entry values (20,'G2491A','G2491','John (a)'),(21,'G2491B','G2491','John (b)'),(22,'G2491C','G2491','John (c)'),(23,'G2491D','G2491','John (d)'),
                                 (24,'H0001','H0001','the; this, who');
insert into lexicon_gloss_translation values (20,'id','Yohanes a'),(21,'id','Yohanes b'),(22,'id','Yohanes c'),(23,'id','Yohanes d');
-- word 201: AI gloss | 202: translated lexicon | 203: English lexicon only | 204: source gloss only | 205: nothing | 206/207: bare and exact G2491
insert into word values (201,50,1,'hebrew','a','a','H5002','["H5002"]','HN',NULL),
                        (202,50,2,'hebrew','b','b','H3068G','["H3068G"]','HN',NULL),
                        (203,50,3,'hebrew','c','c','H0001','["H0001"]','HN',NULL),
                        (204,50,4,'hebrew','d','d','H9999','["H9999"]','HN','source gloss'),
                        (205,50,5,'hebrew','e','e','H8888',NULL,'HN',NULL),
                        (206,50,6,'greek','f','f','G2491',NULL,'N-NSM',NULL),
                        (207,50,7,'greek','g','g','G2491B','["G2491B"]','N-NSM',NULL);
insert into ai_gloss (word_id, gloss) values (201, 'AI-gloss');
insert into verse_translation values (50, 'id', 'Pada mula{G746} Allah{H430}');
""")
c.commit()
words = bec.get_words_for_verse(c, 50, "id")
by = {w["word_id"]: w for w in words}
check("one row per word, in reading order, even where a base Strong's number has four lexicon entries",
      [w["word_id"] for w in words] == [201, 202, 203, 204, 205, 206, 207])
check("gloss priority: AI -> translated lexicon -> English lexicon -> source gloss -> empty",
      [(by[i]["gloss"], by[i]["gloss_source"]) for i in (201, 202, 203, 204, 205)] == [
          ("AI-gloss", "ai_contextual"), ("TUHAN", "lexicon_translated"), ("the", "lexicon_en"), ("source gloss", "source_dataset"), ("", "none")])
check("lexicon join: a bare number takes the lowest lexicon_id, an exact dstrong match wins",
      by[206]["gloss"] == "Yohanes a" and by[207]["gloss"] == "Yohanes b")
check("strongs_parts: decoded from JSON, or the head number when absent",
      by[201]["strongs_parts"] == ["H5002"] and by[206]["strongs_parts"] == ["G2491"] and by[205]["strongs_parts"] == ["H8888"])
check("get_verse_translation: tagged keeps {G746}, plain strips it, other language or verse -> None",
      bec.get_verse_translation(c, 50, "id", tagged=True) == "Pada mula{G746} Allah{H430}"
      and bec.get_verse_translation(c, 50, "id") == "Pada mula Allah" and bec.get_verse_translation(c, 50, "en") is None
      and bec.get_verse_translation(c, 999, "id") is None)
check("get_verse_translation: a database without the table gives None, not an error",
      bec.get_verse_translation(sqlite3.connect(":memory:"), 1, "id") is None)
check("get_books and get_verses_for_book are ordered",
      [b[1] for b in bec.get_books(c)] == ["GEN", "EXO", "MAT"] and [v[1:] for v in bec.get_verses_for_book(c, 1)][:3] == [(1, 1), (1, 2), (1, 3)])
c.close()


def exits(path):
    try:
        bec.connect(path)
    except SystemExit as e:
        return str(e)
    return None


Path("empty.sqlite").touch()
sqlite3.connect("other.sqlite").execute("create table x(a)").connection.commit()
check("connect: a missing path stops with a clear error and creates nothing",
      "missing or empty" in (exits("nope.sqlite") or "") and not os.path.exists("nope.sqlite"))
check("connect: an empty file and a database without a 'book' table are refused",
      "missing or empty" in (exits("empty.sqlite") or "") and "no 'book' table" in (exits("other.sqlite") or ""))
check("connect: a built database opens", exits("db.sqlite") is None and bec.connect("db.sqlite").execute("select count(*) from book").fetchone()[0] == 3)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
