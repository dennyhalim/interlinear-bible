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

## Deploying to Cloudflare D1

The database (~98MB) fits comfortably within D1's limits (10GB per
database on the paid plan) — no need to shard or split it by
Hebrew/Greek or any other axis.

**One-time setup:**
```bash
npm install -g wrangler
wrangler login
wrangler d1 create interlinear-bible-db
# copy the printed database_id into wrangler.toml
```

**Deploy** (regenerates the D1-compatible SQL export from
`output/interlinear.sqlite`, then applies it):
```bash
./scripts/deploy_to_d1.sh interlinear-bible-db --fresh
```

`--fresh` drops and recreates all tables first — needed for every
deploy after the first, since our exported `CREATE TABLE` statements
don't use `IF NOT EXISTS`. The script re-exports from the current
`.sqlite` on every run (never stale), applies each table in dependency
order, then verifies D1's row counts match the source file exactly,
table by table, failing loudly on any mismatch rather than reporting
false success.

**Via GitHub Actions:** trigger the `build` workflow manually (Actions
tab → Run workflow) with `deploy_to_d1` set to `true`, and `d1_tables`
set to a comma-separated list of tables to deploy (defaults to
`lexicon_gloss_translation,ai_gloss`, the two tables that grow
incrementally as translation/gloss work progresses). Leave `d1_tables`
empty only for the rare full-dataset deploy (first-ever deploy, or
after a schema change) — that path deploys all 1.1M+ rows in one run,
which exceeds D1's free-tier 100,000-rows-written-per-day limit; see
`deploy_to_d1.sh`'s own comments for the full reasoning and the
`--tables`/`--fresh` scoping behavior. Requires two repo secrets:
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, plus a repo
variable `D1_DATABASE_NAME` set to your D1 database's name. Deploy is
manual-only by design — never runs automatically on push.

**For public read access:** D1 has no built-in "public" toggle; write
a thin read-only Worker in front of it (a `GET /verse/:book/:chapter/:verse`
style endpoint using prepared statements) rather than exposing the
database directly. This also gives your web app and future mobile app
a single shared API surface instead of each needing D1 credentials.

**Why not split the database:** the `word` table (biggest at ~33MB) is
already indexed on `verse_id` and `strongs`; splitting by language or
by texts-vs-dictionaries would only help if some access pattern never
joined across the split, but interlinear lookup joins `word` against
`lexicon_entry`, `morphology_code`, and `proper_noun_variant`
constantly. Splitting would just force cross-database queries in
Worker code for no size or speed benefit at this scale.

- **AI gloss generation** — `ai_gloss` table exists but is unpopulated.
  Planned: batch by verse, feed word + lemma + morph + lexicon entry +
  verse context to Claude, store structured `{gloss, alt_glosses, note}`
  per word. Not yet implemented in this repo.
## Exporting for NotebookLM

NotebookLM (Google's Gemini-powered research notebook) accepts
Markdown as a source, capped at 500,000 words and 200MB per source,
with 50 sources per notebook on the free tier.

```bash
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown
```

Produces one `.md` file per book (66 total, ~29MB combined), each
rendering the interlinear as readable text: verse reference, the
original-language line, then a compact per-word gloss
(`word=transliteration[Strong's,morphology]:"gloss"`). The largest
book (Jeremiah, ~92,000 rendered words) stays comfortably under the
500,000-word cap.

**66 books exceeds NotebookLM's 50-source free-tier cap.** Two ways to
handle it:

**Option A — thematic groups (recommended, fits in one notebook):**
```bash
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --group
```
Produces 8 files along traditional groupings, each verified to stay
under the 500,000-word cap:

| Group | Books | Words |
|---|---|---|
| Torah | 5 | 330,584 |
| Historical Books | 12 | 433,457 |
| Wisdom & Poetry | 5 | 133,896 |
| Major Prophets | 5 | 273,285 |
| Minor Prophets | 12 | 55,263 |
| Gospels & Acts | 5 | 354,216 |
| Pauline Epistles | 13 | 129,682 |
| General Epistles & Revelation | 9 | 89,235 |

Historical Books is the tightest at 87% of the word cap — worth
re-checking this margin if the underlying source data ever grows.
8 sources fits in a single notebook with room to spare under the
50-source cap.

**Option B — one file per book, split across two notebooks:**
```bash
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown
```
66 files (~29MB combined); upload Old Testament (39 books) to one
notebook and New Testament (27 books) to another, both under the
50-source cap. More granular if you want NotebookLM to cite down to
the individual book rather than the group.

Don't try to combine books into fewer files by testament instead of by
group — a combined Old Testament file alone comes to roughly 1.2
million words, well over the word cap; the two limits (words per
source, sources per notebook) pull in opposite directions, and only
per-book or per-group files satisfy both.

Single-book or single-testament export still works too:
```bash
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --book GEN
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --testament NT
```

## Using with Obsidian (or other Markdown note tools)

The exported Markdown works as-is in Obsidian and similar tools — same
files, no separate export needed. Add `--wikilink` to wrap chapter and
verse references in `[[double brackets]]` (Obsidian's link syntax):

```bash
python3 scripts/export_to_markdown.py --db output/interlinear.sqlite --out-dir output/markdown --group --wikilink
```

Verified this doesn't affect NotebookLM compatibility: `[[GEN 1:1]]`
and `GEN 1:1` produce identical word counts (both render to the same
number of whitespace-separated tokens), since NotebookLM has no
special handling for `[[...]]` — it just reads the brackets as literal
characters, not as syntax. So the same `--wikilink` output works for
both tools without needing two separate exports.

Worth knowing before relying on the links: they resolve to *unresolved*
links in Obsidian until you have notes actually named `GEN 1` or
`GEN 1:1` for them to point to — this export doesn't create per-chapter
or per-verse notes, only the wiki-link syntax pointing at names that
convention would use. Also, Obsidian doesn't have special bidirectional
text (bidi) support, so Hebrew (right-to-left) inline with Latin
transliteration and Strong's numbers (left-to-right) on the same line
may render in a visually confusing order depending on your OS and font
— this is an Obsidian/renderer limitation, not something the export
controls.

### Obsidian vault export (working links, not just link syntax)

The `--wikilink` flag above adds `[[...]]` syntax to the per-book/
per-group files, but those links don't resolve to anything — there's
no note actually named `GEN 1:1` for them to point to. For links that
genuinely work when opened as an Obsidian vault, use the separate
`export_obsidian_vault.py` script instead:

```bash
python3 scripts/export_obsidian_vault.py --db output/interlinear.sqlite --out-dir output/obsidian_vault
```

Produces one note per **chapter** (1,090 notes total, ~31MB), each
containing every verse in that chapter inline as text, plus:
- Previous/next chapter navigation links
- A link back to that book's index
- Book index notes (one per book, listing every chapter)
- Testament index notes (Old Testament / New Testament, listing every book)
- A top-level index linking both testaments

Verse-level notes weren't generated (that would mean 29,135 separate
files) — verses stay inline within their chapter note rather than each
getting their own note and link target. If verse-level granularity
with working links is wanted later, that's a larger, separate export
shape from this one.

**Verified, not just written:** every link in the generated vault was
checked programmatically against the actual filenames produced —
4,296 links across 1,159 files, zero broken links. Filenames were also
checked against Windows-unsafe characters (`< > : " | ? *`), none
found.

Single-book export for testing: `--book GEN`.

## Translating lexicon glosses (Indonesian)

Before generating AI verse-level glosses in any target language, the
lexicon itself gets translated once — a one-time batch of ~16,576
distinct Strong's entries (not 424,654, since most words in the text
reuse a small set of underlying lexicon entries). Translating the
lexicon first means every later verse-level gloss call references an
already-translated term instead of re-deriving meaning from English
independently per occurrence, which is both cheaper and more
consistent (the same Hebrew/Greek word gets the same Indonesian term
across all its occurrences, rather than potentially drifting verse to
verse).

Scope of this first pass: short glosses only (not the longer
Abbott-Smith/BDB-derived `meaning` field), stored **alongside** the
English data, not replacing it. The whole workflow is CSV-based so the
AI-translated output can be opened in Excel/Sheets and reviewed/edited
by a human before it ever touches the database:

```bash
# 1. Export lexicon entries to CSV (gloss_id column starts empty,
#    sorted by real usage frequency in the text so review time goes to
#    the most-used words first)
python3 scripts/export_lexicon_csv.py \
  --db output/interlinear.sqlite \
  --out output/staging/lexicon_id.csv \
  --language id

# 2. Fill in the gloss_id column. Two ways:

#    a) Synchronous (immediate, real-time feedback):
export ANTHROPIC_API_KEY=sk-ant-...
python3 scripts/translate_lexicon.py sync \
  --csv output/staging/lexicon_id.csv \
  --language id \
  --model claude-opus-4-5

#    b) Batch API (50% off both input and output tokens, async --
#       ~455 requests for the full lexicon, comfortably under the
#       100,000-per-job limit, one batch job):
python3 scripts/translate_lexicon.py submit \
  --csv output/staging/lexicon_id.csv \
  --language id --model claude-opus-4-5
# ... later, once the job is done (or check status any time) ...
python3 scripts/translate_lexicon.py retrieve \
  --csv output/staging/lexicon_id.csv \
  --language id

# 3. Open output/staging/lexicon_id.csv in Excel/Sheets, review and
#    hand-edit the gloss_id column as needed. Re-running step 2 later
#    will NOT overwrite any row you've already filled in by hand --
#    it only fills rows that are still empty. Verified: a simulated
#    hand-edit survived a subsequent run untouched.

# 4. Load the (possibly edited) CSV into the database
python3 scripts/load_lexicon_translations.py \
  --db output/interlinear.sqlite \
  --csv output/staging/lexicon_id.csv \
  --language id \
  --model claude-opus-4-5
```

Results land in `lexicon_gloss_translation` (one row per lexicon
entry per language), joinable against `lexicon_entry` on `lexicon_id`.
Schema supports multiple target languages simultaneously — export a
separate CSV per language (`--language es` gives a `gloss_es` column
in its own file) and load each independently.

### Testing cheaply with free-tier providers

`translate_lexicon.py` defaults to Claude (production quality), but
also supports OpenRouter and Gemini via `--provider`, both accessed
through the `openai` package pointed at their OpenAI-compatible
endpoints -- useful for testing this script's plumbing (CSV
read/write, batching, resume logic) cheaply before spending real
Claude usage on the full 16,576-entry run. Requires `pip install
openai --break-system-packages` in addition to `anthropic` (only
needed for these two providers; the default Claude path doesn't need
it).

```bash
# OpenRouter's openrouter/free router randomly selects a currently-
# free model (rotates -- check openrouter.ai/models for what's live).
# Rate limits apply and shift; fine for a small --limit test, not the
# full run.
export OPENAI_API_KEY=sk-or-v1-...   # an OpenRouter key, despite the env var name
python3 scripts/translate_lexicon.py sync \
  --csv output/staging/lexicon_id_test.csv \
  --language id --provider openrouter --model "openrouter/free" \
  --limit 20 --batch-size 10

# Gemini's free tier (Flash/Flash-Lite -- Pro moved to paid-only in
# April 2026 per Google's pricing page; verify current limits there
# rather than trusting a specific number, since they've shifted
# multiple times through 2026).
export GEMINI_API_KEY=...
python3 scripts/translate_lexicon.py sync \
  --csv output/staging/lexicon_id_test.csv \
  --language id --provider gemini --model "gemini-2.5-flash" \
  --limit 20 --batch-size 10
```

Both paths were verified with mocked responses matching each
provider's real API shape (`choices[0].message.content`, vs.
Anthropic's `content` block list) -- confirmed correct request
construction (model name, system+user message structure) and correct
response parsing, plus a regression check that the default Anthropic
path is unaffected by the refactor. Live API calls to either free
provider haven't been run (no API keys available here) -- that
remains the real-world verification step, same as the Anthropic path
always has been.

The CSV is written with a UTF-8 BOM so Excel on Windows opens the
Hebrew/Greek/Indonesian text correctly rather than mis-detecting the
encoding — a plain UTF-8 CSV frequently gets misread by Excel otherwise.

**What's tested vs. not:** the CSV round-trip (including a field
containing a comma, `"α, Ἀλφα"`, which needs correct quoting), the
resume logic, the batching, JSON response parsing (including
markdown-fence stripping and malformed/incomplete-response handling),
and — the most important guarantee for a review workflow — that a
hand-edited row survives a subsequent script run without being
overwritten, were all verified end-to-end against a real copy of the
database using a mocked API client. The actual live API calls to
Claude have not been run — that's the next real-world step, not
something verifiable without API access.

## Generating contextual AI glosses (word-by-word, per verse)

Once the lexicon is translated (previous section), the next step
generates a **contextual** gloss for every word in the actual text —
not just the dictionary sense, but the sense that fits each specific
verse. The same underlying word can mean different things in different
places (Greek λόγος is "Word" in John 1:1 but "matter/case" — a legal
proceeding — elsewhere), which a context-free lexicon lookup can't
capture on its own.

**Batched by chapter, not by verse or an arbitrary window.** Chapters
average ~390 words; the largest (John 6) is 1,285 words. This is a
comfortable size for one API call, and chapter boundaries are natural
discourse units — a pronoun or ambiguous term in verse 5 often depends
on something established in verse 1 of the same chapter, so keeping
the whole chapter in one call's context avoids splitting that
dependency the way an arbitrary N-verse window could. 1,090 chapters
total. Oversized chapters are automatically split into multiple calls
at verse boundaries (never splitting a single verse's words across two
calls).

**Grounded in the translated lexicon**, not free-standing: each word's
prompt includes the already-translated (and human-reviewed) Indonesian
lexicon gloss as the starting point. The model's job is to pick or
lightly adapt the right sense for the verse, not invent a translation
independently — this is what keeps the same underlying word
terminologically consistent across all its occurrences instead of
drifting per-verse.

**A real duplicate-row bug was found and fixed here.** The lexicon
join (`lx.dstrong = w.strongs OR lx.estrong = w.strongs`) could match
multiple lexicon rows for one word when a base Strong's number has
several disambiguated entries sharing it — e.g. `G2491` ("John") has
four entries (`G2491G/H/I/J`) for different individuals named John,
the same pattern as the Herod proper-noun disambiguation case
elsewhere in this project. This silently duplicated affected words in
the API prompt (confirmed: John 1 was inflated from 844 to 1,065
words this way). Fixed with a `ROW_NUMBER()`-ranked join that picks
exactly one lexicon row per word (preferring an exact `dstrong` match,
which is unambiguous, over a same-`estrong` fallback) — verified
against all of John 1–6 with zero duplicates remaining.

### Scoping a run: single book, thematic group, testament, or everything

`--book`, `--group`, and `--testament` are mutually exclusive (the
script errors clearly if more than one is given). Omit all three to
process the whole Bible.

```bash
--book GEN                # one book
--group torah              # Genesis-Deuteronomy
--testament OT             # all 39 Old Testament books
# (nothing)                # everything, all 66 books
```

Valid `--group` values (reusing the same thematic groupings as
`export_to_markdown.py`'s NotebookLM export, so "the Torah" means the
same set of books everywhere in this project): `torah`, `historical`,
`wisdom`, `major_prophets`, `minor_prophets`, `gospels_acts`,
`pauline_epistles`, `general_epistles_revelation`.

Verified against the real database: `--group torah` returns exactly
Genesis through Deuteronomy (178 chapters), `--testament OT`/`NT`
correctly partition all 1,090 chapters with no overlap or gap
(830 + 260 = 1,090), and a single `--book` returns exactly that book's
chapters (e.g. John's 21).

### Two ways to run: synchronous or Batch API

**`sync`** — calls the API immediately per chapter-batch, writes
progress after every call. Simple, real-time visibility, but no cost
discount.

**`submit` / `retrieve`** — uses Anthropic's Batch API (50% off both
input and output tokens) instead. `submit` builds one request per
chapter-batch (~1,090+ requests, comfortably under the 100,000-per-job
limit) and submits them as a single batch job; `retrieve` polls until
the job finishes and writes results into the same JSONL format `sync`
produces, so `load_ai_glosses.py` works identically either way.
Anthropic-only (the Batch API isn't something OpenRouter/Gemini expose
identically, so this mode doesn't offer `--provider`).

```bash
export ANTHROPIC_API_KEY=sk-ant-...

# Test against one chapter first (sync, immediate feedback)
python3 scripts/generate_ai_glosses.py sync \
  --db output/interlinear.sqlite \
  --out output/staging/ai_glosses_id_test.jsonl \
  --language id --book GEN --limit-chapters 1

# Full run via Batch API (50% cheaper, async)
python3 scripts/generate_ai_glosses.py submit \
  --db output/interlinear.sqlite \
  --out output/staging/ai_glosses_id.jsonl \
  --language id --model claude-opus-4-5

# Later (or in a loop), check/retrieve results:
python3 scripts/generate_ai_glosses.py retrieve \
  --out output/staging/ai_glosses_id.jsonl \
  --language id

# Or the full run via sync instead, if preferred:
python3 scripts/generate_ai_glosses.py sync \
  --db output/interlinear.sqlite \
  --out output/staging/ai_glosses_id.jsonl \
  --language id --model claude-opus-4-5

# Load into the database (idempotent -- upserts by word_id)
python3 scripts/load_ai_glosses.py \
  --db output/interlinear.sqlite \
  --jsonl output/staging/ai_glosses_id.jsonl
```

Results land in `ai_gloss` (one row per word), which the front-end
should prefer over `lexicon_entry.gloss` when both exist — it's the
more specific, context-aware answer.

**Review approach for this scale:** at 424,654 words, a full manual
CSV review (as used for the ~22,716-entry lexicon) isn't practical.
The lexicon-grounding above is the primary quality control — most
words should match their already-reviewed lexicon gloss exactly, with
deviations only where the model found a genuine contextual reason to
differ (captured in `alt_gloss`/`note` when the model itself flagged
ambiguity). Spot-checking specific chapters or high-theological-stakes
passages by exporting them to CSV (using the same pattern as the
lexicon export, adapted to `ai_gloss`) is a reasonable follow-up if
deeper review is wanted — not built yet, since it depends on which
passages matter most to check first.

**What's tested vs. not:** batch-splitting logic (verified against
real chapter data with zero verses split across batches, and zero
duplicate words after the join fix above), resume/skip logic (verified
exact word counts across two sequential runs, no duplicates), all five
scope modes (book/group/testament/none, each checked against real
database counts), and the full `submit`→`retrieve` Batch API cycle
(verified end-to-end with mocked responses matching the real SDK's
`MessageBatch`/`MessageBatchIndividualResponse` types) were all tested.
The actual live translation quality, and the real Batch API's
turnaround time in practice, are untested — that depends on running
this for real against your API key.

## OT versification mapping (Hebrew MT vs. standard English numbering)

The `verse` table stores chapter/verse numbers exactly as they appear
in the Hebrew source (TAHOT) and Greek source (byztxt TR) -- which
occasionally differs from the numbering readers expect from an
English Bible. The classic case: Malachi 3 in the Hebrew text runs
through what English readers know as 4:1-6 -- **there is no Hebrew
chapter 4 at all** in the underlying source data. Displaying "Malachi
4:1" to a user requires translating between the two numbering systems.

**Scope, deliberately narrow:** Old Testament only. TVTMS's own
documentation states its "Greek" tradition data is LXX/Septuagint-based,
not Textus Receptus -- so it has no real bearing on this project's NT
text, which already follows standard English NT versification (that's
*why* KJV, translated from a TR-family text, established that standard
in the first place). Canonical 39-book Protestant OT only; TVTMS's
Apocrypha coverage (Sirach, Tobit, Judith, Esther Additions, etc.) is
out of scope since TAHOT doesn't include those books.

```bash
python3 scripts/parse_tvtms.py \
  --tvtms "sources/stepbible/Versification/TVTMS - Translators Versification Traditions with Methodology for Standardisation for Eng+Heb+Lat+Grk+Others - STEPBible.org CC BY.txt" \
  --out output/staging/tvtms_ot.jsonl

python3 scripts/load_versification.py \
  --db output/interlinear.sqlite \
  --jsonl output/staging/tvtms_ot.jsonl
```

Populates `versification_mapping`: **2,900 per-verse mapping rows**
covering 151 distinct difference-passages across 33 of the 39 OT
books. Verified against the Malachi 3/4 case directly: the current
`verse` table has Malachi chapter 3 running to verse 18 with no
chapter 4 at all, and the loaded mapping correctly translates Hebrew
3:19-24 to standard English 4:1-6, verse by verse.

**Known, explicitly-flagged limitations** (the parser reports these at
runtime rather than silently working around them):
- **41 sections have multiple `TEST:` condition branches** in the
  source data (different rules depending on which specific Bible
  sub-tradition you're comparing against). This parser takes all
  mapping rows present per section rather than selecting a specific
  branch, which covers the standard/default case correctly but may
  include extra detail for edge-tradition comparisons that don't apply
  to a Hebrew MT vs. standard English comparison specifically. The
  affected section list is printed by the parser (e.g. `Gen.5:31--6:1`,
  `Mal.4:1-4:6`, several 1 Kings/Esther/Daniel passages) — worth a
  manual look if precision matters for a specific one of these.
- **2 sections have a range-length mismatch** that couldn't be safely
  expanded verse-by-verse (e.g. one English verse maps to a two-verse
  Hebrew range) — these are kept as whole-range mappings
  (`standard_verse`/`standard_verse_end`) rather than guessed at
  per-verse.
- **Esther 4/5** contain Apocryphal "Addition" material (subverses
  like `4:17.1`-`4:17.30`) that has no Hebrew equivalent at all, since
  TAHOT doesn't include the Apocrypha. These collapse to a small
  number of "this verse doesn't exist in Hebrew" facts rather than 30
  redundant rows — correct behavior, not data loss, but worth knowing
  if you ever wonder why Esther 4:17 only has one mapping row instead
  of many.

**Not yet built:** actually *applying* this mapping to renumber the
`verse`/`word` tables in place (currently the mapping exists as a
separate lookup table; the underlying data still uses Hebrew-source
numbering). Whether to renumber in place versus keep both numbering
systems queryable side by side is a real design decision for whoever
builds the display/API layer, not resolved here.

## Read API (Cloudflare Pages Functions)

A read-only HTTP API in front of D1, implemented as Pages Functions
(not a standalone Worker -- Pages Functions bind to D1 identically,
and this keeps the API deployed alongside a frontend as one unit
rather than a separate project). Lives in `functions/`.

**Endpoints:**

| Endpoint | Description |
|---|---|
| `GET /api/books` | List all 66 books with code, name, testament, chapter count |
| `GET /api/verse/:book/:chapter/:verse` | Full interlinear payload for one verse. `:chapter`/`:verse` are standard English numbering -- transparently resolved through `versification_mapping` (e.g. `MAL/4/1` correctly finds Hebrew-stored `3:19`). Optional `?lang=id` adds a translated lexicon gloss. |
| `GET /api/chapter/:book/:chapter` | Every verse in a chapter in one call. `:chapter` is the source (Hebrew/Greek) chapter as stored; each returned verse includes both its source and display (standard English) reference, since a "standard English chapter" doesn't always correspond to one contiguous source chapter. |
| `GET /api/lexicon/:strongs` | Full lexicon entry (English gloss + meaning + all translated-language glosses) for a Strong's number. |

Each word in a verse/chapter response includes: surface text,
transliteration, Strong's number, morphology (both the raw code and
resolved human-readable parts via `word_morph_part`), gloss (AI
contextual gloss preferred when available, falling back through
translated lexicon gloss -> English lexicon gloss -> source dataset
gloss, with the `source` field indicating which was used), full
English lexicon meaning, and resolved proper-noun info when the word
refers to a specific disambiguated person/place.

**Verified against real data, not just written:** `functions/_shared/test-queries.js`
is a dev-only test harness (not deployed) that runs the actual query
logic in `queries.js` against a real copy of `interlinear.sqlite`,
via a minimal D1-binding-API mock backed by better-sqlite3
(`functions/_shared/d1-mock-for-testing.js`, also dev-only). Run it
with:

```bash
npm install
node functions/_shared/test-queries.js output/interlinear.sqlite
```

8 tests currently pass, including the two cases that matter most for
correctness: Malachi 4:1 correctly resolves to the Hebrew-stored 3:19
(and back), and Matthew 2:1's "Herod" correctly resolves to the
specific disambiguated individual (Herod the Great, not Antipas or
Agrippa) via the `word_proper_noun` join built earlier.

**Deploying:** since this lives in `functions/` at the project root,
it deploys automatically as part of a normal Cloudflare Pages deploy
(`wrangler pages deploy` or the Pages Git integration) -- no separate
step. Requires a D1 binding named `DB` configured on the Pages
project (Pages dashboard -> Settings -> Functions -> D1 database
bindings, or via `wrangler.toml`'s `[[d1_databases]]` block, same
`binding = "DB"` name the code expects).

`_routes.json` at the repo root restricts Function invocation to
`/api/*`, so any static assets served alongside this (e.g. a frontend)
stay on Pages' free/unlimited static-asset path rather than invoking
a Function (and its D1 usage) for every request.

**Not yet built:** a search endpoint, and pagination/rate-limiting for
production traffic. Both are reasonable next additions once there's a
frontend to actually drive their design.

## Known gaps / next steps

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
