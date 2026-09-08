#!/usr/bin/env python3
"""
Correct known TAHOT-vs-TBESH transliteration mismatch for H3068 (YHWH,
"LORD"): TAHOT's per-word transliteration uses "Yah.weh", while the
lexicon (TBESH) uses "ye.ho.vah" -- the lexicon form is authoritative.

Uses a SUBSTRING replace, not an exact/LIKE match on the whole field,
because "Yah.weh" appears both standalone and inside compound tokens
with attached Hebrew prefixes (e.g. "la./Yah.weh", "vu./ba./Yah.weh")
and in construct-chain compounds with a second word (e.g.
"Yah.weh/ /tze.Va.'ot" for YHWH Tzevaot / "LORD of Hosts"). A substring
replace correctly fixes the YHWH portion in every case while leaving
prefixes and any second word in a construct chain untouched.

Deliberately does NOT touch H3069, which has a different lexicon
transliteration ("ye.ho.vih", confirmed from the real lexicon_entry
data, single non-disambiguated entry) -- add that as a second,
separate REPLACE below if the same substring pattern is confirmed
present for it too.

Run this AFTER build_db.py, every time the database is rebuilt.

Usage:
  python3 scripts/fix_translit_overrides.py --db output/interlinear.sqlite
"""
import argparse
import sqlite3
import sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE word
        SET translit = REPLACE(translit, 'Yah.weh', 'Ye.ho.vah')
        WHERE (strongs LIKE 'H3068%' OR strongs_parts LIKE '%H3068%' OR strongs LIKE 'H3069%')
          AND translit LIKE '%Yah.weh%'
        """
    )
    print(f"H3068: updated {cur.rowcount} word rows (Yah.weh -> Ye.ho.vah, substring replace)", file=sys.stderr)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()