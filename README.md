# interlinear-bible

Builds a SQLite database for a Hebrew (Masoretic Text) + Greek (Textus
Receptus) interlinear Bible, sourced entirely from public-domain / CC BY
licensed data. A GitHub Action rebuilds `output/interlinear.sqlite` on
every push to `scripts/` and publishes it as a build artifact and, on
tagged releases, as a stable release download URL.

## Sources (verified, not assumed — see notes below)

| Data | Source | License |
|---|---|---|
| Greek NT text (Textus Receptus) + Strong's + morphology | [byztxt/greektext-textus-receptus](https://github.com/byztxt/greektext-textus-receptus) (Robinson) | Public domain |
| Hebrew OT text (Masoretic/Leningrad) + Strong's + morphology | [STEPBible/STEPBible-Data](https://github.com/STEPBible/STEPBible-Data) — TAHOT | CC BY 4.0 |
| Greek lexicon (Abbott-Smith / LSJ-derived) | STEPBible-Data — TBESG / TFLSJ | CC BY 4.0 |
| Hebrew lexicon (BDB-derived) | STEPBible-Data — TBESH | CC BY 4.0 |
| Morphology code expansion | STEPBible-Data — TEGMC / TEHMC | CC BY 4.0 |
| Versification differences (planned, not yet parsed) | STEPBible-Data — TVTMS | CC BY 4.0 |
| Proper noun disambiguation | STEPBible-Data — TIPNR | CC BY 4.0 |

Every source above was pulled and inspected row-by-row before being wired
into the pipeline (see project history) — none of this was taken on faith
from a README description alone.

## Setup

```bash
git submodule add https://github.com/byztxt/greektext-textus-receptus.git sources/tr
git submodule add https://github.com/STEPBible/STEPBible-Data.git sources/stepbible
git submodule update --init --recursive
```

## Build locally

```bash
python3 scripts/parse_tr.py sources/tr/parsed output/staging/tr.jsonl
python3 scripts/parse_tahot.py "sources/stepbible/Translators Amalgamated OT+NT" output/staging/tahot.jsonl
python3 scripts/parse_lexicons.py \
  "sources/stepbible/Lexicons/TBESG - Translators Brief lexicon of Extended Strongs for Greek - STEPBible.org CC BY.txt" \
  "sources/stepbible/Lexicons/TBESH - Translators Brief lexicon of Extended Strongs for Hebrew - STEPBible.org CC BY.txt" \
  output/staging/lexicons.jsonl
python3 scripts/parse_morphology.py \
  "sources/stepbible/Morphology codes/TEGMC - Translators Expansion of Greek Morphhology Codes - STEPBible.org CC BY.txt" \
  "sources/stepbible/Morphology codes/TEHMC - Translators Expansion of Hebrew Morphology Codes - STEPBible.org CC BY.txt" \
  output/staging/morphology.jsonl
python3 scripts/parse_tipnr.py \
  "sources/stepbible/Proper Nouns/TIPNR - Translators Individualised Proper Names with all References - STEPBible.org CC BY.txt" \
  output/staging/tipnr.jsonl
python3 scripts/build_db.py --staging output/staging --schema schema.sql --out output/interlinear.sqlite
python3 scripts/resolve_proper_nouns.py --db output/interlinear.sqlite
```

## Schema

See `schema.sql`. Key tables: `book`, `verse`, `word` (one row per
Greek word or Hebrew morpheme), `lexicon_entry`, `morphology_code`.
`ai_gloss`, `versification_note`, and `proper_noun` are reserved for
later pipeline stages (not yet populated).

## Known gaps / next steps

- **AI gloss generation** — `ai_gloss` table exists but is unpopulated.
  Planned: batch by verse, feed word + lemma + morph + lexicon entry +
  verse context to Claude, store structured `{gloss, alt_glosses, note}`
  per word. Not yet implemented in this repo.
- **Compound morph codes** — fixed. Hebrew grammar codes like `HR/Ncfsa`
  now split into `word_morph_part` rows and resolve at 99.98% against
  `morphology_code`.
- **Trailing punctuation artifacts** — fixed. Verse-end markers like `\׃`
  are stripped from `word.surface`; `punct_tag` still captures the same
  info separately for anyone who needs it.
- **TVTMS versification mapping** — not yet parsed; format is a
  rule-based action table (Keep/Concatenation/Renumber/etc.), not a
  simple lookup, and needs its own dedicated parser.
- **TIPNR proper nouns** — fixed. `resolve_proper_nouns.py` resolves
  ambiguous names (e.g. three different Herods sharing base Strong's
  `G2264`) by matching each word's exact `(book, chapter, verse)` against
  TIPNR's per-variant `AllRefs` occurrence list (captured in
  `proper_noun_occurrence`). Verified against all three Herods (Mat.2.1
  → Herod the Great, Mat.14.1 → Herod Antipas, Act.12.1 → Herod Agrippa I)
  resolving to the correct distinct individuals. Result: 21,394 words
  resolved (10,986 unambiguous single-candidate, 10,408 by verse match),
  406 left unresolved rather than guessed (mostly non-proper-noun senses
  of a shared Strong's number, e.g. G0129 "blood" also appearing as an
  OTHER-category TIPNR entry). Stored in `word_proper_noun`, run as a
  separate pass after `build_db.py` since it depends on both `word` and
  `proper_noun_occurrence` already being populated.
- **TSK cross-references** — public domain, not in STEPBible-Data; would need
  a separate source (e.g. an OSIS/SWORD TSK module). Not used for translation
  (it's a study cross-reference system, verse-to-verse thematic links), but
  flagged as a useful **app-layer feature** for later — e.g. tapping a verse
  to see related passages. Would need its own `cross_reference` table and
  parser, deliberately kept separate from the translation pipeline above.
- **TR vs TAGNT cross-check** — byztxt TR and STEPBible TAGNT are
  different text traditions (TR vs. eclectic/eclectic-amalgam); TAGNT
  is not currently used for the NT text itself, only cross-checked
  during development.
