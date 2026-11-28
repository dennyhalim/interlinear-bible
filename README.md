# interlinear-bible

Builds a SQLite database for a Hebrew (Masoretic Text) + Greek (Textus Receptus) interlinear Bible, sourced entirely
from public-domain / CC BY licensed data, with Indonesian glosses and a natural Indonesian translation generated on top.
A GitHub Action rebuilds `output/interlinear.sqlite` (stage 1 only, English glosses) on changes to `schema.sql`, on
`v*` tags, on manual dispatch and weekly, and publishes it as a build artifact, as Markdown on the `db` branch and, on
tagged releases, as a release download. The Indonesian stages (translated lexicon, contextual glosses, verse
translation) are generated locally; see the pipeline below, and `RUNBOOK.md` for the commands to run next.

## Turso libSQL db

Turso db accessible from url
```bash
libsql://bible-dennyhalim.aws-ap-northeast-1.turso.io
```

You can just download and create your own Turso libsql
```bash
turso db import interlinear.sqlite
```

## Sources (verified, not assumed)

| Data | Source | License |
|---|---|---|
| Greek NT text (Textus Receptus) + Strong's + morphology | [byztxt/greektext-textus-receptus](https://github.com/byztxt/greektext-textus-receptus) (Robinson) | Public domain |
| Hebrew OT text (Masoretic/Leningrad) + Strong's + morphology | [STEPBible/STEPBible-Data](https://github.com/STEPBible/STEPBible-Data): TAHOT | CC BY 4.0 |
| Greek lexicon (Abbott-Smith / LSJ-derived) | STEPBible-Data: TBESG / TFLSJ | CC BY 4.0 |
| Hebrew lexicon (BDB-derived) | STEPBible-Data: TBESH | CC BY 4.0 |
| Morphology code expansion | STEPBible-Data: TEGMC / TEHMC | CC BY 4.0 |
| Versification differences (reference table `versification_mapping`, OT only) | STEPBible-Data: TVTMS | CC BY 4.0 |
| Proper noun disambiguation | STEPBible-Data: TIPNR | CC BY 4.0 |
| Semantic domain categories | [crizin/bible-db](https://github.com/crizin/bible-db) `strong_categories.jsonl` | see that repository |
| Indonesian spelling rules for the natural translation | EYD Edisi V (SK 0424/I/BS.00.01/2022), distilled in `sources/eydv.md` | official publication |

Every source was inspected row by row before it was wired into the pipeline, not taken from a README description.

## Setup

```bash
git submodule add https://github.com/byztxt/greektext-textus-receptus.git sources/tr
git submodule add https://github.com/STEPBible/STEPBible-Data.git sources/stepbible
git submodule update --init --recursive
```

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

#### Which stage does each export need?

What each stage adds to the text:

| Stage | Adds | Text you get |
|---|---|---|
| 1 | database | English glosses (a lexicon gloss may list several senses; the first one is used) |
| 2 | translated lexicon | Indonesian dictionary glosses, one per Strong's number |
| 3 | `ai_gloss` | one Indonesian sense per word, chosen for the verse |
| 4 | `verse_translation` | a natural sentence per verse, with Strong's tags |

Every export runs after stage 1 except MySword `natural`. Later stages only
improve the text, and they work per word and per verse, so a partly processed
Bible still exports. A word without an `ai_gloss` falls back to stage 2, then
stage 1, and a verse without a stage 4 sentence falls back to its joined glosses.

| Export | Earliest | Improved by | Stage 4 |
|---|---|---|---|
| Lexicon CSV | 1 | n/a (it is the input to stage 2) | no |
| Interlinear Markdown (NotebookLM, and Obsidian flat with `--wikilink`) | 1 | 2, 3 | not used |
| Compact Markdown (Claude Project) | 1 | 3, 4 | used if present (plain text); `--strongs` needs it |
| Obsidian vault | 1 | 3 | not used; it ignores stage 2 and uses English fallbacks |
| DOCX | 1 | 2, 3 | not used |
| MySword `interlinear` / `plain` | 1 | 2, 3 | not used |
| MySword `natural` | **4** | 3 (before stage 4) | required; verses without a sentence are skipped |
| e-Sword | 1 | 2, 3 | not used |
| OSIS | 1 | 2, 3, plus optional versification mapping (stage 1 extras) | not used |
| D1 deploy / read API | 1 | each stage fills its own table | the table deploys, but the API does not read it |

The weekly CI build and the `db` branch publish stop at stage 1, so those
outputs contain English glosses only.

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

## Schema and verse numbering

See `schema.sql`. Key tables: `book`; `verse` (standard English numbering, with Psalm titles as verse 0); `word` (one row
per source word; Hebrew rows can carry several Strong's parts); `lexicon_entry`; `lexicon_gloss_translation`
(Indonesian lexicon glosses); `morphology_code` and `word_morph_part`; `ai_gloss` (contextual word glosses);
`verse_translation` (natural verse sentences with Strong's tags); `proper_noun*` and `word_proper_noun`;
`lexicon_category`. `versification_mapping` is reference data (OT only); `versification_note` is reserved.

**Numbering.** TAHOT lists the standard English reference first and the Hebrew reference in brackets when the two differ
(for example `Psa.67.1(67.2)`). `parse_tahot.py` keeps the English reference, so Malachi 4, Joel 3 and the 150 Psalms
are all present, and a Psalm title is verse 0. When two Hebrew verses map to one English verse (Num 26:1 = Heb 25:19
and 26:1), the positions of the later part continue after the earlier one, so no word is dropped. Rows flagged `X` in
TAHOT (152 words from other manuscripts, not in the Leningrad text) are excluded, because the MT is the source of truth.
Expected totals: OT 39 books, 929 chapters, 23,261 verses, 305,500 words; NT 27 books, 260 chapters, 7,957 verses,
140,937 words. `build_db.py` prints these per testament and warns when the chapter count differs.

The optional TVTMS table (`parse_tvtms.py`, `load_versification.py`) records where Hebrew and English numbering
differ. Nothing in the exporters remaps through it any more, since `verse` already holds English numbering.

## Deploying to Cloudflare D1

The database (about 100 MB) fits comfortably within D1's limits (10 GB per database on the paid plan); no need to
shard or split it.

**One-time setup:**
```bash
npm install -g wrangler
wrangler login
wrangler d1 create interlinear-bible-db
# copy the printed database_id into wrangler.toml
```

**Deploy** (regenerates the D1-compatible SQL from `output/interlinear.sqlite`, applies it, then verifies D1's row
counts against the source table by table, failing on any mismatch):
```bash
./scripts/deploy_to_d1.sh interlinear-bible-db --fresh
./scripts/deploy_to_d1.sh interlinear-bible-db --fresh --tables lexicon_gloss_translation,ai_gloss,verse_translation
```

`--fresh` drops and recreates the selected tables first (the exported `CREATE TABLE` statements have no
`IF NOT EXISTS`). The table order lives in one place, `TABLE_ORDER` in `export_to_d1.py`; `generate_d1_reset.py` drops
tables in the reverse order. D1's free tier allows 100,000 rows written per day, so deploy the large static tables
once and the incremental translation tables on their own.

**GitHub Actions:** the `build` workflow's deploy job is manual (`deploy_to_d1 = true`) or runs on a tag ending in
`.0`. It deploys the static tables built in CI only, because CI builds stage 1 and has no translations; it refuses the
three translation tables (`lexicon_gloss_translation`, `ai_gloss`, `verse_translation`), which would otherwise be
replaced by empty ones. Deploy those from your own machine with `deploy_to_d1.sh`. Requires the repo secrets
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` and the repo variable `D1_DATABASE_NAME`.

**Public read access:** D1 has no public toggle; put a thin read-only Worker (or the Pages Functions below) in front
of it rather than exposing the database.

## Read API (Cloudflare Pages Functions)

A read-only HTTP API in front of D1, implemented as Pages Functions in `functions/`, bound to D1 as `DB`.

| Endpoint | Description |
|---|---|
| `GET /api/books` | List all 66 books with code, name, testament, chapter count |
| `GET /api/verse/:book/:chapter/:verse` | Full interlinear payload for one verse; optional `?lang=id` adds the translated lexicon gloss |
| `GET /api/chapter/:book/:chapter` | Every verse in a chapter in one call |
| `GET /api/lexicon/:strongs` | Full lexicon entry with all translated glosses |

Each word includes surface text, transliteration, Strong's, morphology (raw code and parsed parts), the gloss (AI
contextual gloss, then translated lexicon, then English lexicon, then the source gloss, with a `source` field) and
resolved proper-noun info. `node functions/_shared/test-queries.js output/interlinear.sqlite` runs the query logic
against a real copy of the database through a D1-style mock.

**Needs a check after the numbering change:** the earlier version translated `:chapter/:verse` through
`versification_mapping` (English to Hebrew) because `verse` held Hebrew numbering. It now holds English numbering, so
that lookup step should be removed, and the API does not read `verse_translation` yet. `_routes.json` restricts
Function invocation to `/api/*`, so static assets stay on Pages' free path.

## Exports

All exports run after stage 1 and improve as later stages fill in; see "Which stage does each export need?" in the
pipeline above for the details.

- **NotebookLM:** the interlinear Markdown (`export_to_markdown.py ... --group`) makes 8 files, each kept under
  NotebookLM's 500,000-word cap per source and 50 sources per notebook. The script prints the word count per file,
  so re-check the margin after the verses were added. Do not merge books by testament; a combined testament is over
  the cap.
- **Claude Project / reading:** `--compact` writes running text (`## Genesis 1`, superscript verse numbers,
  paragraphs, poetry lines). `--verse-style caret` writes `^4^` for pandoc PDF/DOCX; the default superscript digits
  need a font that has U+2074 to U+2079 (Latin Modern does not).
- **Obsidian:** `--wikilink` adds `[[...]]` syntax, but the links resolve only if notes with those names exist. For a
  vault with working links use `export_obsidian_vault.py` (one note per chapter, with previous/next and index links).
  It has its own English-first gloss lookup.
- **Word / OneNote:** `export_to_docx.py ... --group` converts the same Markdown with pandoc (OneNote has no Markdown
  import; drag the `.docx` onto a page).
- **Bible apps:** `export_mysword.py`, `export_esword.py` and `export_osis.py` each take `--layout natural` (the verse
  translation with clickable Strong's tags). OSIS XML must be compiled with `osis2mod` into a SWORD module.

## Semantic domain categories

`lexicon_category` adds a semantic domain per Strong's number (for example `person_role>personal_name`,
`deity_spirit>divine_name`), which the STEPBible lexicons do not carry. Source: crizin/bible-db's
`strong_categories.jsonl` (14,197 entries, 107 categories). `load_lexicon_categories.py` normalizes the source's bare
keys (`H430`) to this project's padded, letter-suffixed keys (`H0430`, `H0430G`, `H0122a`) and applies each category to
every lexicon entry sharing the base number. A Strong's number can have several categories (`is_primary` marks the
source's primary one).

## Tests

`testkit/run_tests.py` (AI gloss pipeline, stage 4, Markdown exporter, TAHOT parser), `testkit/test_exporters.py`
(MySword, e-Sword, OSIS) and `testkit/test_common.py` (`bible_export_common.py`) run on a toy database with no network,
against the real files in `scripts/`. Run all three from the repo root after `pip install anthropic`.

## Known gaps / next steps

- Stage 3 (contextual glosses) and stage 4 (verse translation) are not complete for the whole Bible; `RUNBOOK.md` lists
  what to run next.
- **RMAC dictionary for MySword:** Hebrew morphology links need a dictionary module; deferred until the real
  `rmac.dct.mybible` schema is known.
- **TSK cross-references:** public domain but not in STEPBible-Data; would need a separate source and its own
  `cross_reference` table. An app-layer feature, kept apart from the translation pipeline.
- **TR vs TAGNT:** byztxt TR and STEPBible TAGNT are different text traditions; TAGNT is not used for the NT text,
  only as a cross-check during development.
- A `reviewed` flag for `ai_gloss`, a search endpoint, and pagination/rate limiting for the read API.
