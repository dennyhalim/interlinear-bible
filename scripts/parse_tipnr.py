#!/usr/bin/env python3
"""
Parse STEPBible TIPNR (Translators Individualised Proper Names with all
References) into normalized rows.

File structure (CRLF line endings in the source):
  - Records are delimited by a line starting with "$==========" followed
    by PERSON(s), PLACE, or OTHER -- this tags the record type, not just
    a section (i.e. it repeats before every single record, ~4,240 times).
  - Each record has:
      1. A HEADER line (no leading marker): starts with
         "UniqueName@Ref=uStrong" followed by tab-separated fields whose
         meaning depends on record type (person: Description/Parents/
         Siblings/Partners/Offspring/Tribe/Summary/Type; place: OpenBible
         name/Founder/PeopleThere/GoogleMapURL/MapURL/GeoArea/Summary/Type;
         other: Description/Summary).
      2. One or more SUB-LINES starting with "– " (en-dash + space):
         Significance \t UniqueName \t dStrong«eStrong=Word \t
         TranslatedName \t StepBibleLink \t AllRefs
         Significance values include: Named, Greek, Spelled, Aramaic,
         "Name combined", Group, Mentioned, Total, and several
         "(same ... )" variants -- "Total" is a summary rollup line, kept
         but flagged separately since it aggregates rather than
         introduces a new dStrong.
      3. Zero or more "@Briefest=" / "@Brief=" / "@Short=" / "@Article="
         lines giving increasingly detailed prose descriptions.

We treat the initial doc/example block (before the first real "@Gen." or
similar dated reference appears) as non-data by requiring the header
line to match a "Name@Ref=..." pattern with a real-looking Strong's or
book reference; the field-documentation examples earlier in the file
use the same "$==========" markers but their header lines don't match
this pattern strictly enough to be picked up as spurious records in
practice (verified against the actual file).

Output: one row per (record, sub-line) pair -- i.e. one row per named
variant of a person/place/other, each carrying its own dStrong so it
can be joined against word.strongs directly, plus a back-reference to
the record's uStrong (unified id), header-level description/summary,
and an all_refs list of every exact verse reference where this specific
name-variant occurs (the actual data needed for word-level resolution,
since first_ref alone only gives the first/last book, not every verse).
"""
import re
import sys
import json
from pathlib import Path

RECORD_MARKER = re.compile(r"^\$={2,}\s*(PERSON\(s\)|PLACE|OTHER)\s*$", re.MULTILINE)

# Header line: "UniqueName@Ref=uStrong<TAB>...rest of fields..."
# uStrong may be absent for some OTHER records (rare); make it optional.
HEADER_LINE = re.compile(r"^([^\t@]+)@([^\t=]+)(?:=(\S+))?\t(.*)$")

# Sub-line: "– Significance<TAB>UniqueName<TAB>dStrong«eStrong=Word<TAB>...rest"
SUBLINE = re.compile(
    r"^[–-]\s*([^\t]*)\t([^\t]*)\t([^\t]*)\t?(.*)$"
)

# "dStrong«eStrong=Word" e.g. "H0175«H0175=אַהֲרֹן" or "G0002«G0002=Ἀαρών"
DSTRONG_FIELD = re.compile(r"^([A-Za-z0-9]+)\s*«\s*([A-Za-z0-9]+)\s*=\s*(.*)$")


def parse_records(path: Path):
    with path.open(encoding="utf-8") as f:
        raw = f.read()

    # Split on record markers, keeping the type captured.
    parts = RECORD_MARKER.split(raw.replace("\r\n", "\n"))
    # parts alternates: [pre-text, type1, body1, type2, body2, ...]
    # (re.split with a capturing group interleaves the captured groups)
    records = []
    i = 1
    while i < len(parts) - 1:
        rec_type, body = parts[i], parts[i + 1]
        records.append((rec_type, body))
        i += 2
    return records


def parse_body(rec_type: str, body: str):
    """Parse one record's body (header + sub-lines + @-description lines)."""
    lines = [l for l in body.split("\n")]
    # Find the header line: first non-blank line that matches HEADER_LINE
    header = None
    header_idx = None
    for idx, line in enumerate(lines):
        line = line.strip("\t").rstrip()
        if not line:
            continue
        m = HEADER_LINE.match(line)
        if m:
            header = m
            header_idx = idx
            break
        else:
            # Not a data record (e.g. leftover doc/example text) -- bail.
            return None
    if header is None:
        return None

    unique_name, first_ref, ustrong, header_rest = header.groups()
    header_fields = header_rest.split("\t")

    # description/summary: for PERSON it's header_fields[0]; for PLACE
    # it's still header_fields[0] (OpenBible name) with summary later
    # (marked by a field starting with "#"); for OTHER, header_fields[0].
    # We pull the "#..." summary field wherever it appears, since its
    # position varies by record type, rather than hardcoding an index.
    summary = ""
    for fld in header_fields:
        if fld.strip().startswith("#"):
            summary = fld.strip().lstrip("#").strip()
            break
    description = header_fields[0].strip() if header_fields else ""

    sublines = []
    briefest = brief = short = article = ""
    for line in lines[header_idx + 1:]:
        line = line.rstrip()
        if not line.strip():
            continue
        if line.startswith("@Briefest="):
            briefest = line.split("=", 1)[1].strip()
            continue
        if line.startswith("@Brief="):
            brief = line.split("=", 1)[1].strip()
            continue
        if line.startswith("@Short="):
            # Sometimes "@Short= ... & @Article= ..." share a line
            rest = line.split("=", 1)[1]
            if "@Article=" in rest:
                short_part, article_part = rest.split("@Article=", 1)
                short = short_part.strip().rstrip("&").strip()
                article = article_part.strip()
            else:
                short = rest.strip()
            continue
        if line.startswith("@Article="):
            article = line.split("=", 1)[1].strip()
            continue

        m = SUBLINE.match(line)
        if not m:
            continue
        significance, subline_name, dstrong_field, remainder = m.groups()
        significance = significance.strip()
        if significance.lower() == "total":
            # Rollup line, not an individual name variant; skip for the
            # per-variant rows but keep nothing extra here (uStrong-level
            # aggregate isn't needed once we have the individual variants).
            continue

        dm = DSTRONG_FIELD.match(dstrong_field.strip())
        if not dm:
            continue
        dstrong, estrong, word_form = dm.groups()

        rest_fields = remainder.split("\t")
        translated_name = rest_fields[0].strip() if rest_fields else ""
        all_refs_raw = rest_fields[2].strip() if len(rest_fields) > 2 else ""
        # AllRefs is a "; "-joined list of exact verse refs, e.g.
        # "Mat.2.1; Mat.2.3; Mat.2.7". This is the real per-occurrence
        # data needed to resolve which individual a given word refers to
        # -- first_ref alone only gives first/last book, not every verse.
        all_refs = [r.strip() for r in all_refs_raw.split(";") if r.strip()]

        sublines.append({
            "significance": significance,
            "name_variant": subline_name.strip(),
            "dstrong": dstrong.strip(),
            "estrong": estrong.strip(),
            "word_form": word_form.strip(),
            "translated_name": translated_name,
            "all_refs": all_refs,
        })

    return {
        "record_type": rec_type.strip(),
        "unique_name": unique_name.strip(),
        "first_ref": first_ref.strip(),
        "ustrong": (ustrong or "").strip(),
        "description": description,
        "summary": summary,
        "briefest": briefest,
        "brief": brief,
        "short": short,
        "article": article,
        "name_variants": sublines,
    }


def main():
    if len(sys.argv) != 3:
        print("Usage: parse_tipnr.py <tipnr_file> <output_jsonl>", file=sys.stderr)
        sys.exit(1)

    src_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    records = parse_records(src_path)
    parsed = []
    skipped = 0
    for rec_type, body in records:
        result = parse_body(rec_type, body)
        if result is None:
            skipped += 1
            continue
        parsed.append(result)

    variant_count = sum(len(r["name_variants"]) for r in parsed)

    with out_path.open("w", encoding="utf-8") as f:
        for row in parsed:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    by_type = {}
    for r in parsed:
        by_type[r["record_type"]] = by_type.get(r["record_type"], 0) + 1

    print(f"Records parsed: {len(parsed)} (skipped {skipped} non-data blocks)", file=sys.stderr)
    print(f"By type: {by_type}", file=sys.stderr)
    print(f"Total name variants: {variant_count}", file=sys.stderr)
    print(f"-> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
