#!/usr/bin/env python3
"""
Resolve which specific TIPNR individual/place a `word` row refers to,
for names that are ambiguous at the base-Strong's level (e.g. three
different Herods all tagged G2264 in the source text, disambiguated by
TIPNR into G2264G/G2264H/G2264I).

Why this is a separate pass, not part of build_db.py's main load:
it depends on both `word` (from TR/TAHOT) and `proper_noun_occurrence`
(from TIPNR) already being fully populated, since resolution works by
matching a word's (book, chapter, verse) location against the specific
verse list recorded for each candidate variant -- not just its Strong's
number, which is often shared across multiple individuals.

Algorithm, per candidate word (one whose base Strong's number matches
one or more proper_noun_variant.estrong values):
  1. Find all proper_noun_variant rows whose estrong matches the word's
     base Strong's number (e.g. word.strongs="G2264" -> matches G2264G,
     G2264H, G2264I candidates).
  2. If only one candidate variant exists for that estrong, it's
     unambiguous -- record the match with match_basis
     "estrong-only-unambiguous" (confidence: high, no verse data needed).
  3. If multiple candidates exist, narrow using proper_noun_occurrence:
     find which candidate(s) have a recorded occurrence at this word's
     exact (book, chapter, verse). If exactly one matches, record it
     with match_basis "estrong+verse" (confidence: high).
  4. If multiple candidates still match the same verse (e.g. two of the
     Herods both mentioned in the same verse), OR zero candidates have
     an occurrence recorded at this verse (TIPNR's AllRefs list may not
     be 100% exhaustive), the word is left unresolved rather than
     guessing -- better to have no answer than a silently wrong one.

Word's base Strong's number is derived by stripping any trailing
disambiguation letter(s) from word.strongs (e.g. "H7225G" -> base
"H7225" is NOT what we want here -- word.strongs for *names* in the
source text is typically already the bare base number like "G2264",
since TR/TAHOT don't apply TIPNR's disambiguation scheme. We match
word.strongs directly against proper_noun_variant.estrong.)

Usage:
  resolve_proper_nouns.py --db output/interlinear.sqlite
"""
import argparse
import sqlite3
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to interlinear.sqlite (modified in place)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # Clear any previous run's results so this script is safely re-runnable.
    cur.execute("DELETE FROM word_proper_noun")
    conn.commit()

    # Build an in-memory index: estrong -> list of (variant_id, noun_id)
    cur.execute("SELECT variant_id, noun_id, estrong FROM proper_noun_variant WHERE estrong IS NOT NULL AND estrong != ''")
    estrong_candidates = {}
    for variant_id, noun_id, estrong in cur.fetchall():
        estrong_candidates.setdefault(estrong, []).append((variant_id, noun_id))

    print(f"Loaded {len(estrong_candidates)} distinct estrong values with candidate variants", file=sys.stderr)

    # Build an in-memory index: (variant_id, book_code, chapter, verse) -> True
    # (sub_ref intentionally ignored for matching -- multiple occurrences of
    # the same individual within one verse, e.g. "a"/"b", both still count
    # as "this individual occurs in this verse" for our purposes)
    cur.execute("SELECT variant_id, book_code, chapter, verse FROM proper_noun_occurrence")
    occurrence_index = {}
    for variant_id, book_code, chapter, verse in cur.fetchall():
        occurrence_index.setdefault((book_code, chapter, verse), set()).add(variant_id)

    print(f"Loaded {len(occurrence_index)} distinct (book,chapter,verse) locations with occurrences", file=sys.stderr)

    # Walk every word whose strongs value matches at least one candidate.
    cur.execute("""
        SELECT w.word_id, w.strongs, ve.chapter, ve.verse, b.code
        FROM word w
        JOIN verse ve ON w.verse_id = ve.verse_id
        JOIN book b ON ve.book_id = b.book_id
        WHERE w.strongs IS NOT NULL
    """)
    all_words = cur.fetchall()
    print(f"Scanning {len(all_words)} words for proper-noun candidates", file=sys.stderr)

    resolved_unambiguous = 0
    resolved_by_verse = 0
    unresolved_ambiguous = 0
    inserts = []

    for word_id, strongs, chapter, verse, book_code in all_words:
        candidates = estrong_candidates.get(strongs)
        if not candidates:
            continue  # this word's Strong's number isn't a proper noun at all

        if len(candidates) == 1:
            variant_id, noun_id = candidates[0]
            inserts.append((word_id, noun_id, variant_id, "estrong-only-unambiguous"))
            resolved_unambiguous += 1
            continue

        # Multiple candidate individuals share this base Strong's number;
        # narrow by checking which candidate(s) have a recorded occurrence
        # at this exact verse.
        key = (book_code, chapter, verse)
        variant_ids_at_verse = occurrence_index.get(key, set())
        matches = [
            (variant_id, noun_id)
            for variant_id, noun_id in candidates
            if variant_id in variant_ids_at_verse
        ]

        if len(matches) == 1:
            variant_id, noun_id = matches[0]
            inserts.append((word_id, noun_id, variant_id, "estrong+verse"))
            resolved_by_verse += 1
        else:
            # 0 matches (TIPNR's AllRefs didn't record this exact verse for
            # any candidate -- can happen with sub-verse "a"/"b" refs or
            # minor data gaps) or 2+ matches (genuinely ambiguous within
            # the same verse) -- leave unresolved rather than guess.
            unresolved_ambiguous += 1

    cur.executemany(
        "INSERT INTO word_proper_noun (word_id, noun_id, variant_id, match_basis) VALUES (?,?,?,?)",
        inserts,
    )
    conn.commit()

    print(f"Resolved (single candidate): {resolved_unambiguous}", file=sys.stderr)
    print(f"Resolved (by verse match):   {resolved_by_verse}", file=sys.stderr)
    print(f"Left unresolved (ambiguous or no verse match): {unresolved_ambiguous}", file=sys.stderr)
    print(f"Total word_proper_noun rows: {len(inserts)}", file=sys.stderr)

    conn.close()


if __name__ == "__main__":
    main()
