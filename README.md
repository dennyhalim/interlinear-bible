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
| Proper noun disambiguation (planned, not yet parsed) | STEPBible-Data — TIPNR | CC BY 4.0 |

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
python3 scripts/build_db.py --staging output/staging --schema schema.sql --out output/interlinear.sqlite
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
- **Compound morph codes** — Hebrew grammar codes like `HR/Ncfsa` (prefix
  + stem) aren't split before joining against `morphology_code`, which
  is keyed on single codes. Needs a small join-time split on `/`.
- **Trailing punctuation artifacts** — verse-end markers like `\׃`
  sometimes remain attached to the last Hebrew word's `surface` field;
  should be stripped for clean display (kept for now since `punct_tag`
  already captures the same info separately).
- **TVTMS versification mapping** — not yet parsed; format is a
  rule-based action table (Keep/Concatenation/Renumber/etc.), not a
  simple lookup, and needs its own dedicated parser.
- **TIPNR proper nouns** — not yet parsed.
- **TR vs TAGNT cross-check** — byztxt TR and STEPBible TAGNT are
  different text traditions (TR vs. eclectic/eclectic-amalgam); TAGNT
  is not currently used for the NT text itself, only cross-checked
  during development.
