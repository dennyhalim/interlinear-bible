"""TEST STUB standing in for your real scripts/bible_export_common.py (same function names and dict keys)."""
import json, re, sqlite3

def first_sense(g):
    depth, out = 0, []
    for ch in (g or "").strip():
        if ch == "(": depth += 1
        elif ch == ")": depth = max(0, depth - 1)
        elif depth == 0 and ch in "/;,:": break
        out.append(ch)
    return "".join(out).strip()

def get_words_for_verse(conn, verse_id, language):
    rows = conn.execute("""SELECT w.word_id, w.position, w.language, w.surface, w.translit, w.strongs, w.strongs_parts,
        w.morph_code, ag.gloss, lgt.gloss FROM word w
        LEFT JOIN ai_gloss ag ON ag.word_id = w.word_id
        LEFT JOIN lexicon_entry le ON le.dstrong = w.strongs
        LEFT JOIN lexicon_gloss_translation lgt ON lgt.lexicon_id = le.lexicon_id AND lgt.language = ?
        WHERE w.verse_id = ? ORDER BY w.position""", (language, verse_id)).fetchall()
    return [{"word_id": wid, "position": pos, "language": lang, "surface": surf, "translit": tr, "strongs": st,
             "strongs_parts": json.loads(sp) if sp else ([st] if st else []), "morph_code": mc,
             "gloss": ai or first_sense(lex) or ""} for wid, pos, lang, surf, tr, st, sp, mc, ai, lex in rows]

def get_verse_translation(conn, verse_id, language, tagged=False):
    try:
        row = conn.execute("SELECT text FROM verse_translation WHERE verse_id = ? AND language = ?", (verse_id, language)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row: return None
    return row[0] if tagged else re.sub(r"\{[HG]\d+\}", "", row[0])
