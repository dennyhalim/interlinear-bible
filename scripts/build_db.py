#!/usr/bin/env python3
"""
Assemble the final interlinear.sqlite from the JSONL files produced by
parse_tr.py, parse_tahot.py, parse_lexicons.py, parse_morphology.py.

Usage:
  build_db.py --staging <dir> --schema schema.sql --out output/interlinear.sqlite

Expects in <staging>:
  tr.jsonl          (from parse_tr.py)
  tahot.jsonl       (from parse_tahot.py)
  lexicons.jsonl    (from parse_lexicons.py)
  morphology.jsonl  (from parse_morphology.py)
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

# 66-book canon: code, name, testament, ordinal
BOOKS = [
    ("GEN","Genesis","OT",1),("EXO","Exodus","OT",2),("LEV","Leviticus","OT",3),
    ("NUM","Numbers","OT",4),("DEU","Deuteronomy","OT",5),("JOS","Joshua","OT",6),
    ("JDG","Judges","OT",7),("RUT","Ruth","OT",8),("1SA","1 Samuel","OT",9),
    ("2SA","2 Samuel","OT",10),("1KI","1 Kings","OT",11),("2KI","2 Kings","OT",12),
    ("1CH","1 Chronicles","OT",13),("2CH","2 Chronicles","OT",14),("EZR","Ezra","OT",15),
    ("NEH","Nehemiah","OT",16),("EST","Esther","OT",17),("JOB","Job","OT",18),
    ("PSA","Psalms","OT",19),("PRO","Proverbs","OT",20),("ECC","Ecclesiastes","OT",21),
    ("SNG","Song of Songs","OT",22),("ISA","Isaiah","OT",23),("JER","Jeremiah","OT",24),
    ("LAM","Lamentations","OT",25),("EZK","Ezekiel","OT",26),("DAN","Daniel","OT",27),
    ("HOS","Hosea","OT",28),("JOL","Joel","OT",29),("AMO","Amos","OT",30),
    ("OBA","Obadiah","OT",31),("JON","Jonah","OT",32),("MIC","Micah","OT",33),
    ("NAM","Nahum","OT",34),("HAB","Habakkuk","OT",35),("ZEP","Zephaniah","OT",36),
    ("HAG","Haggai","OT",37),("ZEC","Zechariah","OT",38),("MAL","Malachi","OT",39),
    ("MAT","Matthew","NT",40),("MRK","Mark","NT",41),("LUK","Luke","NT",42),
    ("JHN","John","NT",43),("ACT","Acts","NT",44),("ROM","Romans","NT",45),
    ("1CO","1 Corinthians","NT",46),("2CO","2 Corinthians","NT",47),("GAL","Galatians","NT",48),
    ("EPH","Ephesians","NT",49),("PHP","Philippians","NT",50),("COL","Colossians","NT",51),
    ("1TH","1 Thessalonians","NT",52),("2TH","2 Thessalonians","NT",53),("1TI","1 Timothy","NT",54),
    ("2TI","2 Timothy","NT",55),("TIT","Titus","NT",56),("PHM","Philemon","NT",57),
    ("HEB","Hebrews","NT",58),("JAS","James","NT",59),("1PE","1 Peter","NT",60),
    ("2PE","2 Peter","NT",61),("1JN","1 John","NT",62),("2JN","2 John","NT",63),
    ("3JN","3 John","NT",64),("JUD","Jude","NT",65),("REV","Revelation","NT",66),
]


def load_jsonl(path: Path):
    if not path.exists():
        print(f"WARN: {path} not found, skipping", file=sys.stderr)
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def get_or_create_verse(cur, book_ids, cache, book_code, chapter, verse):
    key = (book_code, chapter, verse)
    if key in cache:
        return cache[key]
    book_id = book_ids.get(book_code)
    if book_id is None:
        return None
    cur.execute(
        "INSERT OR IGNORE INTO verse (book_id, chapter, verse) VALUES (?,?,?)",
        (book_id, chapter, verse),
    )
    cur.execute(
        "SELECT verse_id FROM verse WHERE book_id=? AND chapter=? AND verse=?",
        (book_id, chapter, verse),
    )
    verse_id = cur.fetchone()[0]
    cache[key] = verse_id
    return verse_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--staging", required=True, type=Path)
    ap.add_argument("--schema", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    conn = sqlite3.connect(args.out)
    cur = conn.cursor()
    cur.executescript(args.schema.read_text(encoding="utf-8"))

    # Books
    cur.executemany(
        "INSERT INTO book (code, name, testament, ordinal) VALUES (?,?,?,?)",
        BOOKS,
    )
    conn.commit()
    cur.execute("SELECT code, book_id FROM book")
    book_ids = dict(cur.fetchall())

    verse_cache = {}

    # --- Greek NT words (from byztxt TR) ---
    tr_path = args.staging / "tr.jsonl"
    tr_count = 0
    for row in load_jsonl(tr_path):
        verse_id = get_or_create_verse(
            cur, book_ids, verse_cache, row["book_code"], row["chapter"], row["verse"]
        )
        if verse_id is None:
            continue
        cur.execute(
            """INSERT OR IGNORE INTO word
               (verse_id, position, word_group, language, surface, translit,
                strongs, strongs_parts, morph_code, parse_number, gloss_source, punct_tag)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                verse_id, row["position"], None, "greek",
                row["surface_translit"], row["surface_translit"],
                row["strongs"], None, row["morph_code"], row.get("parse_number"),
                None, None,
            ),
        )
        tr_count += 1
    conn.commit()
    print(f"Inserted {tr_count} Greek words", file=sys.stderr)

    # --- Hebrew OT morphemes (from STEPBible TAHOT) ---
    tahot_path = args.staging / "tahot.jsonl"
    tahot_count = 0
    word_group_counters = {}
    for row in load_jsonl(tahot_path):
        verse_id = get_or_create_verse(
            cur, book_ids, verse_cache, row["book_code"], row["chapter"], row["verse"]
        )
        if verse_id is None:
            continue
        cur.execute(
            """INSERT OR IGNORE INTO word
               (verse_id, position, word_group, language, surface, translit,
                strongs, strongs_parts, morph_code, parse_number, gloss_source, punct_tag)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                verse_id, row["position"], row["position"], "hebrew",
                row["hebrew"], row["translit"],
                row["head_strongs"], json.dumps(row["strongs_parts"], ensure_ascii=False),
                "/".join(row["grammar_parts"]), None,
                row["gloss"], row.get("punct_tag"),
            ),
        )
        tahot_count += 1
    conn.commit()
    print(f"Inserted {tahot_count} Hebrew morphemes", file=sys.stderr)

    # --- Lexicon entries ---
    lex_path = args.staging / "lexicons.jsonl"
    lex_count = 0
    for row in load_jsonl(lex_path):
        cur.execute(
            """INSERT INTO lexicon_entry
               (language, estrong, dstrong, ustrong, word_form, translit, morph_brief, gloss, meaning)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                row["language"], row["estrong"], row["dstrong"], row.get("ustrong"),
                row["word"], row["translit"], row["morph"], row["gloss"], row.get("meaning"),
            ),
        )
        lex_count += 1
    conn.commit()
    print(f"Inserted {lex_count} lexicon entries", file=sys.stderr)

    # --- Morphology code expansions ---
    morph_path = args.staging / "morphology.jsonl"
    morph_count = 0
    for row in load_jsonl(morph_path):
        cur.execute(
            """INSERT OR IGNORE INTO morphology_code (language, code, description, fields_json)
               VALUES (?,?,?,?)""",
            (row["language"], row["code"], row["description_raw"], json.dumps(row["fields"], ensure_ascii=False)),
        )
        morph_count += 1
    conn.commit()
    print(f"Inserted {morph_count} morphology codes", file=sys.stderr)

    # --- Metadata ---
    cur.executemany(
        "INSERT INTO metadata (key, value) VALUES (?,?)",
        [
            ("schema_version", "1"),
            ("built_at", datetime.now(timezone.utc).isoformat()),
            ("nt_source", "byztxt/greektext-textus-receptus (Robinson TR, public domain)"),
            ("ot_source", "STEPBible-Data TAHOT (Leningrad Codex via OpenScriptures, CC BY 4.0)"),
            ("lexicon_source", "STEPBible-Data TBESG/TBESH (CC BY 4.0)"),
            ("morphology_source", "STEPBible-Data TEGMC/TEHMC (CC BY 4.0)"),
            ("ai_gloss_status", "not yet generated"),
        ],
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM verse")
    print(f"Total verses: {cur.fetchone()[0]}", file=sys.stderr)
    cur.execute("SELECT COUNT(*) FROM word")
    print(f"Total words: {cur.fetchone()[0]}", file=sys.stderr)

    conn.close()
    print(f"Built {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
