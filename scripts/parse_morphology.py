#!/usr/bin/env python3
"""
Parse STEPBible TEGMC (Greek) and TEHMC (Hebrew) morphology code
expansion files into a flat code -> human-readable description table.

Format is simple: "<code>\t<description>" where description is a
semicolon-separated list of "Key=Value" pairs, e.g.:

  V-IAI-3S    Function=Verb; Tense=Imperfect; Voice=Active; Mood=Indicative; Person=3rd; Number=Singular
  HVqp3ms     Function=Verb ; Stem=Qal (hence Action=Simple; Voice=Active); Form=Perfect (...); Person=Third; Gender=Masculine; Number=Singular

We keep both the raw description string (for display) and a best-effort
parsed key/value dict (for structured use, e.g. filtering by Tense).
Note: some Hebrew descriptions have semicolons *inside* parentheses
(e.g. "hence Action=Simple; Voice=Active"), so naive semicolon-splitting
over-segments,;  we only split on top-level semicolons (depth 0).

Covers both TR/TAGNT-style Robinson codes (e.g. "V-IAI-3S") and the
brief lexicon-style codes (e.g. "G:N-M") since TEGMC documents both
in one file.

Output: one row per code:
  language, code, description_raw, fields (dict, best-effort)
"""
import re
import sys
import json
from pathlib import Path

CODE_ROW = re.compile(r"^(\S+)\t(.+)$")


def split_top_level(s: str, sep: str = ";"):
    """Split on sep only at paren-depth 0."""
    parts = []
    depth = 0
    cur = []
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return [p for p in parts if p]


def parse_fields(desc: str):
    fields = {}
    for segment in split_top_level(desc, ";"):
        if "=" in segment:
            key, _, val = segment.partition("=")
            key = key.strip()
            val = val.strip()
            if key and key not in fields:  # keep first occurrence
                fields[key] = val
    return fields


def parse_file(path: Path, language: str):
    rows = []
    seen_header = False
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or "\t" not in line:
                continue
            if line.startswith("Code\t") or line.startswith("===="):
                seen_header = True
                continue
            if not seen_header:
                continue
            m = CODE_ROW.match(line)
            if not m:
                continue
            code, desc = m.groups()
            # description column sometimes has extra trailing tab-separated
            # example text; keep only the first field after the code as the
            # canonical description (matches observed 2-col data pattern).
            desc_first = desc.split("\t")[0]
            rows.append({
                "language": language,
                "code": code.strip(),
                "description_raw": desc_first.strip(),
                "fields": parse_fields(desc_first),
            })
    return rows


def main():
    if len(sys.argv) != 4:
        print("Usage: parse_morphology.py <tegmc_file> <tehmc_file> <output_jsonl>", file=sys.stderr)
        sys.exit(1)

    tegmc_path, tehmc_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    g_rows = parse_file(tegmc_path, "greek")
    print(f"{tegmc_path.name}: {len(g_rows)} codes", file=sys.stderr)
    all_rows.extend(g_rows)

    h_rows = parse_file(tehmc_path, "hebrew")
    print(f"{tehmc_path.name}: {len(h_rows)} codes", file=sys.stderr)
    all_rows.extend(h_rows)

    with out_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"TOTAL: {len(all_rows)} codes -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
