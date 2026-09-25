#!/usr/bin/env python3
"""
Convert this project's interlinear Markdown export (export_to_markdown.py)
to Word (.docx) files, for OneNote import or any other tool without
Markdown support.

Why this exists: OneNote has zero native Markdown support (verified
against multiple 2026 sources) -- no import, no rendering, no export,
on any platform. Dropping a .md file in directly means every heading
marker, pipe, and bracket shows up as literal text with no structure.
DOCX is OneNote's actual supported import path (File -> Insert ->
Printout, or drag-and-drop), and its DOCX import is high fidelity --
headings, lists, and tables carry over correctly.

This is a THIN WRAPPER around export_to_markdown.py, not a
reimplementation: it calls that script's own book/group/testament
rendering to produce the same Markdown content already tested
elsewhere in this project, then converts it with pandoc (already a
project dependency per the docx skill). Two exporters producing
subtly different text for the same book would be a real risk to avoid;
routing through the one, already-tested Markdown renderer keeps this
docx output identical in content to what NotebookLM/Obsidian users see.

Verified against real output (Genesis, full pipeline including Hebrew
text and mixed RTL/pipe-delimited glosses): pandoc's default Markdown
-> docx conversion correctly preserves the heading hierarchy (book
title, chapter headings) and renders Hebrew/Greek text correctly, with
no manual template needed for a first working version.

Usage:
  python3 scripts/export_to_docx.py --db output/interlinear.sqlite --out-dir output/docx --group torah
  python3 scripts/export_to_docx.py --db output/interlinear.sqlite --out-dir output/docx --book GEN
  python3 scripts/export_to_docx.py --db output/interlinear.sqlite --out-dir output/docx --testament NT
"""
import argparse
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from export_to_markdown import BOOK_GROUPS, get_books, render_book


def convert_md_to_docx(md_path: Path, docx_path: Path):
    if shutil.which("pandoc") is None:
        print("ERROR: pandoc not found. Install it (e.g. 'apt install pandoc') and retry.", file=sys.stderr)
        sys.exit(1)
    result = subprocess.run(
        ["pandoc", str(md_path), "-o", str(docx_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR converting {md_path.name}: {result.stderr}", file=sys.stderr)
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--testament", choices=["OT", "NT"], help="Export only one testament")
    ap.add_argument("--book", help="Export only one book (canonical 3-letter code, e.g. GEN, JHN)")
    ap.add_argument(
        "--group", action="store_true",
        help="One .docx per traditional thematic group (Torah, Historical Books, "
             "Wisdom & Poetry, Major/Minor Prophets, Gospels & Acts, Pauline "
             "Epistles, General Epistles & Revelation) -- 8 files, the "
             "recommended granularity for OneNote (a handful of sizeable "
             "sections rather than 66 separate small ones)."
    )
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    books = get_books(cur, testament=args.testament, book_code=args.book)
    if not books:
        print("No matching books found", file=sys.stderr)
        sys.exit(1)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

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
                    parts.append(render_book(cur, book_id, code_, name, testament, wikilink=False))
                if missing:
                    print(f"WARN: group '{group_name}' missing books (excluded by --testament/--book filter?): {missing}", file=sys.stderr)
                if not parts:
                    continue
                combined = "\n\n---\n\n".join(parts)
                md_path = tmp_path / f"{group_name}.md"
                md_path.write_text(combined, encoding="utf-8")
                docx_path = args.out_dir / f"{group_name}.docx"
                if convert_md_to_docx(md_path, docx_path):
                    print(f"{group_name} -> {docx_path}", file=sys.stderr)
        else:
            for book_id, code, name, testament, ordinal in books:
                content = render_book(cur, book_id, code, name, testament, wikilink=False)
                md_path = tmp_path / f"{ordinal:02d}_{code}_{name.replace(' ', '_')}.md"
                md_path.write_text(content, encoding="utf-8")
                docx_path = args.out_dir / f"{ordinal:02d}_{code}_{name.replace(' ', '_')}.docx"
                if convert_md_to_docx(md_path, docx_path):
                    print(f"{code} ({name}) -> {docx_path}", file=sys.stderr)

    conn.close()
    print(f"\nDone. Files in {args.out_dir} -- in OneNote: File > Insert > Printout, or drag-and-drop the .docx onto a page.", file=sys.stderr)


if __name__ == "__main__":
    main()