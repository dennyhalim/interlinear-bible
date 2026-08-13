#!/usr/bin/env python3
"""
Parse STEPBible TBESG (Greek) and TBESH (Hebrew) brief lexicon files.

Both files share one 8-column format once past a large free-text header
and (for TBESG) interspersed "$========== PERSON(s)" proper-name blocks:

  eStrong#  dStrong  uStrong  Word(Heb/Grk)  Transliteration  Morph  Gloss  Meaning

Data rows are recognised by starting with a bare Strong's number
(e.g. "H0001" or "G0001") in the first column -- this reliably skips
all the header prose and the "$====" proper-name disambiguation blocks,
which use "Name@Ref=..." style first columns instead.

Multiple rows can share the same eStrong# (dStrong disambiguates further,
e.g. H0001G / H0001H / H0001I for different senses/individuals), so we key
primarily on dStrong, and separately keep an eStrong -> [dStrongs] index
for lookups from parser output that only has the eStrong-style number.

Output: one row per lexicon entry:
  language, estrong, dstrong, ustrong, word, translit, morph, gloss, meaning
"""
import re
import sys
import json
from pathlib import Path

DATA_ROW = re.compile(r"^([HG]\d{4,5}[A-Za-z]?)\t")


def parse_file(path: Path, language: str):
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not DATA_ROW.match(line):
                continue
            cols = line.split("\t")
            if len(cols) < 7:
                continue
            estrong, dstrong, ustrong, word, translit, morph, gloss = cols[0:7]
            meaning = cols[7] if len(cols) > 7 else ""
            # dStrong column often has trailing " =" or " = a Part of" etc; keep
            # the clean number separately for joining, full text for provenance.
            dstrong_clean = dstrong.split("=")[0].strip().split(",")[0].strip()
            rows.append({
                "language": language,
                "estrong": estrong.strip(),
                "dstrong": dstrong_clean,
                "dstrong_raw": dstrong.strip(),
                "ustrong": ustrong.strip().rstrip(","),
                "word": word.strip(),
                "translit": translit.strip(),
                "morph": morph.strip(),
                "gloss": gloss.strip(),
                "meaning": meaning.strip(),
            })
    return rows


def main():
    if len(sys.argv) != 4:
        print("Usage: parse_lexicons.py <tbesg_file> <tbesh_file> <output_jsonl>", file=sys.stderr)
        sys.exit(1)

    tbesg_path, tbesh_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    g_rows = parse_file(tbesg_path, "greek")
    print(f"{tbesg_path.name}: {len(g_rows)} entries", file=sys.stderr)
    all_rows.extend(g_rows)

    h_rows = parse_file(tbesh_path, "hebrew")
    print(f"{tbesh_path.name}: {len(h_rows)} entries", file=sys.stderr)
    all_rows.extend(h_rows)

    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"TOTAL: {len(all_rows)} entries -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
