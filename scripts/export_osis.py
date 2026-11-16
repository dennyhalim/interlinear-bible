#!/usr/bin/env python3
"""
Export the interlinear database to OSIS XML, the source format CrossWire's SWORD toolchain compiles into a
SWORD module (osis2mod).

This script produces OSIS XML SOURCE only. Turning it into an installable module needs CrossWire's osis2mod:
  1. Install the SWORD tools (e.g. `apt install sword-tools`, or build from git.crosswire.org/sword-tools)
  2. Run: osis2mod /path/to/module/output thisfile.xml -v KJV
  3. Write a conf file describing the module (DataPath, Versification, Lang, ...)
Not tested with osis2mod here.

Details:
  - Strong's numbers: <w lemma="strong:H1234">word</w>, numbers without zero padding. STEPBible's H9000+
    grammatical-particle codes are not real Strong's entries and are left out.
  - Book ids are the standard OSIS names (Gen, Exod, Ps, Matt, John, ...), not this project's 3-letter codes.
  - The database uses standard English verse numbering (Psalm titles are verse 0), so osisIDs are written as
    stored and no versification remapping is needed. A title becomes <title type="psalm" canonical="true">
    in front of verse 1.
  - Verses use the container form <verse osisID="Gen.1.1">...</verse>.

Layouts:
  --layout interlinear (default) : <w lemma morph>gloss</w> per word, the gloss as the visible text
  --layout plain                 : the same
  --layout natural               : the stage 4 sentence; each word followed by its {G746} tag becomes
                                   <w lemma="strong:G746">word</w>; a paragraph start (¶) is
                                   <milestone type="x-p" marker="¶"/>; poetry lines (" | ") are separated by <lb/>.
                                   Verses without a translation are skipped.
Morph attributes: Greek Robinson codes as robinson:<code>, Hebrew codes as oshm:<code> (the prefix for Hebrew is
unverified).

Usage:
  python3 scripts/export_osis.py --db output/interlinear.sqlite --out output/exports/id_interlinear.osis.xml \
    --language id --layout interlinear --title "Vox Antiqua Interlinear (Indonesian)" --lang id
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec

# Standard OSIS book names in the same canonical order as bec.CANONICAL_BOOK_ORDER.
OSIS_BOOKS = [
    "Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth", "1Sam", "2Sam", "1Kgs", "2Kgs", "1Chr", "2Chr",
    "Ezra", "Neh", "Esth", "Job", "Ps", "Prov", "Eccl", "Song", "Isa", "Jer", "Lam", "Ezek", "Dan", "Hos", "Joel",
    "Amos", "Obad", "Jonah", "Mic", "Nah", "Hab", "Zeph", "Hag", "Zech", "Mal",
    "Matt", "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil", "Col", "1Thess", "2Thess",
    "1Tim", "2Tim", "Titus", "Phlm", "Heb", "Jas", "1Pet", "2Pet", "1John", "2John", "3John", "Jude", "Rev",
]
TAG = re.compile(r"\{([HG])(\d+)\}")
WORD_TAGS = re.compile(r"([\w'\u2019-]+)((?:\{[HG]\d+\})+)")   # a word immediately followed by its tag(s)
BAR = re.compile(r"\s*\|\s*")                                    # poetry line separator from the stage 4 prompt


def osis_book(code):
    return OSIS_BOOKS[bec.CANONICAL_BOOK_ORDER.index(code)]


def escape_xml(text):
    if text is None:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def strong_lemma_attr(word):
    """'strong:H1234 strong:G746' for the word's genuine Strong's numbers (unpadded, no H9000+ particles)."""
    values = []
    for part in word["strongs_parts"]:
        if not part:
            continue
        letter = "H" if part.startswith("H") else "G"
        digits = "".join(ch for ch in part[1:] if ch.isdigit())
        if not digits or (letter == "H" and int(digits) >= 9000):
            continue
        values.append(f"strong:{letter}{int(digits)}")
    return " ".join(values)


def morph_attr(word):
    code = (word["morph_code"] or "").strip()
    if not code:
        return ""
    return escape_xml(("robinson:" if word["language"] == "greek" else "oshm:") + code)


def render_words(words):
    """<w> elements: the gloss is the visible text; Strong's and morphology stay attached as attributes."""
    parts = []
    for w in words:
        attrs = []
        lemma, morph = strong_lemma_attr(w), morph_attr(w)
        if lemma:
            attrs.append(f'lemma="{lemma}"')
        if morph:
            attrs.append(f'morph="{morph}"')
        parts.append(f"<w{(' ' + ' '.join(attrs)) if attrs else ''}>{escape_xml(w['gloss'] or '')}</w>")
    return " ".join(parts)


def render_natural(text):
    """'Pada mula{G746} | ...' -> '<w lemma="strong:G746">mula</w><lb/>...'. A tag that does not follow a word is dropped."""
    def word(m):
        lemma = " ".join(f"strong:{l}{int(n)}" for l, n in TAG.findall(m.group(2)))
        return f'<w lemma="{lemma}">{m.group(1)}</w>'
    text = WORD_TAGS.sub(word, escape_xml(text))
    return BAR.sub("<lb/>", TAG.sub("", text))


def title_text(words):
    return " ".join(w["gloss"] for w in words if w["gloss"] and w["gloss"] != "-" and not w["gloss"].startswith("("))


def render_title(conn, verse_id, args):
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=False)
        text = BAR.sub(" ", (text or "").strip().lstrip("\u00b6").strip())
    else:
        text = title_text(bec.get_words_for_verse(conn, verse_id, args.language))
    return f'<title type="psalm" canonical="true">{escape_xml(text)}</title>' if text else None


def render_verse(conn, verse_id, args):
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=True)
        if not text:
            return None
        text = text.strip()
        body = render_natural(text.lstrip("\u00b6").strip())
        return ('<milestone type="x-p" marker="\u00b6"/>' if text.startswith("\u00b6") else "") + body
    words = bec.get_words_for_verse(conn, verse_id, args.language)
    return render_words(words) if words else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--layout", choices=["interlinear", "plain", "natural"], default="interlinear")
    ap.add_argument("--title", required=True)
    ap.add_argument("--lang", default="id", help="OSIS/ISO language code for the osisText xml:lang attribute")
    ap.add_argument("--work-id", default="InterlinearBible", help="OSIS work identifier (no spaces)")
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    args = ap.parse_args()

    if not args.db.is_file() or args.db.stat().st_size == 0:
        print(f"ERROR: database missing or empty: {args.db.resolve()}", file=sys.stderr)
        sys.exit(1)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    books = bec.get_books(conn)
    if args.book:
        books = [b for b in books if b[1] == args.book.upper()]
        if not books:
            print(f"ERROR: book '{args.book}' not found", file=sys.stderr)
            sys.exit(1)

    wid, lang = escape_xml(args.work_id), escape_xml(args.lang)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osis xmlns="http://www.bibletechnologies.net/2003/OSIS/namespace" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xsi:schemaLocation="http://www.bibletechnologies.net/2003/OSIS/namespace '
        'http://www.bibletechnologies.net/osisCore.2.1.1.xsd">',
        f'<osisText osisIDWork="{wid}" osisRefWork="Bible" xml:lang="{lang}">',
        "<header>",
        f'<work osisWork="{wid}">',
        f"<title>{escape_xml(args.title)}</title>",
        f"<language>{lang}</language>",
        "<description>Generated by interlinear-bible export_osis.py. "
        f"Language: {escape_xml(args.language)}. Layout: {escape_xml(args.layout)}. "
        "OSIS XML source; compile with osis2mod to get a SWORD module.</description>",
        "</work>",
        "</header>",
    ]

    verse_count, skipped, chapters = 0, 0, set()
    for book_id, code, name, testament, ordinal in books:
        if code not in bec.CANONICAL_BOOK_ORDER:
            print(f"WARN: book '{code}' not in canonical 66-book order, skipping", file=sys.stderr)
            continue
        osis = osis_book(code)
        lines.append(f'<div type="book" osisID="{osis}">')
        titles, book_verses, book_chapters = {}, 0, set()
        for verse_id, chapter, verse in bec.get_verses_for_book(conn, book_id):
            if verse == 0:                       # title: folded into verse 1 below
                title = render_title(conn, verse_id, args)
                if title:
                    titles[chapter] = title
                continue
            content = render_verse(conn, verse_id, args)
            if content is None:
                skipped += 1
                continue
            if verse == 1 and chapter in titles:
                content = titles.pop(chapter) + content
            lines.append(f'<verse osisID="{osis}.{chapter}.{verse}">{content}</verse>')
            book_verses += 1
            book_chapters.add(chapter)
        lines.append("</div>")
        verse_count += book_verses
        chapters.update((code, c) for c in book_chapters)
        print(f"{code}: {len(book_chapters)} chapters, {book_verses} verses", file=sys.stderr)

    lines += ["</osisText>", "</osis>"]
    args.out.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWrote {len(chapters)} chapters, {verse_count} verses -> {args.out}", file=sys.stderr)
    if skipped:
        print(f"  {skipped} verses skipped (no content for layout '{args.layout}')", file=sys.stderr)
    print("This is OSIS XML SOURCE, not a finished SWORD module. Compile with osis2mod.", file=sys.stderr)
    conn.close()


if __name__ == "__main__":
    main()
