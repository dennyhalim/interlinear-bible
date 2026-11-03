#!/usr/bin/env python3
"""
Find words whose AI gloss does not follow the translated lexicon gloss: for each
Strong's number, how often the AI gloss shares a stem (5+ characters, or the whole
word if shorter) with any sense of the lexicon gloss. Run it after glossing to spot
terms the AI keeps overriding (e.g. "baptis" where the lexicon says "benam").
Some drift is legitimate (the AI may pick another sense when context calls for it),
so read the list before regenerating. Same lexicon join as generate_ai_glosses.py.

Usage:
  find_term_drift.py --db output/interlinear.sqlite [--min-count 10] [--max-rate 0.5] [--top 40]
"""
import argparse
import collections
import re
import sqlite3
import sys
from pathlib import Path

SQL = """
WITH lx AS (
    SELECT w.word_id, le.lexicon_id,
           ROW_NUMBER() OVER (PARTITION BY w.word_id
                              ORDER BY (le.dstrong = w.strongs) DESC, le.lexicon_id) AS rn
    FROM word w
    JOIN ai_gloss ag ON ag.word_id = w.word_id
    JOIN lexicon_entry le ON le.dstrong = w.strongs OR le.estrong = w.strongs
)
SELECT w.strongs, ag.gloss, lgt.gloss
FROM lx
JOIN word w ON w.word_id = lx.word_id
JOIN ai_gloss ag ON ag.word_id = w.word_id
JOIN lexicon_gloss_translation lgt ON lgt.lexicon_id = lx.lexicon_id AND lgt.language = ?
WHERE lx.rn = 1
"""


def base(s):
    m = re.match(r"([HG])0*(\d+)", s or "")
    return f"{m[1]}{m[2]}" if m else None


def senses(lexicon_gloss):
    """'general: specific' / 'a/b' / 'a, b' -> lowercase senses, parenthetical hints removed."""
    text = re.sub(r"\([^)]*\)", " ", lexicon_gloss.lower())
    return [x.strip() for x in re.split(r"[/;,:]", text) if len(x.strip()) >= 2]


def follows(gloss, lexicon_senses, k=5):
    gloss = gloss.lower()
    for sense in lexicon_senses:
        n = min(k, len(sense))
        if any(sense[i:i + n] in gloss for i in range(len(sense) - n + 1)):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--language", default="id")
    ap.add_argument("--min-count", type=int, default=10, help="Ignore Strong's numbers with fewer glossed words")
    ap.add_argument("--max-rate", type=float, default=0.5, help="List those whose follow rate is below this")
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()

    if not a.db.is_file() or a.db.stat().st_size == 0:
        sys.exit(f"ERROR: database missing or empty: {a.db.resolve()}")
    conn = sqlite3.connect(a.db)

    total = collections.Counter()
    followed = collections.Counter()
    lex_text = {}
    drifted = collections.defaultdict(collections.Counter)
    for strongs, gloss, lex in conn.execute(SQL, (a.language,)):
        b = base(strongs)
        if not b or not gloss or gloss == "-" or gloss.startswith("("):
            continue
        sn = senses(lex)
        if not sn:
            continue
        total[b] += 1
        lex_text.setdefault(b, lex)
        if follows(gloss, sn):
            followed[b] += 1
        else:
            drifted[b][gloss] += 1

    rows = [(b, n, followed[b] / n) for b, n in total.items() if n >= a.min_count and followed[b] / n < a.max_rate]
    rows.sort(key=lambda r: r[1] * (1 - r[2]), reverse=True)
    print(f"{sum(total.values())} glossed words checked across {len(total)} Strong's numbers; "
          f"{len(rows)} with fewer than {a.max_rate:.0%} following the lexicon (min {a.min_count} words)\n")
    for b, n, rate in rows[:a.top]:
        top = ", ".join(f"{g} ({c})" for g, c in drifted[b].most_common(4))
        print(f"{b:<7} {n:>5} words  {rate:>4.0%} follow | lexicon: {lex_text[b][:40]} | AI used: {top}")
    if rows:
        print("\nTargeted regeneration of the worst offenders (dry run first):")
        print("  --strongs " + ",".join(b for b, _, _ in rows[:25]))


if __name__ == "__main__":
    main()
