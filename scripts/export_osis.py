#!/usr/bin/env python3
"""
Export the interlinear database to OSIS XML, the source format
CrossWire's SWORD project toolchain compiles into an actual SWORD
module.

IMPORTANT, stated plainly: this script produces OSIS XML SOURCE only.
It does NOT produce a finished SWORD module. Turning this XML into an
actual installable module requires CrossWire's own compiled toolchain
(osis2mod for verse-keyed texts), which is a separate C++ program, not
something reproducible in Python here. After generating the XML:
  1. Install the SWORD tools (e.g. `apt install sword-tools` on
     Debian/Ubuntu, or build from git.crosswire.org/sword-tools)
  2. Run: osis2mod /path/to/module/output thisfile.xml -v KJV
     (or the appropriate -v versification scheme)
  3. Write a conf file describing the module (DataPath, Versification,
     Lang, etc.) per CrossWire's module conf format

Format verified directly against CrossWire's own mailing list and wiki
documentation (not a third-party guide): Strong's numbers are tagged
via <w lemma="strong:H1234">word</w>, matching real published SWORD
modules (confirmed against an actual ESV module excerpt). Verse
structure uses <verse osisID="Book.Chapter.Verse">...</verse> container
form (opening/closing tags wrapping the verse's content), which is
simpler and valid per the OSIS schema for non-overlapping verse-per-
verse content.

Since OSIS/SWORD's own versification systems are English-verse-number
based (KJV, NRSV, etc.) and this project's underlying Hebrew source
uses its own (TAHOT) numbering for the ~295 OT passages that differ
(see versification_mapping, e.g. Malachi 3:19-24 = English 4:1-6),
this exporter uses the STANDARD ENGLISH reference for each verse's
osisID by default (resolving through versification_mapping), so the
resulting module aligns correctly with SWORD's expected versification
schemes. Use --no-remap to instead emit verses under their raw
source-numbering osisID if you specifically want that instead.

Usage:
  python3 scripts/export_osis.py \
    --db output/interlinear.sqlite \
    --out output/exports/id_interlinear.osis.xml \
    --language id --layout interlinear \
    --title "Vox Antiqua Interlinear (Indonesian)" \
    --lang id
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec


def escape_xml(text):
    if text is None:
        return ""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def strong_lemma_attr(word):
    """OSIS lemma attribute value, space-separated for multiple Strong's
    (e.g. a Hebrew word with both a prefix and stem Strong's number),
    in the "strong:H1234" form confirmed from a real published OSIS
    module excerpt. Same pseudo-code filtering as the MySword/e-Sword
    exporters -- STEPBible's H9000+ grammatical-particle codes aren't
    real Strong's dictionary entries and are excluded."""
    values = []
    for part in word["strongs_parts"]:
        if not part:
            continue
        letter = "H" if part.startswith("H") else "G"
        digits = "".join(ch for ch in part[1:] if ch.isdigit())
        if not digits:
            continue
        if letter == "H" and int(digits) >= 9000:
            continue
        values.append(f"strong:{letter}{digits}")
    return " ".join(values)


def morph_attr(word):
    if not word["morph_code"]:
        return ""
    segments = word["morph_code"].split("/")
    return escape_xml(segments[-1] if segments else word["morph_code"])


def render_words_interlinear(words):
    """<w> elements: the gloss (target-language reading) is the visible
    text content, with lemma/morph as queryable attributes carrying the
    Strong's number(s) and morphology. OSIS doesn't have a dedicated
    "interlinear gloss" element the way MySword's <T>/<X> tags do; this
    keeps the running text readable (the translation) while the
    original-language/Strong's/morphology data stays attached as
    attributes on each <w>, which is how real interlinear-tagged OSIS
    Bibles handle this rather than doubling every word inline."""
    parts = []
    for w in words:
        lemma = strong_lemma_attr(w)
        morph = morph_attr(w)
        gloss = escape_xml(w["gloss"] or "")
        attrs = []
        if lemma:
            attrs.append(f'lemma="{lemma}"')
        if morph:
            attrs.append(f'morph="{morph}"')
        attr_str = (" " + " ".join(attrs)) if attrs else ""
        parts.append(f"<w{attr_str}>{gloss}</w>")
    return " ".join(parts)


def render_words_plain(words):
    """Currently identical to render_words_interlinear -- OSIS's <w>
    element IS inherently the Strong's-tagged representation, so
    "plain" and "interlinear" don't diverge as structurally as they do
    for MySword/e-Sword's tag sets. Kept as a separate function so the
    two layouts can diverge later (e.g. adding <foreign> spans for the
    original-language surface text) without disturbing callers."""
    return render_words_interlinear(words)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--layout", choices=["interlinear", "plain"], default="interlinear")
    ap.add_argument("--title", required=True)
    ap.add_argument("--lang", default="id", help="OSIS/ISO language code for the osisText xml:lang attribute")
    ap.add_argument("--work-id", default="InterlinearBible", help="OSIS work identifier (no spaces)")
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    ap.add_argument("--no-remap", action="store_true",
                     help="Emit verses under raw source (Hebrew/Greek) numbering instead of resolving through versification_mapping to standard English references")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    books = bec.get_books(conn)
    if args.book:
        books = [b for b in books if b[1] == args.book.upper()]
        if not books:
            print(f"ERROR: book '{args.book}' not found", file=sys.stderr)
            sys.exit(1)

    render_fn = render_words_interlinear if args.layout == "interlinear" else render_words_plain

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<osis xmlns="http://www.bibletechnologies.net/2003/OSIS/namespace" '
                  'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
                  'xsi:schemaLocation="http://www.bibletechnologies.net/2003/OSIS/namespace '
                  'http://www.bibletechnologies.net/osisCore.2.1.1.xsd">')
    lines.append(f'<osisText osisIDWork="{escape_xml(args.work_id)}" osisRefWork="Bible" xml:lang="{escape_xml(args.lang)}">')
    lines.append('<header>')
    lines.append('<work osisWork="{}">'.format(escape_xml(args.work_id)))
    lines.append(f'<title>{escape_xml(args.title)}</title>')
    lines.append(f'<language>{escape_xml(args.lang)}</language>')
    lines.append('<description>Generated by interlinear-bible export_osis.py. '
                  f'Language: {escape_xml(args.language)}. Layout: {escape_xml(args.layout)}. '
                  'Requires osis2mod (CrossWire SWORD tools) to compile into an installable module -- '
                  'this file is OSIS XML source, not a finished module.</description>')
    lines.append('</work>')
    lines.append('</header>')

    verse_count = 0
    remap_gaps = []  # verses whose standard reference exists in versification_mapping but has no source content
    for book_id, code, name, testament, ordinal in books:
        lines.append(f'<div type="book" osisID="{code}">')
        verses = bec.get_verses_for_book(conn, book_id)
        for verse_id, chapter, verse in verses:
            words = bec.get_words_for_verse(conn, verse_id, args.language)
            if not words:
                continue

            if args.no_remap:
                disp_chapter, disp_verse = chapter, verse
            else:
                cur = conn.cursor()
                cur.execute(
                    "SELECT standard_chapter, standard_verse FROM versification_mapping "
                    "WHERE book_code = ? AND hebrew_chapter = ? AND hebrew_verse = ? LIMIT 1",
                    (code, chapter, verse),
                )
                mapping = cur.fetchone()
                if mapping and mapping[0] is not None and mapping[1] is not None:
                    disp_chapter, disp_verse = mapping
                else:
                    disp_chapter, disp_verse = chapter, verse

            osis_id = f"{code}.{disp_chapter}.{disp_verse}"
            content = render_fn(words)
            lines.append(f'<verse osisID="{osis_id}">{content}</verse>')
            verse_count += 1

        # Report standard references that versification_mapping knows
        # about for this book but which have no corresponding source
        # verse content at all -- e.g. Malachi 4:1-6 (Hebrew 3:19-24):
        # the mapping correctly describes how the NUMBERING translates,
        # but TAHOT's underlying Hebrew text for Malachi genuinely ends
        # at 3:18, so there is no content to emit at that reference.
        # This is a real, pre-existing source-data gap (documented
        # elsewhere in this project), not something this exporter can
        # fix -- but it should be visible rather than silently missing.
        if not args.no_remap:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT standard_chapter, standard_verse, hebrew_chapter, hebrew_verse "
                "FROM versification_mapping WHERE book_code = ? AND standard_chapter IS NOT NULL",
                (code,),
            )
            for std_ch, std_v, heb_ch, heb_v in cur.fetchall():
                cur2 = conn.cursor()
                cur2.execute(
                    "SELECT 1 FROM verse v JOIN book b ON v.book_id = b.book_id "
                    "WHERE b.code = ? AND v.chapter = ? AND v.verse = ?",
                    (code, heb_ch, heb_v),
                )
                if not cur2.fetchone():
                    remap_gaps.append(f"{code} {std_ch}:{std_v} (would come from source {heb_ch}:{heb_v}, which has no content)")

        lines.append('</div>')
        print(f"{code}: done", file=sys.stderr)

    lines.append('</osisText>')
    lines.append('</osis>')

    args.out.write_text("\n".join(lines), encoding="utf-8")

    print(f"\nWrote {verse_count} verses -> {args.out}", file=sys.stderr)
    if remap_gaps:
        print(f"\nNOTE: {len(remap_gaps)} standard-numbered verses have no source content "
              f"(pre-existing TAHOT source gaps, not an exporter bug -- see README's "
              f"'OT versification mapping' section):", file=sys.stderr)
        for gap in remap_gaps:
            print(f"  {gap}", file=sys.stderr)
    print("This is OSIS XML SOURCE, not a finished SWORD module.", file=sys.stderr)
    print("Compile with osis2mod (CrossWire SWORD tools) to produce an installable module -- see module docstring.", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
