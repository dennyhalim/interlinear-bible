# Vox Antiqua handoff

State of the interlinear-bible project at the end of this long chat. Claude's sandbox resets and cannot see your repo, so this file and the zip are the memory. To resume, upload the zip to a new chat and say: **"Continue from HANDOFF.md."**

## 1. Standing rules (yours)

- Textus Receptus (NT) and Masoretic Text (OT) are the only source of truth. Be expert in Greek and Hebrew; give references, plus Strong's where possible.
- Natural translation is simple and literal, and follows the EYD V spelling rules (`sources/SK_EYD_Edisi_V_16082022.pdf`, distilled into `sources/eydv.md`).
- Scripts must print statistics (chapters, verses, words) so input and output can be checked.
- Work in small tasks, each finished in one run, one file per message. Surgical edits, and send only changed files.
- Code: light, simple, readable, production-ready, concise docblock header, tested before sending.

## 2. Database state (after the rebuild with the parse_tahot fixes)

- OT: 39 books, 929 chapters, 23,261 verses, 305,500 words. NT: 27 books, 260 chapters, 7,957 verses, 140,937 words. Total 446,437 words.
- `verse` uses standard English numbering. Psalm titles are verse 0. TAHOT lists the English reference first and the Hebrew one in brackets; both parts of a merged verse get unique positions. The 152 `X`-flag rows (words from other manuscripts) are excluded.
- NT words are inserted first, so NT `word_id`s survive OT rebuilds.
- `lexicon_gloss_translation`: 22,716 `id` rows, loaded from your CSV (the CSV is the source of truth; your terms such as "benam" were in it from the start).
- `ai_gloss`: 99,564 words (22.3%). Minor prophets (14,355) and Gospels + Acts (85,209) are complete.
- Remaining: 346,873 words. NT 55,728 (Pauline, General epistles, Revelation). OT 291,145.
- Your working gloss file: `output\staging\aigloss-refs2.jsonl` (all records carry book/chapter/verse/position). Backups: `interlinear_old.sqlite`, `interlinear_v2.sqlite`.

## 3. Pipeline status

| Stage | Status |
|---|---|
| 1 Build DB | Done. `build_db.py` should print ignored-row counters (edit sent as a snippet). |
| 2 Lexicon translation | Done. |
| 3 Contextual glosses | 22.3%. `generate_ai_glosses.py` is current (below). |
| 4 Natural verse translation | Script ready, 17 + stub checks pass. Not run on real data. |
| 5 Exports | Complete files: `export_to_markdown.py`, `export_mysword.py`, `export_esword.py`, `export_osis.py`. Not tried in the apps. |
| 6 D1 deploy | Table lists fixed, CI deploy guarded. Translation tables not redeployed. |

## 4. Files in this zip

Scripts (these replace your copies; your repo's other scripts are unchanged):

- Stage 3: `generate_ai_glosses.py` (sync/submit/retrieve, `--strongs` targeted mode, `--dry-run`, position fields, comma lists for `--book`/`--group`, cached system prompt, lenient parsing; model `claude-opus-5-5`, 150 words per request, `max_tokens` 16000, prompt version `v2`), `load_ai_glosses.py` (loads by position, cleans `-/ bumi`, `[Obj.]`), `add_glosses_refs.py`, `import_batch_results.py`, `remap_ai_glosses.py`, `batch_api_helper.py` (lenient JSON parser, cached system block, cache-usage summary), `check_term.py`, `find_term_drift.py`.
- Stage 4: `generate_verse_translations.py` (tagged text `{G746}`, `¶` paragraphs, ` | ` poetry, God-pronoun capitalization, escaped quotes, EYD rules from `sources/eydv.md`, `--guidelines` to override).
- Exports: `export_to_markdown.py` (compact reading format, `--verse-style unicode|caret|plain`, `--strongs`), `export_mysword.py` (unpadded Strong's, Greek-only `<WT>`, `Language` column, `natural` layout with `<CM>` paragraph ends, `<TS>` titles, poetry `<br>`), `export_esword.py` (`natural` now real, literal pilcrow, poetry space or `<br>`), `export_osis.py` (standard OSIS book names, unpadded Strong's, no remap, `natural` layout).
- Parsing and D1: `parse_tahot.py`, `export_to_d1.py` + `generate_d1_reset.py` (install as a pair; one table order), `schema.sql` (adds `verse_translation`), `.github/workflows/build.yml` (CI can no longer wipe translation tables in D1).
- Docs: `README.md` (merged and corrected), `RUNBOOK.md` (commands and checklists for the remaining gloss runs and the stage 4 trial), `sources/eydv.md`.
- `testkit/`: stub `bible_export_common.py` (your real one is in your repo), `mkdb.py` (toy database), `run_tests.py` (50 checks) and `test_exporters.py` (32 checks). Run both from the package root after `pip install anthropic`.

Edits still sent only as snippets (confirm they are applied): `bible_export_common.py` (`get_verse_translation`, ranked lexicon join, `first_sense` with colon/parentheses handling, `bec.connect`) and the `build_db.py` ignored-row counters.

## 5. Conventions decided

- Lexicon gloss is authoritative for terminology (e.g. "benam" over "baptis"). `TERM_RULES` carries what a lexicon cannot: `H5002/H559/H1696/H1697/G3056/G4487` firman for God speaking, otherwise ucapan/berkata/perkataan; `H113`/`G2962`/`G1203` tuan vs Tuhan; `H136` always Tuhan.
- Pronouns for God are capitalized (Ia/Dia, -Nya, Engkau/-Mu, Aku/-Ku).
- `H853` plain object marker gets `(tanda objek)`; an unrendered word gets `-`.
- A word with TAHOT-style parts gets one gloss, with no `/` and no English markers.
- Lexicon format: `general: specific`, `a/b` for alternatives, ALL-CAPS parentheses are usage hints, not output.
- Stage 4: Strong's tag after each translated word, `¶` at the start of a paragraph verse, ` | ` between poetic lines.

## 6. Open items

1. Decide the Jesus pronoun convention and add it to the prompt (the Gospels are already glossed).
2. Tell Claude the Indonesian term for `H3068`/`H3069` (TB uses TUHAN, and "Tuhan ALLAH" for Adonai YHWH) so rules can be added.
3. Finish glosses in groups: Pauline, General epistles + Revelation, then OT (RUNBOOK part A).
4. Stage 4 trial on a copy of the database: Genesis 1, Psalm 1, John 1 (RUNBOOK part B). Check quotes, paragraphs and poetry before the full batch.
5. Reapply the database-only SQL after every load: `H5002` before a divine name, `H136` tuan to Tuhan (and `H853` plain forms, if wanted).
6. Cost: divide the Gospels job's cost by its 67,525 words and multiply by the remaining words.
7. Deploy translation tables to D1 from your machine, never from CI.
8. Deferred: an Indonesian RMAC dictionary for MySword (needs the real `rmac.dct.mybible` schema); a `reviewed` column for `ai_gloss`.

## 7. Known risks and unverified items

- Everything was tested on toy data. Nothing was run against your real database or the live model.
- Stage 4 output is a JSON array. An unescaped `"` in a verse loses that verse and the rest of its request. The prompt says to escape quotes, but the trial will show whether the model complies. A line-based output format (`verse<TAB>text`) removes the risk if needed.
- Nothing was compiled or opened in the target apps: `osis2mod`, MySword (the `<br>` poetry break, `<CM>` and `<TS>` rendering) and e-Sword are untested. The `oshm:` morph prefix for Hebrew in OSIS is unverified.
- `eydv.md` examples marked "e.g." are applications to Bible text, not EYD quotations (e.g. `3 hari`, Aku/Ku- for God speaking).
- The pandoc PDF font fix was checked at glyph level only. `--verse-style caret` avoids the font problem.
- Cost and caching figures are rough estimates from the docs at the time.

## 8. Pitfalls found

- `INSERT OR IGNORE` hid dropped rows. Always print counts.
- Reloading the JSONL overwrites database-only edits.
- `check_term.py` shows the lexicon as it is now, not at generation time.
- A second `submit` overwrote the first job file; use a distinct `--batch-job-file`.
- Excel saves CSV as `;`-separated and cp1252 unless "CSV UTF-8" is chosen.
- A prompt that says "follow Bible convention" overrides your lexicon.
- A prompt asking for a "compact JSON array, one object per line" made the model answer in JSON Lines; the parser is now lenient, and the prompt asks for a real array.
- A tag push ending in `.0` used to trigger a full `--fresh` D1 deploy from the CI database, which has no translations.
