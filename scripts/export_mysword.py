#!/usr/bin/env python3
"""
Export the interlinear database to MySword's .bbl.mybible format -- a SQLite
database with Details + Bible tables, readable directly by MySword.

Format checked against MySword's own spec (mysword.info/modules-format):
  - Details: one row of metadata (Abbreviation, VersionDate, Language = 3 letter ISO code, RightToLeft, OT, NT, Strong, ...)
  - Bible: (Book INT, Chapter INT, Verse INT, Scripture TEXT), Book numbered 1-66 in canonical order
  - Strong's tags <WH1234> / <WG1234> (number without zero padding); morphology <WTCode> links to an RMAC
    dictionary, which only covers Greek, so Hebrew words get no <WT> tag
  - <Q>..<q> interlinear block, <X>..<x> transliteration, <T>..<t> gloss
  - <CM> is a paragraph END marker; <TS>..<Ts> is a title; no line-break tag is documented

Layouts:
  --layout interlinear : per word <Q>surface<WH..><WT..><X>translit<x><T>gloss<t><q>
  --layout plain       : the gloss text with a Strong's tag after each word
  --layout natural     : the stage 4 sentence, its {G746} tags turned into <WG746>. Verses that start a
                         paragraph (¶) end the previous verse with <CM>. Poetry lines (" | ") are joined with
                         <br> (HTML is supported but not recommended by the spec; untested in the app) or a
                         space (--poetry space). Verses without a translation are skipped.
Psalm-style titles (verse 0 in the database) are folded into verse 1 as <TS>title<Ts> (interlinear: as
blocks), since MySword has no verse 0.

Usage:
  python3 scripts/export_mysword.py --db output/interlinear.sqlite --out output/exports/id_interlinear.bbl.mybible \
    --language id --layout interlinear --description "Vox Antiqua Interlinear (Indonesian)" --abbreviation VAII
  python3 scripts/export_mysword.py --db ... --out output/exports/id_natural.bbl.mybible \
    --language id --layout natural --description "Vox Antiqua (Indonesian, natural)" --abbreviation VAN
"""
import argparse
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec

LANG3 = {"id": "ind", "en": "eng"}   # ISO 639-2 codes for Details.Language
TAG = re.compile(r"\{([HG])(\d+)\}")
BAR = re.compile(r"\s*\|\s*")        # poetry line separator written by the stage 4 prompt


def escape_html(text):
    if text is None:
        return ""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_verse_natural(text, poetry="br"):
    """'Pada mula{G746} | ...' -> 'Pada mula<WG746><br>...' (MySword Strong's tag, poetry line break)."""
    text = TAG.sub(r"<W\1\2>", escape_html(text))
    return BAR.sub("<br>" if poetry == "br" else " ", text)


def strongs_tags(word):
    """One <WH####>/<WG####> tag per GENUINE Strong's entry among the word's parts, numbers unpadded.
    STEPBible's H9000+ grammatical-particle pseudo-codes (prefixed article, preposition, conjunction) are not
    real Strong's entries and would link to nothing, so they are skipped."""
    tags = []
    for part in word["strongs_parts"]:
        if not part:
            continue
        letter = "H" if part.startswith("H") else "G"
        digits = "".join(ch for ch in part[1:] if ch.isdigit())
        if not digits:
            continue
        if letter == "H" and int(digits) >= 9000:
            continue
        tags.append(f"<W{letter}{int(digits)}>")
    return "".join(tags)


def morph_tag(word):
    """<WT...> links to MySword's RMAC dictionary, which only covers Greek (Robinson codes like N-VSN,
    V-PAI-3S, P-1AP). Hebrew codes (STEPBible TEHMC, e.g. HNcfsa) are not RMAC, so no tag for them."""
    code = (word["morph_code"] or "").strip().upper()
    if word["language"] != "greek" or not code:
        return ""
    return f"<WT{escape_html(code)}>"


def render_verse_interlinear(words):
    """<Q>...<q> block per word, as in MySword's own HiSB sample."""
    parts = []
    for w in words:
        translit = escape_html(w["translit"] or "")
        gloss = escape_html(w["gloss"] or "")
        surface = escape_html(w["surface"] or "")
        parts.append(f"<Q>{surface}{strongs_tags(w)}{morph_tag(w)}<X>{translit}<x><T>{gloss}<t><q>")
    return " ".join(parts)


def render_verse_plain(words):
    """Just the gloss text with the Strong's tags attached inline to each word."""
    return " ".join(f"{escape_html(w['gloss'] or '')}{strongs_tags(w)}" for w in words)


def title_text(words):
    return " ".join(w["gloss"] for w in words if w["gloss"] and w["gloss"] != "-" and not w["gloss"].startswith("("))


def render_title(conn, verse_id, args):
    """Verse 0 (a title) as it is folded into verse 1, or None."""
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=False)
        text = BAR.sub(" ", (text or "").strip().lstrip("\u00b6").strip())
        return f"<TS>{escape_html(text)}<Ts>" if text else None
    words = bec.get_words_for_verse(conn, verse_id, args.language)
    if not words:
        return None
    if args.layout == "interlinear":
        return render_verse_interlinear(words) + " "
    text = title_text(words)
    return f"<TS>{escape_html(text)}<Ts>" if text else None


def render_verse(conn, verse_id, args):
    """(scripture, starts_paragraph), or (None, False) when there is nothing to write for this verse."""
    if args.layout == "natural":
        text = bec.get_verse_translation(conn, verse_id, args.language, tagged=True)
        if not text:
            return None, False
        text = text.strip()
        starts = text.startswith("\u00b6")
        return render_verse_natural(text.lstrip("\u00b6").strip(), args.poetry), starts
    words = bec.get_words_for_verse(conn, verse_id, args.language)
    if not words:
        return None, False
    return (render_verse_interlinear(words) if args.layout == "interlinear" else render_verse_plain(words)), False


def build_details_sql(args, has_ot, has_nt):
    version_date = date.today().isoformat()
    return (
        "INSERT INTO Details "
        "(Description, Abbreviation, Comments, Version, VersionDate, PublishDate, Language, "
        "RightToLeft, OT, NT, Strong, CustomCSS) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            args.description,
            args.abbreviation,
            f"Generated by interlinear-bible export_mysword.py. Language: {args.language}. Layout: {args.layout}.",
            "1.0",
            version_date,
            version_date,
            LANG3.get(args.language, args.language),
            0,
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
    ap.add_argument("--layout", choices=["interlinear", "plain", "natural"], default="interlinear")
    ap.add_argument("--poetry", choices=["br", "space"], default="br",
                    help="natural layout: join poetry lines with <br> (default) or a space")
    ap.add_argument("--description", required=True, help="Full module description shown in MySword's module list")
    ap.add_argument("--abbreviation", required=True, help="Short module abbreviation, no spaces (use _ or -)")
    ap.add_argument("--book", help="Only export one book (canonical 3-letter code), for testing")
    args = ap.parse_args()

    if " " in args.abbreviation:
        print("ERROR: --abbreviation must not contain spaces (MySword uses space as a link delimiter). Use _ or -.", file=sys.stderr)
        sys.exit(1)
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
        "CREATE TABLE Details (Description NVARCHAR(255), Abbreviation NVARCHAR(50), "
        "Comments TEXT, Version TEXT, VersionDate DATETIME, PublishDate DATETIME, Language NVARCHAR(10), "
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

    verse_count, skipped, chapters = 0, 0, set()
    for book_id, code, name, testament, ordinal in books:
        if code not in bec.CANONICAL_BOOK_ORDER:
            print(f"WARN: book '{code}' not in canonical 66-book order, skipping", file=sys.stderr)
            continue
        book_number = bec.CANONICAL_BOOK_ORDER.index(code) + 1

        rows, titles = [], {}
        for verse_id, chapter, verse in bec.get_verses_for_book(src_conn, book_id):
            if verse == 0:                       # title: folded into verse 1 below
                title = render_title(src_conn, verse_id, args)
                if title:
                    titles[chapter] = title
                continue
            scripture, starts_paragraph = render_verse(src_conn, verse_id, args)
            if scripture is None:
                skipped += 1
                continue
            if verse == 1 and chapter in titles:
                scripture = titles.pop(chapter) + scripture
            if starts_paragraph and rows:
                rows[-1][3] += "<CM>"            # <CM> ends the paragraph that came before this verse
            rows.append([book_number, chapter, verse, scripture])

        out_cur.executemany("INSERT OR REPLACE INTO Bible (Book, Chapter, Verse, Scripture) VALUES (?,?,?,?)", rows)
        verse_count += len(rows)
        chapters.update((r[0], r[1]) for r in rows)
        print(f"{code}: {len({r[1] for r in rows})} chapters, {len(rows)} verses", file=sys.stderr)

    details_sql, details_params = build_details_sql(args, has_ot, has_nt)
    out_cur.execute(details_sql, details_params)
    out_cur.execute("CREATE INDEX BookChapterVerseIndex ON Bible (Book, Chapter, Verse)")
    out_conn.commit()

    print(f"\nWrote {len(chapters)} chapters, {verse_count} verses -> {args.out}", file=sys.stderr)
    if skipped:
        print(f"  {skipped} verses skipped (no content for layout '{args.layout}')", file=sys.stderr)
    print("Copy to a .bbl.mybible file in MySword's modules/bibles folder to use.", file=sys.stderr)

    src_conn.close()
    out_conn.close()


if __name__ == "__main__":
    main()
