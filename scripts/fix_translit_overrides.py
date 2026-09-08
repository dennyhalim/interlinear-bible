#!/usr/bin/env python3
"""
Correct known TAHOT-vs-TBESH transliteration mismatches for the divine
name Strong's numbers H3068 (YHWH, "LORD") and H3069 (YHWH/God,
vocalized as "Elohim"). TAHOT's per-word transliteration and TBESH's
lexicon transliteration are independently maintained and don't
guarantee agreement -- the lexicon form is treated as authoritative
here.

Verified against real data (screenshots of actual lexicon_entry and
word rows), not assumed:
  H3068: lexicon translit "ye.ho.vah" (consistent across all three
         disambiguated entries H3068G/H/I). TAHOT's per-word form,
         "Yah.weh", appears standalone AND inside compound tokens with
         attached Hebrew prefixes (la./, va./, ba./, me./, ka./, ha./,
         vu./ba./, she./, vu./me./, ve./la./), a hyphenated form
         ("Yah.weh-"), and a construct-chain compound with a second
         word ("Yah.weh/ /tze.Va.'ot" = YHWH Tzevaot, "LORD of
         Hosts"). Fixed via SUBSTRING replace so every variant is
         corrected in one pass while prefixes/second-words are left
         untouched. Confirmed after running: all 13 Yah.weh-containing
         variants correctly became Ye.ho.vah; "ha.shem" (a genuinely
         different word -- the traditional substitute reading "the
         Name", not a transliteration of YHWH at all) correctly did
         NOT match and was left alone.
  H3069: lexicon translit "ye.ho.vih" (single non-disambiguated
         entry). TAHOT's per-word form: exactly one distinct value,
         "Yah.weh", no compound variants. Fixed via exact match/direct
         assignment (no substring needed).

Run this AFTER build_db.py, every time the database is rebuilt -- this
is a real data correction, not a one-off manual edit, so it must
survive rebuilds from source.

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

    # H3068: substring replace, preserves prefixes/construct-chains.
    cur.execute(
        """
        UPDATE word
        SET translit = REPLACE(translit, 'Yah.weh', 'Ye.ho.vah')
        WHERE (strongs LIKE 'H3068%' OR strongs_parts LIKE '%H3068%')
          AND translit LIKE '%Yah.weh%'
        """
    )
    print(f"H3068: updated {cur.rowcount} word rows (Yah.weh -> Ye.ho.vah, substring replace)", file=sys.stderr)

    # H3069: exact match, single known variant, no compounds observed.
    cur.execute(
        """
        UPDATE word
        SET translit = 'Ye.ho.vih'
        WHERE (strongs LIKE 'H3069%' OR strongs_parts LIKE '%H3069%')
          AND translit = 'Yah.weh'
        """
    )
    print(f"H3069: updated {cur.rowcount} word rows (Yah.weh -> Ye.ho.vih, exact match)", file=sys.stderr)

    cur.execute(
        """
        UPDATE lexicon_entry
        SET gloss = 'JEHOVAH'
        WHERE (dstrong = 'H3068G' OR dstrong = 'H3069')
        """
    )
    print(f"updated {cur.rowcount} word rows", file=sys.stderr)


    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()