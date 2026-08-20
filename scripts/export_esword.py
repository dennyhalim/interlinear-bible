#!/usr/bin/env python3
"""
Export the interlinear database to e-Sword's .bblx format -- a SQLite
database with Details + Bible tables.

Format verified against a real, working generator
(github.com/buric/e-sword-bblx-generator, fetched directly) and
confirmed Strong's tag convention from BibleSupport.com's official
MySword/e-Sword markup documentation (e-Sword shares the same
<WH####>/<WG####> inline tag syntax as MySword):
  - Details table: (Description, Abbreviation, Information, Version,
    Font, RightToLeft, OT, NT, Apocrypha, Strong)
  - Bible table: (Book INT, Chapter INT, Verse INT, Scripture TEXT),
    Book numbered 1-66 in canonical order
  - Version=4 in Details indicates HTML-formatted Scripture content

IMPORTANT CAVEAT, stated plainly rather than glossed over: e-Sword's
Windows desktop version does NOT have a first-class interlinear
display feature the way MySword does -- the <Q>/<X>/<T> interlinear
block tags are a MySword-specific extension, not part of e-Sword's own
tag set. Real e-Sword "interlinear" modules found during research were
built as HTML tables crammed into the Scripture field (primarily for
the Mac/iOS version, which renders arbitrary HTML), not as a
standardized cross-app feature. Given that:
  --layout plain        : recommended default. Gloss text with inline
                           <WH####>/<WG####> tags -- this is standard,
                           reliable e-Sword behavior (Strong's-tagged
                           Bible module, works identically to how real
                           e-Sword Strong's Bibles are built).
  --layout interlinear   : renders as an HTML <table> per verse (word /
                           translit / gloss / Strong's per column) in
                           the Scripture field, matching the workaround
                           approach real e-Sword interlinear modules
                           have used. Only reliably renders on e-Sword
                           versions with HTML support (Mac/iOS); on
                           Windows e-Sword this will likely display as
                           raw/broken HTML rather than a formatted
                           table. Use --layout plain for guaranteed
                           cross-platform compatibility.

Usage:
  python3 scripts/export_esword.py \
    --db output/interlinear.sqlite \
    --out output/exports/id_plain.bblx \
    --language id --layout plain \
    --description "Vox Antiqua (Indonesian, Strong's tagged)" \
    --abbreviation VAI
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec
from export_mysword import escape_html, strongs_tags


def render_verse_plain(words):
    """Same approach as MySword's plain layout: gloss text with inline
    Strong's tags. This is the format real e-Sword Strong's Bibles use."""
    parts = []
    for w in words:
        gloss = escape_html(w["gloss"] or "")
        parts.append(f"{gloss}{strongs_tags(w)}")
    return " ".join(parts)


def render_verse_interlinear_html_table(words):
    """HTML table per verse: one column per word, four rows (original,
    translit, gloss, Strong's). This is a workaround approach (not a
    first-class e-Sword feature -- see module docstring), included
    because it's genuinely how real e-Sword interlinear modules have
    been built, and renders correctly at least on HTML-capable e-Sword
    versions (Mac/iOS)."""
    cells_orig, cells_translit, cells_gloss, cells_strongs = [], [], [], []
    for w in words:
        surface = escape_html(w["surface"] or "")
        translit = escape_html(w["translit"] or "")
        gloss = escape_html(w["gloss"] or "")
        strongs_display = escape_html(w["strongs"] or "")
        cells_orig.append(f"<td>{surface}</td>")
        cells_translit.append(f"<td><i>{translit}</i></td>")
        cells_gloss.append(f"<td>{gloss}</td>")
        cells_strongs.append(f"<td><small>{strongs_display}{strongs_tags(w)}</small></td>")

    return (
        "<table border='1'>"
        f"<tr>{''.join(cells_orig)}</tr>"
        f"<tr>{''.join(cells_translit)}</tr>"
        f"<tr>{''.join(cells_gloss)}</tr>"
        f"<tr>{''.join(cells_strongs)}</tr>"
        "</table>"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--layout", choices=["interlinear", "plain"], default="plain",
                     help="'plain' (default) is standard e-Sword behavior everywhere. "
                          "'interlinear' renders as an HTML table, reliable only on "
                          "HTML-capable e-Sword versions (Mac/iOS) -- see module docstring.")
    ap.add_argument("--description", required=True)
    ap.add_argument("--abbreviation", required=True)
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    src_conn = sqlite3.connect(args.db)
    out_conn = sqlite3.connect(args.out)
    out_cur = out_conn.cursor()

    out_cur.execute(
        "CREATE TABLE Details (Description NVARCHAR(250), Abbreviation NVARCHAR(50), "
        "Information TEXT, Version INT, Font NVARCHAR(50), RightToLeft BOOL, "
        "OT BOOL, NT BOOL, Apocrypha BOOL, Strong BOOL)"
    )
    out_cur.execute("CREATE TABLE Bible (Book INT, Chapter INT, Verse INT, Scripture TEXT)")

    books = bec.get_books(src_conn)
    if args.book:
        books = [b for b in books if b[1] == args.book.upper()]
        if not books:
            print(f"ERROR: book '{args.book}' not found", file=sys.stderr)
            sys.exit(1)

    has_ot = any(b[3] == "OT" for b in books)
    has_nt = any(b[3] == "NT" for b in books)

    verse_count = 0
    for book_id, code, name, testament, ordinal in books:
        if code not in bec.CANONICAL_BOOK_ORDER:
            print(f"WARN: book '{code}' not in canonical 66-book order, skipping", file=sys.stderr)
            continue
        book_number = bec.CANONICAL_BOOK_ORDER.index(code) + 1

        verses = bec.get_verses_for_book(src_conn, book_id)
        for verse_id, chapter, verse in verses:
            words = bec.get_words_for_verse(src_conn, verse_id, args.language)
            if not words:
                continue
            scripture = (
                render_verse_interlinear_html_table(words) if args.layout == "interlinear"
                else render_verse_plain(words)
            )
            out_cur.execute(
                "INSERT INTO Bible (Book, Chapter, Verse, Scripture) VALUES (?,?,?,?)",
                (book_number, chapter, verse, scripture),
            )
            verse_count += 1

        print(f"{code}: done", file=sys.stderr)

    out_cur.execute(
        "INSERT INTO Details (Description, Abbreviation, Information, Version, Font, "
        "RightToLeft, OT, NT, Apocrypha, Strong) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            args.description,
            args.abbreviation,
            f"Generated by interlinear-bible export_esword.py. Language: {args.language}. Layout: {args.layout}.",
            4,
            "DEFAULT",
            0,
            1 if has_ot else 0,
            1 if has_nt else 0,
            0,
            1,
        ),
    )

    out_cur.execute("CREATE INDEX BookChapterVerseIndex ON Bible (Book, Chapter, Verse)")
    out_conn.commit()

    print(f"\nWrote {verse_count} verses -> {args.out}", file=sys.stderr)
    if args.layout == "interlinear":
        print("NOTE: interlinear layout uses HTML tables -- verify rendering on your target e-Sword version before distributing (see module docstring).", file=sys.stderr)

    src_conn.close()
    out_conn.close()


if __name__ == "__main__":
    main()
