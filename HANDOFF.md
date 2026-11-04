# Vox Antiqua handoff

State of the interlinear-bible project at the end of this long chat. Claude's sandbox resets and cannot see your repo, so this file and the zip are the memory. To resume, upload the zip to a new chat and say: **"Continue from HANDOFF.md."**

## 1. Standing rules (yours)

- Textus Receptus (NT) and Masoretic Text (OT) are the only source of truth. Be expert in Greek and Hebrew; give references, plus Strong's where possible.
- Natural translation is simple and literal, and follows the EYD V spelling rules (`sources/SK_EYD_Edisi_V_16082022.pdf`, distilled into `sources/eydv.md`).
- Scripts must print statistics (chapters, verses, words) so input and output can be checked.
- Work in small tasks, each finished in one run. Send progress often. Surgical edits, and send only changed files.
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
| 1 Build DB | Done. Add the ignored-row counters in `build_db.py` (edits were sent). |
| 2 Lexicon translation | Done. |
| 3 Contextual glosses | 22.3%. `generate_ai_glosses.py` is current (below). |
| 4 Natural verse translation | Script ready and tested on stubs only. Not run on real data. |
| 5 Exports | `export_to_markdown.py` done. MySword, e-Sword and OSIS edits sent, not confirmed applied. |
| 6 D1 deploy | Not redone. |

## 4. Files in this zip

Scripts (these replace your copies; your repo's other scripts are unchanged):

- `generate_ai_glosses.py`: sync/submit/retrieve, `--strongs` targeted mode, `--dry-run`, position fields in records, comma lists for `--book`/`--group`, cached system prompt, lenient parsing. Defaults: model `claude-opus-5-5`, 150 words per request, `max_tokens` 16000, prompt version `v2`.
- `load_ai_glosses.py`: loads by position (survives rebuilds) and cleans glosses (`-/ bumi` becomes `bumi`, `[Obj.]` becomes `(tanda objek)`).
- `add_glosses_refs.py`, `import_batch_results.py` (from `--batch-id` or a Console download), `remap_ai_glosses.py` (old DB to new DB).
- `batch_api_helper.py`: lenient JSON parser (arrays, JSON Lines, fences, cut-off replies), cache-usage summary, cached system block.
- `check_term.py`, `find_term_drift.py`: show what the AI was sent versus what it returned, and which Strong's numbers drift from the lexicon.
- `generate_verse_translations.py`: stage 4. Tagged text (`{G746}`), `¶` paragraphs, ` | ` poetry lines, God-pronoun capitalization, direct speech with escaped quotes, EYD rules from `sources/eydv.md` (`--guidelines` to override).
- `export_to_markdown.py`: compact reading format (`Genesis 1` headings, superscript verse numbers, paragraphs, poetry hard breaks). `--strongs`, `--verse-style unicode|caret|plain`, `--language`.
- `parse_tahot.py`: handles bracketed references, merged verses, skips `X` rows, prints chapters/verses/skips.
- `sources/eydv.md`, `README_pipeline.md` (the pipeline section for the README), `testkit/` (stub `bible_export_common.py` and a toy database builder).

Edits sent as snippets, not in the zip (confirm they are applied): `bible_export_common.py` (`get_verse_translation`, ranked lexicon join, `first_sense` with colon/parentheses handling, `bec.connect`); `export_mysword.py` (unpadded Strong's with `int(digits)`, Greek-only `morph_tag`, `natural` layout); `export_osis.py` (unpadded Strong's, use `--no-remap`); `build_db.py` counters; `export_to_d1.py` / `generate_d1_reset.py` table lists.

## 5. Conventions decided

- Lexicon gloss is authoritative for terminology (e.g. "benam" over "baptis"). `TERM_RULES` carries what a lexicon cannot: `H5002/H559/H1696/H1697/G3056/G4487` firman for God speaking, otherwise ucapan/berkata/perkataan; `H113`/`G2962`/`G1203` tuan vs Tuhan; `H136` always Tuhan.
- Pronouns for God are capitalized (Ia/Dia, -Nya, Engkau/-Mu, Aku/-Ku).
- `H853` plain object marker gets `(tanda objek)`; an unrendered word gets `-`.
- A word with TAHOT-style parts gets one gloss, with no `/` and no English markers.
- Gloss lexicon format: `general: specific`, `a/b` for alternatives, and ALL-CAPS parentheses are usage hints, not output.
- Stage 4: Strong's tag after each translated word, `¶` at the start of a paragraph verse, ` | ` between poetic lines.

## 6. Open items

1. Decide the Jesus pronoun convention and add it to the prompt (the Gospels are already glossed).
2. Tell Claude the Indonesian term for `H3068`/`H3069` (TB uses TUHAN, and "Tuhan ALLAH" for Adonai YHWH) so rules can be added.
3. Finish glosses in groups: Pauline, General epistles + Revelation, then OT. Test two chapters first, `--dry-run`, one `--batch-job-file` per job, `retrieve` before the next `submit`.
4. Stage 4 trial: Genesis 1, a Psalm and John 1. Check paragraphs, poetry lines and quotes before any batch.
5. Reapply the database-only SQL after every load: `H853` plain forms, `H5002` before a divine name, `H136` tuan to Tuhan.
6. Cost: divide the Gospels job's cost by its 67,525 words and multiply by the remaining words.
7. README: replace "Build locally" with `README_pipeline.md`; remove the stale lines about `ai_gloss` and TVTMS.
8. D1: add `verse_translation` and `lexicon_gloss_translation` to the table lists; deploy translation tables from your machine, not from CI.
9. Deferred: an Indonesian RMAC dictionary for MySword (needs the real `rmac.dct.mybible` schema); a `reviewed` column for `ai_gloss`.

## 7. Known risks and unverified items

- Everything was tested on toy data. Nothing was run against your real database or the live model.
- Stage 4 output is a JSON array. An unescaped `"` in a verse loses that verse and the rest of its request. The prompt now says to escape quotes, but Genesis and the Psalms will show whether the model complies. A line-based output format (`verse<TAB>text`) removes the risk, if needed.
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

## 9. Commands (Windows, repo root)

```bat
python scripts\build_db.py --staging output\staging --schema schema.sql --out output\interlinear.sqlite
python scripts\load_lexicon_translations.py --db output\interlinear.sqlite --csv output\staging\lexicon_id.csv --language id --model <label>
python scripts\load_ai_glosses.py --db output\interlinear.sqlite --jsonl output\staging\aigloss-refs2.jsonl
python scripts\generate_ai_glosses.py submit --db output\interlinear.sqlite --out output\staging\aigloss-refs2.jsonl --group pauline_epistles --batch-job-file output\staging\batch_pauline.json --dry-run
python scripts\generate_verse_translations.py sync --db output\interlinear.sqlite --out output\staging\verses_id.jsonl --book GEN --chapter 1
python scripts\export_to_markdown.py --db output\interlinear.sqlite --out-dir output\reading --compact --group --verse-style caret
```
