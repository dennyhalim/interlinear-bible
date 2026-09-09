#!/usr/bin/env python3
"""
Export the interlinear database as human-readable per-book Markdown
files, suitable for uploading as NotebookLM sources -- or, with
--compact, as a much smaller running-text export sized for Claude
Project knowledge.

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

IMPORTANT CAVEAT ABOUT WORD COUNTS, learned the hard way: the word
counts this script prints (and the README's per-group table) are
computed with `len(text.split())` -- a naive whitespace split. That's
a reasonable proxy for NotebookLM's stated 500,000-*word* cap, but it
is NOT a reasonable proxy for Claude Project knowledge's capacity,
which is accounted in real tokens, not whitespace-delimited chunks.
The default (interlinear) render packs every single word of Scripture
into one compound whitespace-token like:

    בְּרֵאשִׁית=B're'shit[H7225,HNcfsa]:"beginning"

`.split()` counts that as ONE "word", but it's 6-10x the character/
token weight of a real word. A file that reports as, say, 400,000
"words" by this script's own counter can therefore be several times
that in actual tokens -- comfortably explaining a project-knowledge
upload that fails as ">1000% over limit" even though the printed word
count looked safely under NotebookLM's cap. The two caps are sized
against genuinely different units; a file built for one is not sized
for the other. This is why --compact exists (see below) rather than
just uploading the interlinear export to both tools.

--compact mode, added for Claude Project knowledge specifically:
strips the per-word Strong's/morphology/transliteration apparatus
entirely and renders each verse as reference + running gloss text
only (reads like a plain translation, not an interlinear). This is
NOT a reduced-fidelity version of the same export -- it's a
deliberately different artifact for a different job. Use the default
(interlinear) export for NotebookLM when you want word-level Strong's/
morphology lookups; use --compact for Claude when you want a project
that can hold the whole Bible (or large groups of it) for read/search/
discuss workflows where per-word morphology isn't the point. Verified
against real output: compact mode drops file size by roughly 75-85%
relative to the interlinear render, since it removes the surface-text
line, the transliteration, the Strong's numbers, and the morphology
code for every single word, keeping only the gloss.

Usage:
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --testament OT
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --book GEN

  # Compact mode, sized for Claude Project knowledge:
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown_compact --compact --group
  export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown_compact --compact --book GEN
"""
import argparse
import sqlite3
import sys
from pathlib import Path


# Traditional thematic groupings. Verified against actual rendered word
# counts (see README) -- every group stays under NotebookLM's 500,000
# word cap, with Historical Books the tightest at ~433K (87% of the
# limit). If STEPBible/byztxt source data ever grows substantially,
# re-check this margin before relying on it. NOTE: these word counts
# are naive whitespace-split counts against the INTERLINEAR render --
# see the module docstring's caveat before using this table to reason
# about Claude Project knowledge capacity, which needs --compact sizing
# instead (see print_compact_estimate below).
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


def render_book(cur, book_id, code, name, testament, wikilink=False, compact=False):
    """Render one book as Markdown.

    compact=False (default): full interlinear -- surface-text line,
    then a per-word `surface=translit[strongs,morph]:"gloss"` line.
    Sized for NotebookLM (see module docstring).

    compact=True: reference + a single running-gloss line per verse,
    no surface text, no transliteration, no Strong's, no morphology.
    Reads like a plain translation. Sized for Claude Project knowledge
    -- roughly 75-85% smaller than the interlinear render for the same
    verses, since it drops four of the five per-word fields entirely
    rather than just formatting them more tersely.
    """
    lines = [f"# {name} ({'Old Testament, Masoretic Text' if testament == 'OT' else 'New Testament, Textus Receptus'})", ""]

    verses = get_verses(cur, book_id)
    current_chapter = None

    for verse_id, chapter, verse in verses:
        if chapter != current_chapter:
            lines.append(f"\n## Chapter {chapter}\n")
            current_chapter = chapter

        words = get_words(cur, verse_id)
        ref = f"{code} {chapter}:{verse}"
        if wikilink:
            ref = f"[[{ref}]]"

        if compact:
            # Running gloss only, in reading order, no per-word
            # apparatus at all. This is deliberately NOT the same
            # information at lower resolution -- it's a different,
            # much smaller artifact for a different job (see
            # docstring). If you need Strong's/morphology, use the
            # default interlinear export instead.
            gloss_parts = []
            for pos, lang, surface, translit, strongs, morph, gloss_source, ai_gloss, lexicon_gloss in words:
                gloss = ai_gloss or gloss_source or lexicon_gloss or ""
                if gloss:
                    gloss_parts.append(gloss)
            lines.append(f"**{ref}** {' '.join(gloss_parts)}")
        else:
            surface_line = " ".join(w[2] for w in words)
            lines.append(f"**{ref}** {surface_line}")

            # Compact per-word gloss line instead of a Markdown table --
            # far fewer tokens for the same information, and reads more
            # like a traditional printed interlinear's word-by-word
            # gloss row.
            word_parts = []
            for pos, lang, surface, translit, strongs, morph, gloss_source, ai_gloss, lexicon_gloss in words:
                gloss = ai_gloss or gloss_source or lexicon_gloss or ""
                strongs_disp = strongs or ""
                word_parts.append(f"{surface}={translit}[{strongs_disp},{morph}]:\"{gloss}\"")
            lines.append("  " + " | ".join(word_parts))

        lines.append("")

    return "\n".join(lines)


def report_size(label, content, out_path):
    """Print both the naive whitespace word count (comparable to the
    README's existing table / NotebookLM's stated cap) AND a rough
    character-based proxy for actual token weight, so the two caps
    this script's outputs get judged against are both visible instead
    of only the one that happens to look favorable. Real tokenization
    varies by model/tokenizer; ~4 characters per token is a widely
    used rough English-text estimate and is presented as an estimate,
    not an exact figure -- good enough to catch a 1000%-over-limit
    class of problem before upload, not precise enough to plan an
    exact budget around.
    """
    word_count = len(content.split())
    char_count = len(content)
    est_tokens = char_count // 4
    print(f"{label}: {word_count} words (whitespace-split), "
          f"~{est_tokens:,} estimated tokens -> {out_path}", file=sys.stderr)
    return word_count, est_tokens


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--testament", choices=["OT", "NT"], help="Export only one testament")
    ap.add_argument("--book", help="Export only one book (canonical 3-letter code, e.g. GEN, JHN)")
    ap.add_argument("--wikilink", action="store_true", help="Wrap chapter/verse references in [[double brackets]] for Obsidian")
    ap.add_argument(
        "--compact", action="store_true",
        help="Render running-gloss-only text (no surface text, transliteration, Strong's, or "
             "morphology) instead of full interlinear. Roughly 75-85%% smaller. Use this for "
             "Claude Project knowledge -- the default interlinear render is sized for "
             "NotebookLM's word-count cap, not Claude's token-based project capacity, and will "
             "vastly overshoot it (see module docstring)."
    )
    ap.add_argument(
        "--combine-testament", action="store_true",
        help="Write one combined file per testament instead of one per book. "
             "NOTE: does NOT help with NotebookLM's 500,000-word source cap -- "
             "a combined testament file is well OVER that limit (OT ~1.2M "
             "words, NT ~570K words) in interlinear mode; even in --compact "
             "mode a combined testament file may still be too large for a "
             "single Claude Project alongside anything else. For NotebookLM, "
             "use --group instead (see below) or the default one-file-per-book "
             "output split across two notebooks."
    )
    ap.add_argument(
        "--group", action="store_true",
        help="Write one file per traditional thematic group (Torah, "
             "Historical Books, Wisdom & Poetry, Major/Minor Prophets, "
             "Gospels & Acts, Pauline Epistles, General Epistles & "
             "Revelation) -- 8 files total. In default (interlinear) mode "
             "these are verified to stay under NotebookLM's 500,000-word "
             "cap. In --compact mode, sizes are dramatically smaller and "
             "better suited to loading several groups (or the whole Bible) "
             "into one Claude Project -- run with --compact --group and "
             "check the printed estimated-token counts for your specific "
             "database before uploading."
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
        total_est_tokens = 0
        for group_name, book_codes in BOOK_GROUPS.items():
            parts = []
            missing = []
            for code in book_codes:
                if code not in code_to_book:
                    missing.append(code)
                    continue
                book_id, code_, name, testament, ordinal = code_to_book[code]
                parts.append(render_book(cur, book_id, code_, name, testament, wikilink=args.wikilink, compact=args.compact))
            if missing:
                print(f"WARN: group '{group_name}' missing books (excluded by --testament/--book filter?): {missing}", file=sys.stderr)
            if not parts:
                continue
            combined = "\n\n---\n\n".join(parts)
            suffix = "_compact" if args.compact else ""
            out_path = args.out_dir / f"{group_name}{suffix}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count, est_tokens = report_size(f"{group_name} ({len(parts)} books)", combined, out_path)
            total_est_tokens += est_tokens
            if not args.compact and word_count > 500_000:
                print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        if args.compact:
            print(f"\nTOTAL across all groups: ~{total_est_tokens:,} estimated tokens "
                  f"({total_est_tokens // 250:,}-ish equivalent 'project knowledge words' by a "
                  f"rough 4 chars/token, ~4 chars/word rule of thumb -- treat as a ballpark, not "
                  f"an exact figure). Compare against your Claude plan's project capacity before "
                  f"uploading all 8 files to one project.", file=sys.stderr)
        conn.close()
        return

    if args.combine_testament:
        by_testament = {}
        for book_id, code, name, testament, ordinal in books:
            by_testament.setdefault(testament, []).append((book_id, code, name, ordinal))

        for testament, book_list in by_testament.items():
            parts = []
            for book_id, code, name, ordinal in sorted(book_list, key=lambda b: b[3]):
                parts.append(render_book(cur, book_id, code, name, testament, wikilink=args.wikilink, compact=args.compact))
            combined = "\n\n---\n\n".join(parts)
            label = "Old_Testament" if testament == "OT" else "New_Testament"
            suffix = "_compact" if args.compact else ""
            out_path = args.out_dir / f"{label}{suffix}.md"
            out_path.write_text(combined, encoding="utf-8")
            word_count, _ = report_size(label, combined, out_path)
            if not args.compact and word_count > 500_000:
                print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)
        conn.close()
        return

    for book_id, code, name, testament, ordinal in books:
        content = render_book(cur, book_id, code, name, testament, wikilink=args.wikilink, compact=args.compact)
        suffix = "_compact" if args.compact else ""
        out_path = args.out_dir / f"{ordinal:02d}_{code}_{name.replace(' ', '_')}{suffix}.md"
        out_path.write_text(content, encoding="utf-8")
        word_count, _ = report_size(f"{code} ({name})", content, out_path)
        if not args.compact and word_count > 500_000:
            print(f"  WARNING: exceeds NotebookLM's 500,000 word limit!", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()