#!/usr/bin/env python3
"""
Shared verse/word data assembly used by all Bible-format exporters
(export_mysword.py, export_esword.py, export_osis.py). Keeping this
in one place means every exporter reads the database the same way and
applies the same gloss-priority fallback, rather than each script
re-deriving its own (possibly inconsistent) logic.

Gloss priority for the "primary reading text" (matches the same
fallback order used elsewhere in this project, e.g. the read API):
  AI contextual gloss (language-specific, context-aware)
  -> translated lexicon gloss (language-specific, dictionary-level)
  -> English lexicon gloss
  -> source dataset's own gloss (TAHOT's Hebrew gloss_source column)
"""
import json
import sqlite3
import re


# Canonical 66-book order, matching book.ordinal in the schema. Needed
# by exporters that require a specific numeric book ID convention
# (e.g. e-Sword's 1-66, or a format-specific numbering scheme) rather
# than just re-using our own book_id primary key, which isn't
# guaranteed to match any particular target format's expected range.
CANONICAL_BOOK_ORDER = [
    "GEN", "EXO", "LEV", "NUM", "DEU", "JOS", "JDG", "RUT", "1SA", "2SA",
    "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST", "JOB", "PSA", "PRO",
    "ECC", "SNG", "ISA", "JER", "LAM", "EZK", "DAN", "HOS", "JOL", "AMO",
    "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL",
    "MAT", "MRK", "LUK", "JHN", "ACT", "ROM", "1CO", "2CO", "GAL", "EPH",
    "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM", "HEB", "JAS",
    "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV",
]


def get_books(conn):
    cur = conn.cursor()
    cur.execute("SELECT book_id, code, name, testament, ordinal FROM book ORDER BY ordinal")
    return cur.fetchall()


def get_verses_for_book(conn, book_id):
    cur = conn.cursor()
    cur.execute(
        "SELECT verse_id, chapter, verse FROM verse WHERE book_id = ? ORDER BY chapter, verse",
        (book_id,),
    )
    return cur.fetchall()

def get_verse_translation(conn, verse_id, language, tagged=True):
    """Natural verse translation, or None. Stored with {G746}-style Strong's
    tags; tagged=False (default) strips them for plain reading."""
    try:
        row = conn.execute(
            "SELECT text FROM verse_translation WHERE verse_id = ? AND language = ?",
            (verse_id, language)).fetchone()
    except sqlite3.OperationalError:
        return None  # table not created yet
    if not row:
        return None
    return row[0] if tagged else re.sub(r"\{[HG]\d+\}", "", row[0])
    
def first_sense(gloss):
    """'the; this, who' -> 'the'; separators inside (...) are ignored."""
    depth, out = 0, []
    for ch in (gloss or "").strip():
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "/;,":
            break
        out.append(ch)
    return "".join(out).strip()
    
def get_words_for_verse(conn, verse_id, language):
    """Returns a list of dicts, one per word, with everything an
    exporter needs: surface text, transliteration, Strong's (both the
    single head value and the full prefix/stem breakdown when
    available), morphology, and the resolved primary gloss text plus
    which source it came from."""
    cur = conn.cursor()
    cur.execute(
        """
        WITH lx AS (
            SELECT w2.word_id AS wid, le.lexicon_id, le.gloss,
                   ROW_NUMBER() OVER (
                       PARTITION BY w2.word_id
                       ORDER BY (le.dstrong = w2.strongs) DESC, le.lexicon_id
                   ) AS rn
            FROM word w2
            JOIN lexicon_entry le ON le.dstrong = w2.strongs OR le.estrong = w2.strongs
            WHERE w2.verse_id = ?2
        )
        SELECT w.word_id, w.position, w.language, w.surface, w.translit,
               w.strongs, w.strongs_parts, w.morph_code, w.gloss_source,
               lx.gloss AS lexicon_gloss_en,
               lgt.gloss AS lexicon_gloss_target,
               ag.gloss AS ai_gloss
        FROM word w
        LEFT JOIN lx ON lx.wid = w.word_id AND lx.rn = 1
        LEFT JOIN lexicon_gloss_translation lgt
            ON lgt.lexicon_id = lx.lexicon_id AND lgt.language = ?1
        LEFT JOIN ai_gloss ag ON ag.word_id = w.word_id
        WHERE w.verse_id = ?2
        ORDER BY w.position
        """,
        (language, verse_id),
    )
    rows = cur.fetchall()

    words = []
    for (word_id, position, lang, surface, translit, strongs, strongs_parts_json,
         morph_code, gloss_source, lexicon_gloss_en, lexicon_gloss_target, ai_gloss) in rows:

        if ai_gloss:
            gloss, gloss_source_label = ai_gloss, "ai_contextual"
        elif lexicon_gloss_target:
            gloss, gloss_source_label = first_sense(lexicon_gloss_target), "lexicon_translated"
        elif lexicon_gloss_en:
            gloss, gloss_source_label = first_sense(lexicon_gloss_en), "lexicon_en"
        elif gloss_source:
            gloss, gloss_source_label = gloss_source, "source_dataset"
        else:
            gloss, gloss_source_label = "", "none"

        strongs_parts = json.loads(strongs_parts_json) if strongs_parts_json else ([strongs] if strongs else [])

        words.append({
            "word_id": word_id,
            "position": position,
            "language": lang,
            "surface": surface,
            "translit": translit,
            "strongs": strongs,
            "strongs_parts": strongs_parts,
            "morph_code": morph_code,
            "gloss": gloss,
            "gloss_source": gloss_source_label,
        })
    return words
