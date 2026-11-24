#!/usr/bin/env python3
"""
Test AI gloss generation against SPECIFIC chapters (not "first N
chapters of a book" like --limit-chapters), and render the result as
readable Markdown for quick review -- instead of raw JSONL.

Why this exists as a separate script rather than extending
generate_ai_glosses.py directly: that script's --limit-chapters takes
the first N chapters of a book/group/testament, which is the right
shape for "process everything up through chapter N" but the wrong
shape for "test exactly John 20 without also processing and paying
for/rate-limiting against John 1-19 first". This script adds a
--chapter flag (pairs with --book) for that, and reuses
generate_ai_glosses.py's own functions (get_chapter_words,
split_into_batches, call_model) directly via import rather than
re-implementing any of the lexicon-join or batching logic -- so this
stays in sync with that script's own (already-fixed) duplicate-row
join and verse-boundary-safe splitting.

Also supports --provider openrouter/gemini (same OpenAI-compatible
pattern as translate_lexicon.py) for testing cheaply before spending
real Claude usage, addressing the free-model rate-limit concern
directly: since generate_ai_glosses.py already batches per-chapter
(with oversized chapters further split at --max-words-per-batch, e.g.
John 6's ~1,285 words split into 2 calls at the default 700-word
threshold), targeting ONE specific chapter here means exactly one (or
a small few, if that single chapter is itself oversized) API call(s)
-- not one call per chapter from 1 up through your target the way
--limit-chapters would.

Usage:
  # Single specific chapter, Claude:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 scripts/test_gloss_chapter.py \
    --db output/interlinear.sqlite \
    --book JHN --chapter 20 \
    --language id --out-dir output/staging/gloss_review

  # Multiple specific chapters in one run (comma-separated):
  python3 scripts/test_gloss_chapter.py \
    --db output/interlinear.sqlite \
    --book MAT --chapter 13,14 \
    --language id --out-dir output/staging/gloss_review

  # Free model, to sanity-check plumbing/prompt before spending real credits:
  export OPENAI_API_KEY=sk-or-v1-...
  python3 scripts/test_gloss_chapter.py \
    --db output/interlinear.sqlite \
    --book JHN --chapter 20 \
    --language id --out-dir output/staging/gloss_review \
    --provider openrouter --model "openrouter/free"

Output: one Markdown file per requested chapter, e.g.
  output/staging/gloss_review/JHN_20.md
containing, per verse: the original-language line, then a per-word
table (surface / translit / Strong's / morph / lexicon gloss (for
comparison) / AI CONTEXTUAL gloss (this run's result) / alt_gloss /
note) -- laid out specifically so the lexicon gloss and the new AI
gloss sit next to each other for a fast "did it actually adapt, or
just copy the lexicon" visual check, which is exactly the kurios/
Yesus-vs-Yosua/Father-vs-father kind of case worth eyeballing before
a full run.

Does NOT write to ai_gloss or touch the database -- this is a
dry-run/preview tool. Nothing here is loaded anywhere; review the
Markdown, then run the real generate_ai_glosses.py sync/submit for
chapters you're satisfied with.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import sqlite3

from generate_ai_glosses import (
    SYSTEM_PROMPT_TEMPLATE,
    get_chapter_words,
    split_into_batches,
    call_model,
    build_prompt,
)

try:
    import anthropic
except ImportError:
    anthropic = None


PROVIDER_DEFAULTS = {
    "anthropic": {
        "env_var": "ANTHROPIC_API_KEY",
        "default_model": "claude-opus-5",
    },
    "openrouter": {
        "env_var": "OPENAI_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "openrouter/free",
    },
    "gemini": {
        "env_var": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "default_model": "gemini-2.5-flash",
    },
}


def call_model_openai_compatible(client, model, words, language, max_retries=3):
    """Same OpenAI-compatible call path as translate_lexicon.py's
    provider support, adapted to this script's word-batch shape
    (build_prompt/SYSTEM_PROMPT_TEMPLATE come from generate_ai_glosses,
    imported above -- not redefined here, so both scripts always send
    the identical prompt for a given chapter, whichever provider runs
    it)."""
    import json
    import time

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(language=language)
    prompt = build_prompt(words)
    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=8192,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            return json.loads(text)
        except Exception as e:
            print(f"    attempt {attempt}/{max_retries} failed: {e}", file=sys.stderr)
            if attempt == max_retries:
                raise
            time.sleep(2 ** attempt)
    return None


def build_client(provider):
    cfg = PROVIDER_DEFAULTS[provider]
    api_key = os.environ.get(cfg["env_var"])
    if not api_key:
        print(f"ERROR: {cfg['env_var']} environment variable not set (required for --provider {provider}).", file=sys.stderr)
        sys.exit(1)

    if provider == "anthropic":
        if anthropic is None:
            print("ERROR: anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
            sys.exit(1)
        return anthropic.Anthropic(api_key=api_key), call_model

    try:
        import openai
    except ImportError:
        print("ERROR: openai package not installed (required for --provider openrouter/gemini). "
              "Run: pip install openai", file=sys.stderr)
        sys.exit(1)
    client = openai.OpenAI(api_key=api_key, base_url=cfg["base_url"])
    return client, call_model_openai_compatible


def escape_md(text):
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_chapter_markdown(book_code, book_name, chapter, verses_words, results_by_word_id):
    """verses_words: dict verse_num -> list of word tuples (as returned
    by get_chapter_words). results_by_word_id: dict word_id -> the
    model's result dict for that word (gloss/alt_gloss/note), or {} if
    that word's batch failed."""
    lines = [f"# {book_name} {chapter}", ""]
    lines.append(
        "Columns: Surface | Translit | Strong's | Morph | "
        "Lexicon gloss (existing) | **AI contextual gloss (this test run)** | Alt | Note"
    )
    lines.append("")

    for verse_num in sorted(verses_words.keys()):
        words = verses_words[verse_num]
        surface_line = " ".join(w[3] for w in words)  # w[3] = surface
        lines.append(f"## {book_code} {chapter}:{verse_num}")
        lines.append(f"> {surface_line}")
        lines.append("")
        lines.append("| Surface | Translit | Strong's | Morph | Lexicon gloss | AI gloss | Alt | Note |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for w in words:
            word_id, verse, position, surface, translit, strongs, morph, gloss_source, lex_en, lex_target = w
            lexicon_gloss = lex_target or lex_en or gloss_source or ""
            result = results_by_word_id.get(word_id, {})
            ai_gloss = result.get("gloss", "(no result)")
            alt = result.get("alt_gloss") or ""
            note = result.get("note") or ""
            lines.append(
                f"| {escape_md(surface)} | {escape_md(translit)} | {escape_md(strongs)} | "
                f"{escape_md(morph)} | {escape_md(lexicon_gloss)} | **{escape_md(ai_gloss)}** | "
                f"{escape_md(alt)} | {escape_md(note)} |"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument("--book", required=True, help="Book code, e.g. JHN, MAT")
    ap.add_argument("--chapter", required=True, help="Comma-separated chapter number(s), e.g. '20' or '13,14'")
    ap.add_argument("--language", default="id")
    ap.add_argument("--provider", default="anthropic", choices=list(PROVIDER_DEFAULTS.keys()))
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-words-per-batch", type=int, default=700)
    args = ap.parse_args()

    model = args.model or PROVIDER_DEFAULTS[args.provider]["default_model"]
    book_code = args.book.upper()
    try:
        chapters = [int(c.strip()) for c in args.chapter.split(",") if c.strip()]
    except ValueError:
        print(f"ERROR: --chapter must be a number or comma-separated numbers, got '{args.chapter}'", file=sys.stderr)
        sys.exit(1)

    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(args.db)
    cur = conn.cursor()

    cur.execute("SELECT book_id, name FROM book WHERE code = ?", (book_code,))
    row = cur.fetchone()
    if not row:
        print(f"ERROR: book '{book_code}' not found", file=sys.stderr)
        sys.exit(1)
    book_id, book_name = row

    client, call_fn = build_client(args.provider)
    print(f"Provider: {args.provider}, model: {model}", file=sys.stderr)

    for chapter in chapters:
        print(f"\n[{book_code} {chapter}] fetching words...", file=sys.stderr)
        words = get_chapter_words(cur, book_id, chapter, args.language, already_done_ids=set())
        if not words:
            print(f"  WARNING: no words found for {book_code} {chapter} -- does this chapter exist?", file=sys.stderr)
            continue
        print(f"  {len(words)} words", file=sys.stderr)

        batches = split_into_batches(words, args.max_words_per_batch)
        print(f"  {len(batches)} API call(s)", file=sys.stderr)

        results_by_word_id = {}
        for batch_num, batch in enumerate(batches, 1):
            print(f"  batch {batch_num}/{len(batches)}...", file=sys.stderr)
            try:
                results = call_fn(client, model, batch, args.language)
            except Exception as e:
                print(f"  BATCH FAILED: {e}", file=sys.stderr)
                continue
            for r in results:
                results_by_word_id[r["word_id"]] = r

        verses_words = {}
        for w in words:
            verse_num = w[1]
            verses_words.setdefault(verse_num, []).append(w)

        md = render_chapter_markdown(book_code, book_name, chapter, verses_words, results_by_word_id)
        out_path = args.out_dir / f"{book_code}_{chapter}.md"
        out_path.write_text(md, encoding="utf-8")
        print(f"  -> {out_path}", file=sys.stderr)

    conn.close()
    print(f"\nDone. Review the .md files in {args.out_dir}. Nothing was written to the database.", file=sys.stderr)


if __name__ == "__main__":
    main()