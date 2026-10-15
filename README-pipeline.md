## Pipeline overview (start to exports)

```
sources (TR, TAHOT, lexicons, morphology, TIPNR, categories)
   │  1. parse_* + build_db + post-build fixes
   ▼
interlinear.sqlite            English glosses, Strong's, morphology
   │  2. translate lexicon      (CSV, human review)     -> lexicon_gloss_translation
   │  3. contextual word glosses (per chapter)          -> ai_gloss
   │  4. natural verse translation + Strong's tags      -> verse_translation
   ▼
exports: Markdown / Obsidian / DOCX / MySword / e-Sword / OSIS / D1 + API
```

Each AI stage reads the previous one, so run them in order. Every AI stage
writes to a JSONL/CSV file first, and a separate `load` step puts it in the
database, so you can review before anything is stored.

**Keep your staging files.** `build_db.py` deletes and recreates the
database, which empties `lexicon_gloss_translation`, `ai_gloss` and
`verse_translation`. After any rebuild, reload them from
`output/staging/lexicon_id.csv`, `ai_glosses_id.jsonl` and `verses_id.jsonl`
(stages 2c, 3c and 4c below). All loaders are idempotent upserts.

### Requirements

- Python 3.12+, `pip install anthropic` (AI stages only)
- `pandoc` (DOCX export only), `wrangler` (D1 deploy only)
- `ANTHROPIC_API_KEY` set in the environment for AI stages
- Pass `--model` explicitly (e.g. `claude-opus-5-5`); script defaults differ
  between scripts and may be outdated.

### Gloss priority

Exporters that use `bible_export_common.py` pick each word's gloss in this
order, and fall back to the first sense of a multi-sense lexicon gloss:

1. `ai_gloss` (contextual, stage 3)
2. `lexicon_gloss_translation` (translated lexicon, stage 2)
3. English `lexicon_entry.gloss`
4. TAHOT's own `gloss_source` (Hebrew only)

`export_obsidian_vault.py` still has its own English-first lookup; switch it to
`bible_export_common.get_words_for_verse` if you want Indonesian there.

---

### Stage 1: build the database

```bash
git submodule update --init --recursive

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
```

Post-build steps (run after **every** rebuild, in this order):

```bash
python3 scripts/fix_translit_overrides.py --db output/interlinear.sqlite   # YHWH transliteration/gloss corrections
python3 scripts/resolve_proper_nouns.py --db output/interlinear.sqlite     # word_proper_noun (e.g. which Herod)
python3 scripts/load_lexicon_categories.py --db output/interlinear.sqlite --jsonl sources/strong_categories.jsonl
```

Optional, OT versification mapping (Hebrew vs. English numbering; not run by CI):

```bash
python3 scripts/parse_tvtms.py --tvtms "sources/stepbible/Versification/TVTMS - Translators Versification Traditions with Methodology for Standardisation for Eng+Heb+Lat+Grk+Others - STEPBible.org CC BY.txt" --out output/staging/tvtms_ot.jsonl
python3 scripts/load_versification.py --db output/interlinear.sqlite --jsonl output/staging/tvtms_ot.jsonl
```

The GitHub Action runs stage 1 only (on `schema.sql` changes, tags, manual
dispatch, and a weekly schedule). It never contains stages 2 to 4, because
those are generated locally.

### Stage 2: translate the lexicon (~22,716 entries)

Translating the lexicon once keeps the same Hebrew/Greek word on the same
Indonesian term everywhere.

```bash
# 2a. Export (sorted by usage, most-used first)
python3 scripts/export_lexicon_csv.py --db output/interlinear.sqlite --out output/staging/lexicon_id.csv --language id

# 2b. Translate: sync, or Batch API (50% cheaper, async)
python3 scripts/translate_lexicon.py sync --csv output/staging/lexicon_id.csv --language id --model claude-opus-5-5
#   or: translate_lexicon.py submit ...  then later  translate_lexicon.py retrieve --csv ... --language id

# Review/edit the gloss_id column in Excel/Sheets. Re-running 2b never overwrites filled rows.
# Save as "CSV UTF-8 (Comma delimited)" so Hebrew/Greek survive.

# 2c. Load
python3 scripts/load_lexicon_translations.py --db output/interlinear.sqlite \
  --csv output/staging/lexicon_id.csv --language id --model claude-opus-5-5
```

Use `--model manual` if the CSV was hand-translated. The `model` label is
applied to the whole load run.

### Stage 3: contextual word glosses (one chapter per call)

Picks the single sense that fits each verse, starting from the stage 2 gloss.

```bash
# Preview only (writes Markdown, never touches the database)
python3 scripts/test_gloss_chapter.py --db output/interlinear.sqlite \
  --book JHN --chapter 1 --language id --out-dir output/staging/gloss_review --model claude-opus-5-5

# 3a/3b. Generate: sync, or Batch API
python3 scripts/generate_ai_glosses.py sync --db output/interlinear.sqlite \
  --out output/staging/ai_glosses_id.jsonl --language id --model claude-opus-5-5 [--book GEN | --group torah | --testament NT]
#   or: submit (same flags)  then  retrieve --out output/staging/ai_glosses_id.jsonl --language id

# 3c. Load
python3 scripts/load_ai_glosses.py --db output/interlinear.sqlite --jsonl output/staging/ai_glosses_id.jsonl
```

Scope flags `--book`, `--group`, `--testament` are mutually exclusive;
`--limit-chapters N` is for testing. Runs are resumable.

### Stage 4: natural verse translation with Strong's tags

Turns the stage 3 glosses into one literal, readable Indonesian sentence per
verse. Each translated word is followed by its Strong's number, for example
`Pada mula{G746}`.

```bash
# 4a/4b. Generate (test one chapter first)
python3 scripts/generate_verse_translations.py sync --db output/interlinear.sqlite \
  --out output/staging/verses_id.jsonl --book JHN --chapter 1 --model claude-opus-5-5
#   full run: submit --db ... --out ... --model ...   then   retrieve --out ...

# 4c. Validate tags and load
python3 scripts/generate_verse_translations.py load --db output/interlinear.sqlite --jsonl output/staging/verses_id.jsonl
```

`load` checks every tag against the verse's real Strong's numbers, drops
unknown ones, and reports verses with no tags or missing source numbers;
review those. Text is stored tagged in `verse_translation.text`;
`bible_export_common.get_verse_translation()` strips tags unless asked for
them. `--chapter` accepts `all` or ranges like `1-3,5`. Already-translated
verses are skipped, so delete old rows/JSONL before regenerating with a new
prompt version.

### Stage 5: exports

Re-export after any gloss or translation change: MySword, e-Sword and OSIS
files bake the text in at export time.

| Target | Command | Notes |
|---|---|---|
| NotebookLM (interlinear Markdown) | `export_to_markdown.py --db ... --out-dir output/markdown --group` | 8 groups, under the 500,000-word cap |
| Claude Project (running text) | `export_to_markdown.py ... --compact --group` | Natural translation, falls back to joined glosses; add `--strongs` to keep `{G746}` tags |
| Obsidian (flat notes) | `export_to_markdown.py ... --group --wikilink` | Links only resolve if notes exist |
| Obsidian vault (working links) | `export_obsidian_vault.py --db ... --out-dir output/obsidian_vault` | One note per chapter |
| Word / OneNote | `export_to_docx.py --db ... --out-dir output/docx --group` | Needs pandoc |
| MySword, interlinear | `export_mysword.py --db ... --out output/exports/id_interlinear.bbl.mybible --language id --layout interlinear --description "..." --abbreviation VAII` | Word-by-word `<Q>` blocks |
| MySword, word-tagged | same with `--layout plain` | Gloss text with `<WH/WG####>` per word |
| MySword, natural | same with `--layout natural` | Stage 4 sentence with Strong's tags; verses without a translation are skipped |
| e-Sword | `export_esword.py --db ... --out output/exports/id_plain.bblx --language id --layout plain --description "..." --abbreviation VAI` | `interlinear` renders an HTML table, reliable only on Mac/iOS |
| OSIS (SWORD source) | `export_osis.py --db ... --out output/exports/id.osis.xml --language id --title "..."` | Needs `osis2mod` to compile; verse numbers use standard English via `versification_mapping` (`--no-remap` to disable) |
| Lexicon CSV | `export_lexicon_csv.py ...` | For translation/review (stage 2) |

Add `--book GEN` to most exporters to test on a single book first.

### Stage 6: deploy (Cloudflare D1 + read API)

```bash
./scripts/deploy_to_d1.sh <db-name> --fresh                                   # first deploy / schema change
./scripts/deploy_to_d1.sh <db-name> --fresh --tables lexicon_gloss_translation,ai_gloss,verse_translation
```

D1's free tier allows 100,000 rows written per day, so deploy the big static
tables once and the incremental tables on their own. Add `verse_translation`
and `lexicon_gloss_translation` to `TABLE_ORDER` in `export_to_d1.py` and
`DROP_ORDER` in `generate_d1_reset.py`, or they are appended with a warning.

**Deploy translation tables from your machine, not from CI.** The workflow's
`deploy-d1` job uses the database built in CI, which contains stage 1 only.
Deploying `lexicon_gloss_translation`, `ai_gloss` or `verse_translation` from
there with `--fresh` would replace them with empty tables.
