#!/usr/bin/env python3
"""
Export the interlinear database as an Obsidian vault: one note per
chapter, with genuinely resolving [[wiki-links]] between chapters
(previous/next navigation, book index, testament index) -- not just
link *syntax* pointing at nothing.

This is deliberately a separate script from export_to_markdown.py:
that one optimizes for NotebookLM's per-source word cap (one file per
book or per thematic group, large files, no cross-file link targets
needed). This one optimizes for Obsidian's vault-of-small-notes idiom,
where [[links]] are only useful if the target note actually exists.

Structure produced:
  vault/
    00_Index.md                    -- top-level, links to both testaments
    Old_Testament/
      00_Index.md                  -- links to every OT book
      Genesis/
        00_Index.md                -- links to every chapter in Genesis
        Genesis 1.md                -- full chapter, every verse inline
        Genesis 2.md
        ...
      Exodus/
        ...
    New_Testament/
      ...

Every chapter note is named exactly "<Book Name> <Chapter>.md" (e.g.
"Genesis 1.md"), and every [[Book Name Chapter]] link elsewhere in the
vault (prev/next nav, book index, testament index, top index) points
at that exact name -- so links resolve on import, not just visually
look like links.

Usage:
  export_obsidian_vault.py --db output/interlinear.sqlite --out-dir output/obsidian_vault
  export_obsidian_vault.py --db output/interlinear.sqlite --out-dir output/obsidian_vault --book GEN
"""
import argparse
import re
import sqlite3
import sys
from pathlib import Path


def safe_filename(name: str) -> str:
    """Strip characters that are unsafe across Windows/Mac/Linux filesystems."""
    return re.sub(r'[\\/:*?"<>|]', "", name)


def get_books(cur, book_code=None):
    query = "SELECT book_id, code, name, testament, ordinal FROM book"
    params = []
    if book_code:
        query += " WHERE code = ?"
        params.append(book_code)
    query += " ORDER BY ordinal"
    cur.execute(query, params)
    return cur.fetchall()


def get_chapters(cur, book_id):
    cur.execute(
        "SELECT DISTINCT chapter FROM verse WHERE book_id = ? ORDER BY chapter",
        (book_id,),
    )
    return [r[0] for r in cur.fetchall()]


def get_verses_in_chapter(cur, book_id, chapter):
    cur.execute(
        "SELECT verse_id, verse FROM verse WHERE book_id = ? AND chapter = ? ORDER BY verse",
        (book_id, chapter),
    )
    return cur.fetchall()


def get_words(cur, verse_id):
    cur.execute(
        """
        SELECT w.position, w.surface, w.translit, w.strongs, w.morph_code,
               w.gloss_source, ag.gloss as ai_gloss, lx.gloss as lexicon_gloss
        FROM word w
        LEFT JOIN ai_gloss ag ON ag.word_id = w.word_id
        LEFT JOIN lexicon_entry lx ON lx.dstrong = w.strongs OR lx.estrong = w.strongs
        WHERE w.verse_id = ?
        ORDER BY w.position
        """,
        (verse_id,),
    )
    return cur.fetchall()


def render_chapter(cur, book_name, code, chapter, book_id, prev_link, next_link, book_index_link):
    lines = [f"# {book_name} {chapter}", ""]
    lines.append(f"[[{book_index_link}|<- {book_name} index]]")
    if prev_link:
        lines.append(f"[[{prev_link}|<- previous chapter]]")
    if next_link:
        lines.append(f"[[{next_link}|next chapter ->]]")
    lines.append("")

    verses = get_verses_in_chapter(cur, book_id, chapter)
    for verse_id, verse in verses:
        words = get_words(cur, verse_id)
        surface_line = " ".join(w[1] for w in words)
        lines.append(f"## {code} {chapter}:{verse}")
        lines.append(surface_line)
        lines.append("")

        word_parts = []
        for pos, surface, translit, strongs, morph, gloss_source, ai_gloss, lexicon_gloss in words:
            gloss = ai_gloss or gloss_source or lexicon_gloss or ""
            word_parts.append(f"{surface}={translit}[{strongs or ''},{morph or ''}]:\"{gloss}\"")
        lines.append(" | ".join(word_parts))
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--book", help="Export only one book (canonical 3-letter code, e.g. GEN)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    vault_dir = args.out_dir
    vault_dir.mkdir(parents=True, exist_ok=True)

    books = get_books(cur, book_code=args.book)
    if not books:
        print("No matching books found", file=sys.stderr)
        sys.exit(1)

    by_testament = {"OT": [], "NT": []}
    for book_id, code, name, testament, ordinal in books:
        by_testament[testament].append((book_id, code, name, ordinal))

    total_chapters = 0

    for testament, book_list in by_testament.items():
        if not book_list:
            continue
        testament_label = "Old_Testament" if testament == "OT" else "New_Testament"
        testament_dir = vault_dir / testament_label
        testament_dir.mkdir(parents=True, exist_ok=True)

        testament_index_lines = [f"# {testament_label.replace('_', ' ')}", ""]

        for book_id, code, name, ordinal in sorted(book_list, key=lambda b: b[3]):
            safe_name = safe_filename(name)
            book_dir = testament_dir / safe_name
            book_dir.mkdir(parents=True, exist_ok=True)

            chapters = get_chapters(cur, book_id)
            book_index_target = f"{testament_label}/{safe_name}/00_Index"

            book_index_lines = [f"# {name}", ""]
            for ch in chapters:
                chapter_target = f"{testament_label}/{safe_name}/{safe_name} {ch}"
                book_index_lines.append(f"- [[{chapter_target}|Chapter {ch}]]")

            (book_dir / "00_Index.md").write_text("\n".join(book_index_lines), encoding="utf-8")

            for i, ch in enumerate(chapters):
                prev_target = f"{testament_label}/{safe_name}/{safe_name} {chapters[i-1]}" if i > 0 else None
                next_target = f"{testament_label}/{safe_name}/{safe_name} {chapters[i+1]}" if i < len(chapters) - 1 else None

                content = render_chapter(
                    cur, name, code, ch, book_id,
                    prev_link=prev_target, next_link=next_target,
                    book_index_link=book_index_target,
                )
                chapter_path = book_dir / f"{safe_name} {ch}.md"
                chapter_path.write_text(content, encoding="utf-8")
                total_chapters += 1

            testament_index_lines.append(f"- [[{book_index_target}|{name}]] ({len(chapters)} chapters)")

        (testament_dir / "00_Index.md").write_text("\n".join(testament_index_lines), encoding="utf-8")

    top_index_lines = ["# Interlinear Bible", ""]
    if by_testament["OT"]:
        top_index_lines.append("- [[Old_Testament/00_Index|Old Testament]]")
    if by_testament["NT"]:
        top_index_lines.append("- [[New_Testament/00_Index|New Testament]]")
    (vault_dir / "00_Index.md").write_text("\n".join(top_index_lines), encoding="utf-8")

    print(f"Wrote {total_chapters} chapter notes across {len(books)} books -> {vault_dir}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()