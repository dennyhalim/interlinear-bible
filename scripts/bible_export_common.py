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


def get_words_for_verse(conn, verse_id, language):
    """Returns a list of dicts, one per word, with everything an
    exporter needs: surface text, transliteration, Strong's (both the
    single head value and the full prefix/stem breakdown when
    available), morphology, and the resolved primary gloss text plus
    which source it came from."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT w.word_id, w.position, w.language, w.surface, w.translit,
               w.strongs, w.strongs_parts, w.morph_code, w.gloss_source,
               lx.gloss as lexicon_gloss_en,
               lgt.gloss as lexicon_gloss_target,
               ag.gloss as ai_gloss
        FROM word w
        LEFT JOIN lexicon_entry lx ON lx.dstrong = w.strongs OR lx.estrong = w.strongs
        LEFT JOIN lexicon_gloss_translation lgt
            ON lgt.lexicon_id = lx.lexicon_id AND lgt.language = ?
        LEFT JOIN ai_gloss ag ON ag.word_id = w.word_id
        WHERE w.verse_id = ?
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
            gloss, gloss_source_label = lexicon_gloss_target, "lexicon_translated"
        elif lexicon_gloss_en:
            gloss, gloss_source_label = lexicon_gloss_en, "lexicon_en"
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
