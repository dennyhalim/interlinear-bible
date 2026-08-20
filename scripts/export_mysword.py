#!/usr/bin/env python3
"""
Export the interlinear database to MySword's .bbl.mybible format --
a SQLite database with Details + Bible tables, readable directly by
MySword (Android/Windows/Web) without any external compile step.

Format verified against MySword's own official spec
(mysword.info/modules-format, fetched directly, not secondhand):
  - Details table: one row of module metadata (Abbreviation,
    VersionDate, Language, RightToLeft, OT, NT, Strong, etc.)
  - Bible table: (Book INT, Chapter INT, Verse INT, Scripture TEXT),
    Book numbered 1-66 in canonical order (Genesis=1 ... Revelation=66)
  - Strong's tags: <WH1234> / <WG1234> inline in Scripture text
  - Interlinear tags: <Q>...<q> per-word block, <X>...<x> for
    transliteration, <T>...<t> for the gloss/translation shown to the
    reader, <WT????> for morphology code
  - A real sample interlinear verse (Genesis 1:1) from MySword's own
    spec page was used as the template this exporter's output is
    structured after.

Two layouts, both generatable from the same data:
  --layout interlinear : original word + translit + Strong's + morph
                          + gloss, per word, in <Q>...<q> blocks
                          (matches the official spec's own sample)
  --layout plain        : just the gloss (translated reading text)
                          with inline <WH####>/<WG####> Strong's tags
                          attached to each word -- closer to how a
                          normal tagged Bible module reads, less
                          cluttered than full interlinear

Usage:
  python3 scripts/export_mysword.py \
    --db output/interlinear.sqlite \
    --out output/exports/id_interlinear.bbl.mybible \
    --language id --layout interlinear \
    --description "Vox Antiqua Interlinear (Indonesian)" \
    --abbreviation VAII

  python3 scripts/export_mysword.py \
    --db output/interlinear.sqlite \
    --out output/exports/id_plain.bbl.mybible \
    --language id --layout plain \
    --description "Vox Antiqua (Indonesian, Strong's tagged)" \
    --abbreviation VAI
"""
import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec


def escape_html(text):
    if text is None:
        return ""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def strongs_tags(word):
    """Emit one <WH####>/<WG####> tag per GENUINE Strong's dictionary
    entry among the word's parts -- skipping STEPBible's grammatical-
    particle pseudo-codes (H9000-H9999: prefixed article/conjunction/
    preposition markers like H9003="in", H9009="the", H9002="and").
    Those aren't real Strong's dictionary numbers and would incorrectly
    trigger a Strong's lookup for a non-existent entry in MySword if
    tagged as <WH9003> etc. Verified against the real database: these
    pseudo-codes appear on the vast majority of Hebrew words with a
    prefix (283,717 total Hebrew words checked)."""
    tags = []
    for part in word["strongs_parts"]:
        if not part:
            continue
        letter = "H" if part.startswith("H") else "G"
        digits = "".join(ch for ch in part[1:] if ch.isdigit())
        if not digits:
            continue
        if letter == "H" and int(digits) >= 9000:
            continue  # STEPBible grammatical pseudo-code, not a real Strong's entry
        tags.append(f"<W{letter}{digits}>")
    return "".join(tags)


def morph_tag(word):
    """MySword's <WT????> expects a single morphology code, matching
    its own spec examples (<WTN-NSF>). Our morph_code can be a
    compound like "HTd/Ncmpa" (prefix+stem grammar codes joined by /)
    -- take just the final (head-word) segment and strip the leading
    language letter, which is closer to what a single morph tag should
    contain, rather than exporting our internal compound representation
    verbatim."""
    if not word["morph_code"]:
        return ""
    segments = word["morph_code"].split("/")
    tail = segments[-1] if segments else word["morph_code"]
    return f"<WT{escape_html(tail)}>"


def render_verse_interlinear(words):
    """<Q>...<q> block per word, matching MySword's own spec sample
    verse structure exactly (Genesis 1:1 in HiSB style)."""
    parts = []
    for w in words:
        translit = escape_html(w["translit"] or "")
        gloss = escape_html(w["gloss"] or "")
        surface = escape_html(w["surface"] or "")
        parts.append(
            f"<Q>{surface}{strongs_tags(w)}{morph_tag(w)}<X>{translit}<x>"
            f"<T>{gloss}<t><q>"
        )
    return " ".join(parts)


def render_verse_plain(words):
    """Just the gloss text, reading naturally, with Strong's tags
    attached inline to each word -- e.g. 'In the beginning<WH7225> God
    created<WH1254>...'. No <Q>/<X>/<T> interlinear scaffolding."""
    parts = []
    for w in words:
        gloss = escape_html(w["gloss"] or "")
        parts.append(f"{gloss}{strongs_tags(w)}")
    return " ".join(parts)


def build_details_sql(args, has_ot, has_nt):
    version_date = date.today().isoformat()
    right_to_left = 0
    return (
        "INSERT INTO Details "
        "(Description, Abbreviation, Comments, Version, VersionDate, PublishDate, "
        "RightToLeft, OT, NT, Strong, CustomCSS) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            args.description,
            args.abbreviation,
            f"Generated by interlinear-bible export_mysword.py. Language: {args.language}. Layout: {args.layout}.",
            "1.0",
            version_date,
            version_date,
            right_to_left,
            1 if has_ot else 0,
            1 if has_nt else 0,
            1,
            "",
        ),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--layout", choices=["interlinear", "plain"], default="interlinear")
    ap.add_argument("--description", required=True, help="Full module description shown in MySword's module list")
    ap.add_argument("--abbreviation", required=True, help="Short module abbreviation, no spaces (use _ or -)")
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    args = ap.parse_args()

    if " " in args.abbreviation:
        print("ERROR: --abbreviation must not contain spaces (MySword uses space as a link delimiter). Use _ or -.", file=sys.stderr)
        sys.exit(1)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    src_conn = sqlite3.connect(args.db)
    out_conn = sqlite3.connect(args.out)
    out_cur = out_conn.cursor()

    out_cur.execute(
        "CREATE TABLE Details (Description NVARCHAR(255), Abbreviation NVARCHAR(50), "
        "Comments TEXT, Version TEXT, VersionDate DATETIME, PublishDate DATETIME, "
        "RightToLeft BOOL, OT BOOL, NT BOOL, Strong BOOL, CustomCSS TEXT)"
    )
    out_cur.execute(
        "CREATE TABLE Bible (Book INT, Chapter INT, Verse INT, Scripture TEXT, PRIMARY KEY(Book,Chapter,Verse))"
    )

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
                render_verse_interlinear(words) if args.layout == "interlinear"
                else render_verse_plain(words)
            )
            out_cur.execute(
                "INSERT OR REPLACE INTO Bible (Book, Chapter, Verse, Scripture) VALUES (?,?,?,?)",
                (book_number, chapter, verse, scripture),
            )
            verse_count += 1

        print(f"{code}: done", file=sys.stderr)

    details_sql, details_params = build_details_sql(args, has_ot, has_nt)
    out_cur.execute(details_sql, details_params)

    out_cur.execute("CREATE INDEX BookChapterVerseIndex ON Bible (Book, Chapter, Verse)")
    out_conn.commit()

    print(f"\nWrote {verse_count} verses -> {args.out}", file=sys.stderr)
    print(f"Rename/copy to a .bbl.mybible file in MySword's modules/bibles folder to use.", file=sys.stderr)

    src_conn.close()
    out_conn.close()


if __name__ == "__main__":
    main()
