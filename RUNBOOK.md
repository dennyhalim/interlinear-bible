# Runbook: what to run next (Windows, repo root)

Order: A, finish stage 3 glosses; B, stage 4 trial and run; C, exports. Everything here uses the current scripts.
Keep one working gloss file: `output\staging\aigloss-refs2.jsonl` (records carry positions). Every `submit` gets its own `--batch-job-file`, and you `retrieve` it before the next `submit`.

## 0. Before you start

```bat
python testkit\run_tests.py
python testkit\test_exporters.py
```

Both should say all checks passed. Decide first:
- **Jesus pronouns** (Ia/Dia, -Nya capitalized for Jesus or not) and add it to the pronoun bullet in `SYSTEM_PROMPT_TEMPLATE`.
- **YHWH term** for `H3068`/`H3069` (and "Tuhan ALLAH" for Adonai YHWH, if you follow TB), so a rule can be added to `TERM_RULES`.

Bump `--prompt-version` (for example `v3`) whenever the prompt or rules change.

## A. Remaining glosses (stage 3): 346,873 words

Remaining: NT 55,728 (Pauline epistles, General epistles + Revelation), OT 291,145 (Torah, Historical, Wisdom, Major prophets). Do one group at a time.

1. **Test one chapter** (scratch file, nothing is loaded):
   ```bat
   python scripts\generate_ai_glosses.py sync --db output\interlinear.sqlite --out output\staging\glosses\test_rom.jsonl --book ROM --limit-chapters 1 --prompt-version v3
   ```
   Open the file and check: finite verbs show their subject, God pronouns are capitalized, no `/` or English markers in glosses, unrendered words are `-`.
2. **Dry run** (prints words, chapters and verses it would send, calls nothing):
   ```bat
   python scripts\generate_ai_glosses.py submit --db output\interlinear.sqlite --out output\staging\aigloss-refs2.jsonl --group pauline_epistles --batch-job-file output\staging\batch_pauline.json --prompt-version v3 --dry-run
   ```
3. **Submit**: the same command without `--dry-run`. Then, when the job has ended:
   ```bat
   python scripts\generate_ai_glosses.py retrieve --out output\staging\aigloss-refs2.jsonl --batch-job-file output\staging\batch_pauline.json
   ```
   Check the last lines: `Still unglossed after this retrieve` should be 0, and there should be no `cut off by max_tokens`.
4. **Load, then reapply the database-only fixes** (a load overwrites them):
   ```bat
   python scripts\load_ai_glosses.py --db output\interlinear.sqlite --jsonl output\staging\aigloss-refs2.jsonl
   ```
   H5002 before a divine name, and H136 tuan to Tuhan:
   ```sql
   UPDATE ai_gloss SET gloss = 'firman' WHERE word_id IN (
     SELECT w.word_id FROM word w JOIN word nxt ON nxt.verse_id = w.verse_id AND nxt.position = w.position + 1
     WHERE w.strongs LIKE 'H5002%'
       AND (nxt.strongs LIKE 'H3068%' OR nxt.strongs LIKE 'H3069%' OR nxt.strongs LIKE 'H0136%' OR nxt.strongs LIKE 'H0430%'));
   UPDATE ai_gloss SET gloss = REPLACE(REPLACE(gloss, 'tuan', 'Tuhan'), 'Tuan', 'Tuhan')
   WHERE word_id IN (SELECT word_id FROM word WHERE strongs LIKE 'H0136%') AND gloss LIKE '%tuan%';
   ```
5. **Check**:
   ```bat
   python scripts\find_term_drift.py --db output\interlinear.sqlite
   python scripts\check_term.py G907 --db output\interlinear.sqlite
   ```
   `find_term_drift` lists terms the model keeps overriding. Fix those with a targeted run, for example `--strongs G907,G908 --prompt-version v4 --out output\staging\glosses\fix_terms.jsonl`, then load that file.
6. **Cost**: take the Gospels job's cost from the Console usage page, divide by its 67,525 words, and multiply by the words in the group (the dry run shows them).

A job that returns replies that fail to parse: `python scripts\import_batch_results.py --batch-id msgbatch_... --out output\staging\aigloss-refs2.jsonl --db output\interlinear.sqlite` (needs the job to still be within 29 days).

## B. Stage 4: natural verse translation

Needs `sources\eydv.md` (loaded automatically; `--guidelines` to use another file) and the stage 3 glosses (words without one fall back to the lexicon gloss).

1. **Trial on a copy of the database**, so nothing real is loaded yet:
   ```bat
   copy output\interlinear.sqlite output\trial.sqlite
   python scripts\generate_verse_translations.py sync --db output\trial.sqlite --out output\staging\verses_trial.jsonl --book GEN --chapter 1 --prompt-version v2 --format lines
   python scripts\generate_verse_translations.py sync --db output\trial.sqlite --out output\staging\verses_trial.jsonl --book PSA --chapter 1 --format lines
   python scripts\generate_verse_translations.py sync --db output\trial.sqlite --out output\staging\verses_trial.jsonl --book JHN --chapter 1 --format lines
   python scripts\generate_verse_translations.py load --db output\trial.sqlite --jsonl output\staging\verses_trial.jsonl
   python scripts\export_to_markdown.py --db output\trial.sqlite --out-dir output\trial --compact --book GEN --verse-style caret
   ```
2. **Review checklist**
   - No `FAILED` lines while it ran. `--format lines` asks the model for one `N<TAB>text` line per verse, so double quotes need no escaping. With `--format json` (the default), a `response cut off or malformed` warning means the model left a double quote unescaped and lost the rest of that request: use `lines`.
   - The `load` report: `unknown tag(s) dropped` should be near 0; `verse(s) missing some source Strong's` shows how complete the tagging is.
   - Paragraphs (`¶`) every 3 to 8 verses and always at verse 1; Psalm 1 has its lines split into 2 or 3 per verse.
   - Direct speech: double quotes balanced, a quotation running over several verses opens once and closes once.
   - God's pronouns capitalized as in the glosses; spelling follows `eydv.md` (di and ke separate from places, -nya attached, `-Nya` hyphenated for God, numbers as digits for measures such as `40 hari`).
3. **Full run** (about 1,300 requests, one job):
   ```bat
   python scripts\generate_verse_translations.py submit --db output\interlinear.sqlite --out output\staging\verses_id.jsonl --batch-job-file output\staging\batch_verses.json --prompt-version v2 --model claude-opus-5-5 --format lines
   python scripts\generate_verse_translations.py retrieve --out output\staging\verses_id.jsonl --batch-job-file output\staging\batch_verses.json
   python scripts\generate_verse_translations.py load --db output\interlinear.sqlite --jsonl output\staging\verses_id.jsonl
   ```
   Re-run `submit` and `retrieve` for verses a job missed; verses already done are skipped.

## C. Exports (after the loads; they bake the text in)

```bat
python scripts\export_to_markdown.py --db output\interlinear.sqlite --out-dir output\reading --compact --group --verse-style caret
python scripts\export_mysword.py --db output\interlinear.sqlite --out output\exports\id_natural.bbl.mybible --language id --layout natural --description "Vox Antiqua (Indonesian)" --abbreviation VAN
python scripts\export_esword.py --db output\interlinear.sqlite --out output\exports\id_natural.bblx --language id --layout natural --description "Vox Antiqua (Indonesian)" --abbreviation VAN
python scripts\export_osis.py --db output\interlinear.sqlite --out output\exports\id.osis.xml --language id --layout natural --title "Vox Antiqua (Indonesian)"
```

Test each with `--book JUD` first. PDF from the Markdown: `--verse-style caret` avoids the missing-glyph warnings.

## D. After any rebuild

`build_db.py` empties `lexicon_gloss_translation`, `ai_gloss` and `verse_translation`. Reload them from the staging files (lexicon CSV, `aigloss-refs2.jsonl`, `verses_id.jsonl`); gloss files with positions load correctly even if `word_id`s moved. Then reapply the SQL fixes in A.4. Deploy translation tables to D1 from your machine only, never from CI.
