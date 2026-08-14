#!/usr/bin/env python3
"""
Export the interlinear database as human-readable per-book Markdown
files, suitable for uploading as NotebookLM sources.

Why per-book rather than one giant file: NotebookLM caps each source
at 500,000 words (or 200MB, whichever hits first). Even the largest
book here (Jeremiah, ~92,000 rendered words including transliteration/
Strong's/morphology/gloss) stays well under that per-book, so one file
per book is a safe granularity. Combining multiple books into fewer
files does NOT help and can push you over the limit -- a combined Old
Testament file alone comes to roughly 1.2 million words, well over the
cap; New Testament combined is roughly 570,000, also over. Per-book
files, uploaded as 66 separate sources split across two notebooks
(Old Testament: 39 books, New Testament: 27 books, both under
NotebookLM's 50-source free-tier cap), is the approach that actually
fits both limits.

Why Markdown rather than raw table dumps: NotebookLM's value is in
reading/summarizing/answering questions about *readable* content. A
raw CSV of word_id/strongs/morph_code columns is technically "text"
but doesn't read like anything -- this renders each verse as a real
interlinear block: reference, original-language text, transliteration,
per-word gloss, Strong's number, and morphology, in reading order.

Usage:
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --testament OT
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --book GEN
"""
import argparse
import sqlite3
import sys
from pathlib import Path


# Traditional thematic groupings. Verified against actual rendered word
# counts (see README) -- every group stays under NotebookLM's 500,000
# word cap, with Historical Books the tightest at ~433K (87% of the
# limit). If STEPBible/byztxt source data ever grows substantially,
# re-check this margin before relying on it.
BOOK_GROUPS = {
    "01_Torah": ["GEN", "EXO", "LEV", "NUM", "DEU"],
    "02_Historical_Books": ["JOS", "JDG", "RUT", "1SA", "2SA", "1KI", "2KI", "1CH", "2CH", "EZR", "NEH", "EST"],
    "03_Wisdom_and_Poetry": ["JOB", "PSA", "PRO", "ECC", "SNG"],
    "04_Major_Prophets": ["ISA", "JER", "LAM", "EZK", "DAN"],
    "05_Minor_Prophets": ["HOS", "JOL", "AMO", "OBA", "JON", "MIC", "NAM", "HAB", "ZEP", "HAG", "ZEC", "MAL"],
    "06_Gospels_and_Acts": ["MAT", "MRK", "LUK", "JHN", "ACT"],
    "07_Pauline_Epistles": ["ROM", "1CO", "2CO", "GAL", "EPH", "PHP", "COL", "1TH", "2TH", "1TI", "2TI", "TIT", "PHM"],
    "08_General_Epistles_and_Revelation": ["HEB", "JAS", "1PE", "2PE", "1JN", "2JN", "3JN", "JUD", "REV"],
}


def get_books(cur, testament=None, book_code=None):
    query = "SELECT book_id, code, name, testament, ordinal FROM book"
    params = []
    conditions = []
    if testament:
        conditions.append("testament = ?")
        params.append(testament)
    if book_code:
        conditions.append("code = ?")
        params.append(book_code)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY ordinal"
    cur.execute(query, params)
    return cur.fetchall()


def get_verses(cur, book_id):
    cur.execute(
        "SELECT verse_id, chapter, verse FROM verse WHERE book_id = ? ORDER BY chapter, verse",
        (book_id,),
    )
    return cur.fetchall()


def get_words(cur, verse_id):
    cur.execute(
        """
        SELECT w.position, w.language, w.surface, w.translit, w.strongs,
               w.morph_code, w.gloss_source, ag.gloss as ai_gloss,
               lx.gloss as lexicon_gloss
        FROM word w
        LEFT JOIN ai_gloss ag ON ag.word_id = w.word_id
        LEFT JOIN lexicon_entry lx ON lx.dstrong = w.strongs OR lx.estrong = w.strongs
        WHERE w.verse_id = ?
        ORDER BY w.position
        """,
        (verse_id,),
    )
    return cur.fetchall()


def render_book(cur, book_id, code, name, testament):
    lines = [f"# {name} ({'Old Testament, Masoretic Text' if testament == 'OT' else 'New Testament, Textus Receptus'})", ""]

    verses = get_verses(cur, book_id)
    current_chapter = None

    for verse_id, chapter, verse in verses:
        if chapter != current_chapter:
            lines.append(f"\n## Chapter {chapter}\n")
            current_chapter = chapter

        words = get_words(cur, verse_id)
        surface_line = " ".join(w[2] for w in words)

        lines.append(f"**{code} {chapter}:{verse}** {surface_line}")

        # Compact per-word gloss line instead of a Markdown table -- far
        # fewer tokens for the same information, and reads more like
        # a traditional printed interlinear's word-by-word gloss row.
        word_parts = []
        for pos, lang, surface, translit, strongs, morph, gloss_source, ai_gloss, lexicon_gloss in words:
            gloss = ai_gloss or gloss_source or lexicon_gloss or ""
            strongs_disp = strongs or ""
            word_parts.append(f"{surface}={translit}[{strongs_disp},{morph}]:\"{gloss}\"")
        lines.append("  " + " | ".join(word_parts))
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--testament", choices=["OT", "NT"], help="Export only one testament")
    ap.add_argument("--book", help="Export only one book (canonical 3-letter code, e.g. GEN, JHN)")
    ap.add_argument(
        "--combine-testament", action="store_true",
        help="Write one combined file per testament instead of one per book. "
             "NOTE: does NOT help with NotebookLM's 500,000-word source cap -- "
             "a combined testament file is well OVER that limit (OT ~1.2M "
             "words, NT ~570K words). This flag is only useful for other "
             "purposes (e.g. feeding a single file to a different tool with "
             "no such limit). For NotebookLM, use --group instead (see below) "
             "or the default one-file-per-book output split across two "
             "notebooks."
    )
    ap.add_argument(
        "--group", action="store_true",
        help="Write one file per traditional thematic group (Torah, "
             "Historical Books, Wisdom & Poetry, Major/Minor Prophets, "
             "Gospels & Acts, Pauline Epistles, General Epistles & "
             "Revelation) -- 8 files total, each verified to stay under "
             "NotebookLM's 500,000-word cap. Recommended over the default "
             "one-file-per-book output when uploading to a single notebook, "
             "since 8 sources is well under the 50-source cap too."
    )
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    books = get_books(cur, testament=args.testament, book_code=args.book)
    if not books:
        print("No matching books found", file=sys.stderr)
        sys.exit(1)

    if args.group:
        code_to_book = {code: (book_id, code, name, testament, ordinal) for book_id, code, name, testament, ordinal in books}
        for group_name, book_codes in BOOK_GROUPS.items():
            parts = []
            missing = []
            for code in book_codes:
                if code not in code_to_book:
                    missing.append(code)
                    continue
                book_id, code_, name, testament, ordinal = code_to_book[code]
                parts.append(render_book(cur, book_id, code_, name, testament))
            if missing:
                print(f"WARN: group '{group_name}' missing books (excluded by --testament/--book filter?): {missing}", file=sys.stderr)
            if not parts:
                continue
            combined = "\n\n---\n\n".join(parts)
            out_path = args.out_dir / f"{group_name}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count = len(combined.split())
            print(f"{group_name}: {word_count} words ({len(parts)} books) -> {out_path}", file=sys.stderr)
            if word_count > 500_000:
                print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        conn.close()
        return

    if args.combine_testament:
        by_testament = {}
        for book_id, code, name, testament, ordinal in books:
            by_testament.setdefault(testament, []).append((book_id, code, name, ordinal))

        for testament, book_list in by_testament.items():
            parts = []
            for book_id, code, name, ordinal in sorted(book_list, key=lambda b: b[3]):
                parts.append(render_book(cur, book_id, code, name, testament))
            combined = "\n\n---\n\n".join(parts)
            label = "Old_Testament" if testament == "OT" else "New_Testament"
            out_path = args.out_dir / f"{label}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count = len(combined.split())
            print(f"{label}: {word_count} words -> {out_path}", file=sys.stderr)
            if word_count > 500_000:
                print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        conn.close()
        return

    for book_id, code, name, testament, ordinal in books:
        content = render_book(cur, book_id, code, name, testament)
        out_path = args.out_dir / f"{ordinal:02d}_{code}_{name.replace(' ', '_')}.md"
        out_path.write_text(content, encoding="utf-8")
        word_count = len(content.split())
        print(f"{code} ({name}): {word_count} words -> {out_path}", file=sys.stderr)
        if word_count > 500_000:
            print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()