#!/usr/bin/env python3
"""
Parse byztxt greektext-textus-receptus .UTR files into normalized rows.

Format (per file, one file per NT book):
  "1:1 en 1722 {PREP} arch 746 {N-DSF} hn 1510 5707 {V-IAI-3S} o 3588\\r\\n
   {T-NSM} logov 3056 {N-NSM} ...\\r\\n
   1:2 outov 3778 {D-NSM} ..."

- Verses are delimited by a "chapter:verse " token.
- Each word is: <transliterated_word> <strongs_number> [<extra_parse_number>] {<MORPH_TAG>}
  The optional extra numeric token (seen on verbs) is Robinson's numeric
  parsing code and is kept alongside the morph tag, not discarded.
- Lines are CRLF-terminated and wrap words across lines; unwrap first.

Variant readings: where the two editions differ, the file gives "| A | B |" (A, B may be empty or several words;
for a respelled word it is "| etuqh | equqh | 2380 5681 {V-API-3S}", the number and parse after the second bar being
shared). The repo README says the readings appear to be Stephanus 1550 (A) and Scrivener 1894 (B), identified by
comparison, because the Robinson files do not name the editions. Exactly one reading is kept (--reading, default B,
Scrivener 1894, the text behind the KJV tradition), so the output never mixes the two. Before this fix the markers
were ignored: words of both readings were kept, and a respelled word was lost.

Output: one row per word:
  book_code, chapter, verse, position, surface_translit, strongs, morph_code, parse_number

Usage:
  parse_tr.py <parsed_dir> <output_jsonl> [--reading A|B] [--report variants.txt]
"""
import argparse
import re
import sys
import json
from pathlib import Path

# byztxt uses 3-letter file codes that don't all match our canonical book codes.
FILE_TO_BOOK = {
    "MT": "MAT", "MR": "MRK", "LU": "LUK", "JOH": "JHN", "AC": "ACT",
    "RO": "ROM", "1CO": "1CO", "2CO": "2CO", "GA": "GAL", "EPH": "EPH",
    "PHP": "PHP", "COL": "COL", "1TH": "1TH", "2TH": "2TH", "1TI": "1TI",
    "2TI": "2TI", "TIT": "TIT", "PHM": "PHM", "HEB": "HEB", "JAS": "JAS",
    "1PE": "1PE", "2PE": "2PE", "1JO": "1JN", "2JO": "2JN", "3JO": "3JN",
    "JUDE": "JUD", "RE": "REV",
}

VERSE_MARK = re.compile(r"(\d+):(\d+)\s+")
# word token: transliteration, strongs number, optional extra parse number, {MORPH}
WORD_TOKEN = re.compile(
    r"([A-Za-z][A-Za-z']*)\s+(\d+)(?:\s+(\d+))?\s+\{([^}]*)\}"
)
# a variant reading: "| A | B |"
VARIANT = re.compile(r"\|([^|]*)\|([^|]*)\|")
READINGS = {"A": 0, "B": 1}
READING_NAMES = {"A": "Stephanus 1550", "B": "Scrivener 1894"}


def parse_file(path: Path, reading: str = "B"):
    """Returns (rows, variants). variants lists (chapter, verse, words_in_A, words_in_B) for every verse that
    has variant readings; the rows carry the chosen reading only."""
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()

    file_code = path.stem.upper()
    book_code = FILE_TO_BOOK.get(file_code)
    if not book_code:
        print(f"WARN: unmapped file code {file_code} ({path.name}), skipping", file=sys.stderr)
        return [], []

    rows, variants = [], []
    # Split on verse markers, keeping them
    parts = VERSE_MARK.split(text)
    # parts[0] is leading junk before first marker (should be empty/near-empty)
    # then triples of (chapter, verse, body) repeat
    i = 1
    while i < len(parts) - 2:
        chapter, verse, body = parts[i], parts[i + 1], parts[i + 2]
        if "|" in body:
            words = [len(WORD_TOKEN.findall(VARIANT.sub(lambda m: m.group(1 + k), body))) for k in (0, 1)]
            variants.append((int(chapter), int(verse), words[0], words[1]))
            body = VARIANT.sub(lambda m: m.group(1 + READINGS[reading]), body)
            if "|" in body:
                print(f"WARN: {book_code} {chapter}:{verse} has an unpaired '|'; ignored", file=sys.stderr)
                body = body.replace("|", " ")
        position = 0
        for m in WORD_TOKEN.finditer(body):
            position += 1
            translit, strongs, parse_num, morph = m.groups()
            rows.append({
                "book_code": book_code,
                "chapter": int(chapter),
                "verse": int(verse),
                "position": position,
                "surface_translit": translit,
                "strongs": f"G{int(strongs):04d}",
                "morph_code": morph,
                "parse_number": parse_num,
            })
        i += 3
    return rows, variants


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("parsed_dir", type=Path)
    ap.add_argument("out_jsonl", type=Path)
    ap.add_argument("--reading", choices=sorted(READINGS), type=str.upper, default="B",
                    help="Which edition to keep where the file gives '| A | B |': A = Stephanus 1550, B = Scrivener 1894 (default)")
    ap.add_argument("--report", type=Path, help="Write the verses that have variant readings, with their word counts, to this file")
    args = ap.parse_args()
    args.out_jsonl.parent.mkdir(parents=True, exist_ok=True)

    all_rows, report = [], []
    for utr_file in sorted(args.parsed_dir.glob("*.UTR")):
        rows, variants = parse_file(utr_file, args.reading)
        note = f", {len(variants)} verses with variant readings" if variants else ""
        print(f"{utr_file.name}: {len(rows)} words{note}", file=sys.stderr)
        all_rows.extend(rows)
        if rows:
            code = rows[0]["book_code"]
            report += [f"{code} {c}:{v}\tA={a}\tB={b}" for c, v, a, b in variants]

    with args.out_jsonl.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    if args.report:
        args.report.write_text("\n".join(report) + "\n", encoding="utf-8")

    chapters = {(r["book_code"], r["chapter"]) for r in all_rows}
    verses = {(r["book_code"], r["chapter"], r["verse"]) for r in all_rows}
    print(f"TOTAL: {len(chapters)} chapters, {len(verses)} verses, {len(all_rows)} words -> {args.out_jsonl}", file=sys.stderr)
    print(f"Reading kept: {args.reading} ({READING_NAMES[args.reading]}); {len(report)} verses had variant readings"
          + (f", listed in {args.report}" if args.report else " (add --report FILE to list them)"), file=sys.stderr)


if __name__ == "__main__":
    main()
