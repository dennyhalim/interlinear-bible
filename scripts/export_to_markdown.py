#!/usr/bin/env python3
"""
Export the interlinear database as Markdown: one file per book, per
thematic group (--group), or per testament.

Two renderings:

  default   Full interlinear, sized for NotebookLM (500,000-word cap per
            source): per verse the reference, the original-language text,
            then a per-word `surface=translit[strongs,morph]:"gloss"` line.

  --compact Reading text (for Claude Project knowledge or plain reading):
              ## Genesis 1
              ¹ Pada mula ...
              ² Bumi belum berbentuk ...          <- one line per verse,
                                                     blank line only between
              ¶-started paragraphs                   paragraphs
            Uses the natural verse translation (verse_translation table) and
            falls back to the joined word glosses when a verse has none yet.
            Superscript verse numbers; Psalm titles (verse 0) as an italic
            line; in poetry ('|' marks the lines of a verse) every line is
            kept apart with a Markdown hard break. --strongs keeps the
            {G746}-style Strong's tags in that text.

Glosses come from bible_export_common (ai_gloss -> translated lexicon ->
English lexicon -> source gloss), the same as the MySword/e-Sword/OSIS
exporters.

WORD COUNT CAVEAT: the word counts printed are naive whitespace splits. They
are a fair proxy for NotebookLM's word cap but NOT for Claude Project
knowledge, which is token-based: the interlinear render packs each word into
one compound whitespace token (6-10x the real weight). Use --compact for
Claude; the estimated-token figure printed alongside is chars/4.

Usage:
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown
  export_to_markdown.py --db ... --out-dir ... --group
  export_to_markdown.py --db ... --out-dir ... --book GEN
  export_to_markdown.py --db ... --out-dir output/reading --compact --group [--strongs]
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import bible_export_common as bec

# Traditional thematic groupings. Interlinear word counts (whitespace-split)
# stay under NotebookLM's 500,000-word cap, with Historical Books the
# tightest at ~433K. Re-check if the source data ever grows substantially.
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

SUP = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


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


def count_chapters_verses(cur, book_ids):
    """(chapters, verses) stored for these books, for the progress lines."""
    book_ids = list(book_ids)
    marks = ",".join("?" * len(book_ids))
    cur.execute(
        f"SELECT COUNT(DISTINCT book_id * 1000 + chapter), COUNT(*) FROM verse WHERE book_id IN ({marks})",
        book_ids,
    )
    return cur.fetchone()


def render_book(cur, book_id, code, name, testament, wikilink=False, compact=False, language="id", tagged=False):
    """Render one book as Markdown. compact=True is the reading format:
    '## Genesis 1', superscript verse numbers, one line per verse with no
    blank lines inside a paragraph (reads as one paragraph in Markdown, one
    verse per line in plain text), blank line between paragraphs. In poetry
    ('|' separates the lines of a verse) every line is kept apart with a
    Markdown hard break. A verse whose translation starts with the paragraph
    mark begins a new paragraph; Psalm titles (verse 0) are an italic line.
    Default (compact=False) is the full interlinear."""
    lines = [f"# {name} ({'Old Testament, Masoretic Text' if testament == 'OT' else 'New Testament, Textus Receptus'})", ""]
    verses = get_verses(cur, book_id)
    current_chapter = None
    para = []  # compact mode: (lines, is_poetry) per verse of the paragraph being built

    def flush():
        if not para:
            return
        out = [ln for verse_lines, _ in para for ln in verse_lines]
        if any(is_poetry for _, is_poetry in para):  # two trailing spaces = hard break
            out = [ln + "  " for ln in out[:-1]] + out[-1:]
        lines.extend(out + [""])
        para.clear()

    for verse_id, chapter, verse in verses:
        if chapter != current_chapter:
            flush()
            lines.append(f"\n## {name} {chapter}\n")
            current_chapter = chapter

        words = bec.get_words_for_verse(cur.connection, verse_id, language)

        if compact:
            text = bec.get_verse_translation(cur.connection, verse_id, language, tagged=tagged)
            new_para = False
            if text:
                text = text.strip()
                new_para = text.startswith("\u00b6")
                text = text.lstrip("\u00b6").strip()
            else:  # no natural translation yet: joined glosses, minus placeholders
                text = " ".join(w["gloss"] for w in words
                                if w["gloss"] and w["gloss"] != "-" and not w["gloss"].startswith("("))
            parts = [p.strip() for p in text.split("|") if p.strip()]
            if not parts:
                continue
            if verse == 0:  # Psalm title: own italic line, no number
                flush()
                lines.extend([f"*{' '.join(parts)}*", ""])
                continue
            if new_para:
                flush()
            verse_lines = [f"{str(verse).translate(SUP)} {parts[0]}"] + [f"   {p}" for p in parts[1:]]
            para.append((verse_lines, len(parts) > 1))
            continue

        ref = f"{code} {chapter}:{verse}"
        if wikilink:
            ref = f"[[{ref}]]"
        surface_line = " ".join(w["surface"] for w in words)
        lines.append(f"**{ref}** {surface_line}")
        # Compact per-word gloss line instead of a Markdown table: far fewer
        # tokens, and reads like a printed interlinear's word-by-word row.
        word_parts = [
            f'{w["surface"]}={w["translit"]}[{w["strongs"] or ""},{w["morph_code"] or ""}]:"{w["gloss"]}"'
            for w in words
        ]
        lines.append("  " + " | ".join(word_parts))
        lines.append("")

    flush()
    return "\n".join(lines)


def report_size(label, content, out_path, counts=None):
    """Print the naive whitespace word count (comparable to NotebookLM's
    cap), a rough chars/4 token estimate (for Claude Project capacity), and
    the chapters/verses written."""
    word_count = len(content.split())
    est_tokens = len(content) // 4
    extra = f", {counts[0]} chapters, {counts[1]} verses" if counts else ""
    print(f"{label}: {word_count} words (whitespace-split), "
          f"~{est_tokens:,} estimated tokens{extra} -> {out_path}", file=sys.stderr)
    return word_count, est_tokens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--testament", choices=["OT", "NT"], help="Export only one testament")
    ap.add_argument("--book", help="Export only one book (canonical 3-letter code, e.g. GEN, JHN)")
    ap.add_argument("--language", default="id", help="Gloss/translation language code (default: id)")
    ap.add_argument("--wikilink", action="store_true", help="Wrap chapter/verse references in [[double brackets]] for Obsidian")
    ap.add_argument(
        "--compact", action="store_true",
        help="Reading format (natural verse translation, superscript verse numbers, paragraphs, poetry lines) "
             "instead of full interlinear. Roughly 75-85%% smaller; use this for Claude Project knowledge."
    )
    ap.add_argument("--strongs", action="store_true", help="Keep {G746}-style Strong's tags in --compact text")
    ap.add_argument(
        "--combine-testament", action="store_true",
        help="Write one combined file per testament instead of one per book. Does NOT help with "
             "NotebookLM's 500,000-word cap (combined testaments are over it in interlinear mode)."
    )
    ap.add_argument(
        "--group", action="store_true",
        help="Write one file per traditional thematic group (8 files). In interlinear mode each stays "
             "under NotebookLM's 500,000-word cap; in --compact mode they are far smaller."
    )
    args = ap.parse_args()

    if not args.db.is_file() or args.db.stat().st_size == 0:
        print(f"ERROR: database missing or empty: {args.db.resolve()}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    books = get_books(cur, testament=args.testament, book_code=args.book)
    if not books:
        print("No matching books found", file=sys.stderr)
        sys.exit(1)

    suffix = "_compact" if args.compact else ""

    def render(book_id, code, name, testament):
        return render_book(cur, book_id, code, name, testament, wikilink=args.wikilink,
                           compact=args.compact, language=args.language, tagged=args.strongs)

    if args.group:
        code_to_book = {code: (book_id, code, name, testament, ordinal) for book_id, code, name, testament, ordinal in books}
        total_est_tokens = 0
        for group_name, book_codes in BOOK_GROUPS.items():
            parts, ids, missing = [], [], []
            for code in book_codes:
                if code not in code_to_book:
                    missing.append(code)
                    continue
                book_id, code_, name, testament, ordinal = code_to_book[code]
                parts.append(render(book_id, code_, name, testament))
                ids.append(book_id)
            if missing:
                print(f"WARN: group '{group_name}' missing books (excluded by --testament/--book filter?): {missing}", file=sys.stderr)
            if not parts:
                continue
            combined = "\n\n---\n\n".join(parts)
            out_path = args.out_dir / f"{group_name}{suffix}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count, est_tokens = report_size(f"{group_name} ({len(parts)} books)", combined, out_path,
                                                 count_chapters_verses(cur, ids))
            total_est_tokens += est_tokens
            if not args.compact and word_count > 500_000:
                print("  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        if args.compact:
            print(f"\nTOTAL across all groups: ~{total_est_tokens:,} estimated tokens (chars/4, a ballpark). "
                  f"Compare against your Claude plan's project capacity before uploading all 8 files.", file=sys.stderr)
        conn.close()
        return

    if args.combine_testament:
        by_testament = {}
        for book_id, code, name, testament, ordinal in books:
            by_testament.setdefault(testament, []).append((book_id, code, name, ordinal))

        for testament, book_list in by_testament.items():
            ordered = sorted(book_list, key=lambda b: b[3])
            combined = "\n\n---\n\n".join(render(b[0], b[1], b[2], testament) for b in ordered)
            label = "Old_Testament" if testament == "OT" else "New_Testament"
            out_path = args.out_dir / f"{label}{suffix}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count, _ = report_size(label, combined, out_path, count_chapters_verses(cur, [b[0] for b in ordered]))
            if not args.compact and word_count > 500_000:
                print("  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        conn.close()
        return

    for book_id, code, name, testament, ordinal in books:
        content = render(book_id, code, name, testament)
        out_path = args.out_dir / f"{ordinal:02d}_{code}_{name.replace(' ', '_')}{suffix}.md"
        out_path.write_text(content, encoding="utf-8")
        word_count, _ = report_size(f"{code} ({name})", content, out_path, count_chapters_verses(cur, [book_id]))
        if not args.compact and word_count > 500_000:
            print("  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
