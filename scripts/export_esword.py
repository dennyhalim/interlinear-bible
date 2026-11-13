#!/usr/bin/env python3
"""
Export the interlinear database to e-Sword's .bblx format -- a SQLite database with Details + Bible tables.

Format verified against a working generator (github.com/buric/e-sword-bblx-generator) and the Strong's tag
convention shared with MySword (<WH####> / <WG####>, numbers without zero padding):
  - Details: (Description, Abbreviation, Information, Version, Font, RightToLeft, OT, NT, Apocrypha, Strong)
  - Bible: (Book INT, Chapter INT, Verse INT, Scripture TEXT), Book numbered 1-66 in canonical order
  - Version=4 in Details indicates HTML-formatted Scripture content

IMPORTANT CAVEAT: e-Sword's Windows desktop version has no first-class interlinear display. The <Q>/<X>/<T>
tags are MySword-specific. Real e-Sword "interlinear" modules are HTML tables in the Scripture field, which render
reliably only on HTML-capable versions (Mac/iOS); Windows e-Sword likely shows raw HTML.

Layouts:
  --layout plain       (default) gloss text with a Strong's tag after each word; works everywhere
  --layout natural     the stage 4 sentence with its {G746} tags turned into <WG746>. A paragraph start is kept
                       as a literal "¶ " (no markup that Windows e-Sword might show raw); poetry lines (" | ")
                       are joined with a space, or <br> with --poetry br (HTML-capable versions only)
  --layout interlinear an HTML table per verse (word / translit / gloss / Strong's)
Psalm-style titles (verse 0 in the database) are put in front of verse 1 as plain text, since there is no verse 0.

Usage:
  python3 scripts/export_esword.py --db output/interlinear.sqlite --out output/exports/id_plain.bblx \
    --language id --layout plain --description "Vox Antiqua (Indonesian, Strong's tagged)" --abbreviation VAI
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec
from export_mysword import escape_html, strongs_tags, render_verse_natural, title_text, BAR


def render_verse_plain(words):
    """Gloss text with inline Strong's tags: how real e-Sword Strong's Bibles are built."""
    return " ".join(f"{escape_html(w['gloss'] or '')}{strongs_tags(w)}" for w in words)


def render_verse_interlinear_html_table(words):
    """HTML table per verse: one column per word, four rows (original, translit, gloss, Strong's)."""
    cells = {"orig": [], "translit": [], "gloss": [], "strongs": []}
    for w in words:
        cells["orig"].append(f"<td>{escape_html(w['surface'] or '')}</td>")
        cells["translit"].append(f"<td><i>{escape_html(w['translit'] or '')}</i></td>")
        cells["gloss"].append(f"<td>{escape_html(w['gloss'] or '')}</td>")
        cells["strongs"].append(f"<td><small>{escape_html(w['strongs'] or '')}{strongs_tags(w)}</small></td>")
    return ("<table border='1'>" + "".join(f"<tr>{''.join(cells[k])}</tr>" for k in ("orig", "translit", "gloss", "strongs"))
            + "</table>")


def render_title(conn, verse_id, args):
    """Verse 0 (a title) as plain text to put in front of verse 1, or None."""
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=False)
        text = BAR.sub(" ", (text or "").strip().lstrip("\u00b6").strip())
    else:
        text = title_text(bec.get_words_for_verse(conn, verse_id, args.language))
    return escape_html(text) if text else None


def render_verse(conn, verse_id, args):
    """Scripture text for one verse, or None when there is nothing to write for this layout."""
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=True)
        if not text:
            return None
        text = text.strip()
        body = render_verse_natural(text.lstrip("\u00b6").strip(), args.poetry)
        return ("\u00b6 " if text.startswith("\u00b6") else "") + body
    words = bec.get_words_for_verse(conn, verse_id, args.language)
    if not words:
        return None
    return render_verse_interlinear_html_table(words) if args.layout == "interlinear" else render_verse_plain(words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--layout", choices=["interlinear", "plain", "natural"], default="plain",
                    help="'plain' (default) works everywhere; 'interlinear' is an HTML table (Mac/iOS only)")
    ap.add_argument("--poetry", choices=["space", "br"], default="space",
                    help="natural layout: join poetry lines with a space (default) or <br> (HTML-capable versions)")
    ap.add_argument("--description", required=True)
    ap.add_argument("--abbreviation", required=True)
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    args = ap.parse_args()

    if not args.db.is_file() or args.db.stat().st_size == 0:
        print(f"ERROR: database missing or empty: {args.db.resolve()}", file=sys.stderr)
        sys.exit(1)
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

    verse_count, skipped, chapters = 0, 0, set()
    for book_id, code, name, testament, ordinal in books:
        if code not in bec.CANONICAL_BOOK_ORDER:
            print(f"WARN: book '{code}' not in canonical 66-book order, skipping", file=sys.stderr)
            continue
        book_number = bec.CANONICAL_BOOK_ORDER.index(code) + 1

        rows, titles = [], {}
        for verse_id, chapter, verse in bec.get_verses_for_book(src_conn, book_id):
            if verse == 0:                         # title: put in front of verse 1 below
                title = render_title(src_conn, verse_id, args)
                if title:
                    titles[chapter] = title
                continue
            scripture = render_verse(src_conn, verse_id, args)
            if scripture is None:
                skipped += 1
                continue
            if verse == 1 and chapter in titles:
                sep = "<br>" if args.layout == "interlinear" else " "
                scripture = titles.pop(chapter) + sep + scripture
            rows.append((book_number, chapter, verse, scripture))

        out_cur.executemany("INSERT INTO Bible (Book, Chapter, Verse, Scripture) VALUES (?,?,?,?)", rows)
        verse_count += len(rows)
        chapters.update((r[0], r[1]) for r in rows)
        print(f"{code}: {len({r[1] for r in rows})} chapters, {len(rows)} verses", file=sys.stderr)

    out_cur.execute(
        "INSERT INTO Details (Description, Abbreviation, Information, Version, Font, "
        "RightToLeft, OT, NT, Apocrypha, Strong) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (args.description, args.abbreviation,
         f"Generated by interlinear-bible export_esword.py. Language: {args.language}. Layout: {args.layout}.",
         4, "DEFAULT", 0, 1 if has_ot else 0, 1 if has_nt else 0, 0, 1),
    )
    out_cur.execute("CREATE INDEX BookChapterVerseIndex ON Bible (Book, Chapter, Verse)")
    out_conn.commit()

    print(f"\nWrote {len(chapters)} chapters, {verse_count} verses -> {args.out}", file=sys.stderr)
    if skipped:
        print(f"  {skipped} verses skipped (no content for layout '{args.layout}')", file=sys.stderr)
    if args.layout == "interlinear":
        print("NOTE: interlinear layout uses HTML tables -- verify rendering on your target e-Sword version before distributing.", file=sys.stderr)

    src_conn.close()
    out_conn.close()


if __name__ == "__main__":
    main()