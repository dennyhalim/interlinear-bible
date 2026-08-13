#!/usr/bin/env python3
"""
Parse STEPBible TAHOT (Translators Amalgamated Hebrew OT) files into
normalized rows.

We parse the per-morpheme detail table (rows like "Gen.1.1#01=L\t...")
rather than the summary block above it, since the detail rows are one
row per morpheme (prefix/stem/suffix split apart) with clean tab columns:

  Ref#NN=Source \t Hebrew \t Transliteration \t Translation \t dStrongs \t
  Grammar \t MeaningVariants \t SpellingVariants \t RootStrong+Instance \t
  AltStrongs \t ConjoinWord \t ExpandedStrongTags ...

- dStrongs may contain multiple slash-joined parts (prefix/stem/suffix),
  each optionally wrapped in {} to mark the "head" word of the group vs.
  attached participles (e.g. "H9003/{H7225G}" = prefix H9003 + head word
  H7225G). We keep all parts and flag which one is the head.
- A trailing "\\H9016" (paragraph/verse-end marker) or similar backslash-
  joined tag can appear appended to the last morpheme of a verse; kept
  as a separate punctuation-tag field, not treated as a Strong's number.

Output: one row per morpheme:
  book_code, chapter, verse, word_index, morph_index, hebrew, translit,
  gloss, strongs_parts (list), head_strongs, grammar_parts (list), punct_tag
"""
import re
import sys
import json
from pathlib import Path

FILE_TO_BOOKS = {
    # filename fragment -> ordered list of book codes it may contain
    # (used only for sanity; actual book code comes from the ref itself)
}

REF_ROW = re.compile(
    r"^([1-3]?[A-Za-z]{2,3})\.(\d+)\.(\d+)#(\d+)=(\S*)\t(.*)$"
)

# Map STEPBible's 3-letter-ish book abbreviations to our canonical codes.
# Verified exhaustively against actual TAHOT files (all 39 OT books present):
# {'1Ch','1Ki','1Sa','2Ch','2Ki','2Sa','Amo','Dan','Deu','Ecc','Est','Exo',
#  'Ezk','Ezr','Gen','Hab','Hag','Hos','Isa','Jdg','Jer','Job','Jol','Jon',
#  'Jos','Lam','Lev','Mal','Mic','Nam','Neh','Num','Oba','Pro','Psa','Rut',
#  'Sng','Zec','Zep'}
STEP_TO_CANON = {
    "Gen": "GEN", "Exo": "EXO", "Lev": "LEV", "Num": "NUM", "Deu": "DEU",
    "Jos": "JOS", "Jdg": "JDG", "Rut": "RUT", "1Sa": "1SA", "2Sa": "2SA",
    "1Ki": "1KI", "2Ki": "2KI", "1Ch": "1CH", "2Ch": "2CH", "Ezr": "EZR",
    "Neh": "NEH", "Est": "EST", "Job": "JOB", "Psa": "PSA", "Pro": "PRO",
    "Ecc": "ECC", "Sng": "SNG", "Isa": "ISA", "Jer": "JER", "Lam": "LAM",
    "Ezk": "EZK", "Dan": "DAN", "Hos": "HOS", "Jol": "JOL", "Amo": "AMO",
    "Oba": "OBA", "Jon": "JON", "Mic": "MIC", "Nam": "NAM", "Hab": "HAB",
    "Zep": "ZEP", "Hag": "HAG", "Zec": "ZEC", "Mal": "MAL",
}


def split_strongs_group(field: str):
    """
    Split a dStrongs-style field like:
      "H9003/{H7225G}"        -> [("H9003", False), ("H7225G", True)]
      "{H1254A}"               -> [("H1254A", True)]
      "H9009/{H0776G}\\H9016"  -> [("H9009", False), ("H0776G", True)], punct="H9016"
    Returns (parts, punct_tag) where parts is a list of (strongs, is_head).
    """
    punct_tag = None
    if "\\" in field:
        field, punct_tag = field.split("\\", 1)
        punct_tag = punct_tag.strip() or None

    parts = []
    for chunk in field.split("/"):
        chunk = chunk.strip()
        if not chunk:
            continue
        is_head = chunk.startswith("{") and chunk.endswith("}")
        clean = chunk.strip("{}")
        if clean:
            parts.append((clean, is_head))
    return parts, punct_tag


def parse_file(path: Path):
    rows = []
    word_counters = {}  # (book, chapter, verse) -> running word_index

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            m = REF_ROW.match(line)
            if not m:
                continue
            step_book, chapter, verse, morph_idx, source, rest = m.groups()
            book_code = STEP_TO_CANON.get(step_book)
            if not book_code:
                print(f"WARN: unmapped book '{step_book}' in {path.name}", file=sys.stderr)
                continue

            cols = rest.split("\t")
            # cols: Hebrew, Translit, Translation, dStrongs, Grammar, ...
            if len(cols) < 5:
                continue
            hebrew, translit, gloss, dstrongs, grammar = cols[0:5]

            strongs_parts, punct_tag = split_strongs_group(dstrongs)
            grammar_parts = [g for g in grammar.split("/") if g]

            head_strongs = None
            for s, is_head in strongs_parts:
                if is_head:
                    head_strongs = s
                    break
            if head_strongs is None and strongs_parts:
                head_strongs = strongs_parts[-1][0]

            key = (book_code, int(chapter), int(verse))
            # word_index groups morphemes belonging to the same surface word
            # (STEPBible's #NN is already a per-morpheme counter within the verse,
            # matching Hebrew word order, so we use it directly as position).
            rows.append({
                "book_code": book_code,
                "chapter": int(chapter),
                "verse": int(verse),
                "position": int(morph_idx),
                "source_flag": source,
                "hebrew": hebrew,
                "translit": translit,
                "gloss": gloss,
                "strongs_parts": [s for s, _ in strongs_parts],
                "head_strongs": head_strongs,
                "grammar_parts": grammar_parts,
                "punct_tag": punct_tag,
            })
    return rows


def main():
    if len(sys.argv) != 3:
        print("Usage: parse_tahot.py <tahot_dir_glob_or_files...> <output_jsonl>", file=sys.stderr)
        sys.exit(1)

    src = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    files = sorted(src.glob("TAHOT*.txt")) if src.is_dir() else [src]

    all_rows = []
    for f in files:
        rows = parse_file(f)
        print(f"{f.name}: {len(rows)} morphemes", file=sys.stderr)
        all_rows.extend(rows)

    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"TOTAL: {len(all_rows)} morphemes -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
