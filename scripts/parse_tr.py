#!/usr/bin/env python3
"""
Parse byztxt greektext-textus-receptus .UTR files into normalized rows.

Format (per file, one file per NT book):
  "1:1 en 1722 {PREP} arch 746 {N-DSF} hn 1510 5707 {V-IAI-3S} o 3588\r\n
   {T-NSM} logov 3056 {N-NSM} ...\r\n
   1:2 outov 3778 {D-NSM} ..."

- Verses are delimited by a "chapter:verse " token.
- Each word is: <transliterated_word> <strongs_number> [<extra_parse_number>] {<MORPH_TAG>}
  The optional extra numeric token (seen on verbs) is Robinson's numeric
  parsing code and is kept alongside the morph tag, not discarded.
- Lines are CRLF-terminated and wrap words across lines; unwrap first.

Output: one row per word:
  book_code, chapter, verse, position, surface_translit, strongs, morph_code, parse_number
"""
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


def parse_file(path: Path):
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()

    file_code = path.stem.upper()
    book_code = FILE_TO_BOOK.get(file_code)
    if not book_code:
        print(f"WARN: unmapped file code {file_code} ({path.name}), skipping", file=sys.stderr)
        return []

    rows = []
    # Split on verse markers, keeping them
    parts = VERSE_MARK.split(text)
    # parts[0] is leading junk before first marker (should be empty/near-empty)
    # then triples of (chapter, verse, body) repeat
    i = 1
    while i < len(parts) - 2:
        chapter, verse, body = parts[i], parts[i + 1], parts[i + 2]
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
    return rows


def main():
    if len(sys.argv) != 3:
        print("Usage: parse_tr.py <parsed_dir> <output_jsonl>", file=sys.stderr)
        sys.exit(1)

    parsed_dir = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    for utr_file in sorted(parsed_dir.glob("*.UTR")):
        rows = parse_file(utr_file)
        print(f"{utr_file.name}: {len(rows)} words", file=sys.stderr)
        all_rows.extend(rows)

    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"TOTAL: {len(all_rows)} words -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
