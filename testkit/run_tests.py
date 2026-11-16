#!/usr/bin/env python3
"""
Smoke tests for the AI gloss pipeline on a toy database. No API calls, no network.

Covers generate_ai_glosses.py (prompt, sync, resume, --dry-run, targeted mode, submit/retrieve),
load_ai_glosses.py (position loading after a rebuild, cleaning), add_glosses_refs.py,
import_batch_results.py, remap_ai_glosses.py, check_term.py, find_term_drift.py,
generate_verse_translations.py (stage 4), export_to_markdown.py and parse_tahot.py.

Usage, from the package root:   python testkit/run_tests.py
Needs:  pip install anthropic        (testkit/bible_export_common.py is a stub of your real one)
"""
import contextlib
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "testkit")]
import anthropic          # noqa: E402
import mkdb               # noqa: E402
import batch_api_helper as bah   # noqa: E402
import generate_ai_glosses as gag   # noqa: E402
import load_ai_glosses, add_glosses_refs, import_batch_results, remap_ai_glosses   # noqa: E402
import check_term, find_term_drift   # noqa: E402
import generate_verse_translations as gvt, export_to_markdown as etm, parse_tahot   # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def run(main, *argv):
    """Call a script's main() with argv; return (stderr, stdout). SystemExit is caught."""
    old, err, out = sys.argv, io.StringIO(), io.StringIO()
    sys.argv = ["x", *map(str, argv)]
    try:
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            main()
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return err.getvalue(), out.getvalue()


def jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


calls = {"n": 0, "prompts": [], "systems": []}


def reply(prompt):
    """Answer a gloss prompt: one entry per word that is not context_only, rotating through three reply shapes."""
    ids = [int(i) for line in prompt.splitlines() if line.startswith("{") and '"context_only"' not in line
           for i in re.findall(r'"word_id": (\d+)', line)]
    objs = [json.dumps({"word_id": i, "gloss": f"g{i}"}) for i in ids]
    calls["n"] += 1
    return ["\n".join(objs), "[\n" + "\n".join(objs) + "\n]", "[" + ", ".join(objs) + "]"][calls["n"] % 3]


class FakeClient:
    def __init__(self, *a, **k):
        def create(model, max_tokens, system, messages):
            calls["prompts"].append(messages[0]["content"])
            calls["systems"].append(system)
            return NS(content=[NS(type="text", text=reply(messages[0]["content"]))])
        self.messages = NS(create=create)


anthropic.Anthropic = FakeClient
os.environ["ANTHROPIC_API_KEY"] = "test"

work = Path(tempfile.mkdtemp())
os.chdir(work)
mkdb.build("db.sqlite")
mkdb.build("db_rebuilt.sqlite", shift=2)          # same words, word_ids moved by 2 (simulated rebuild)

# ---- prompt ----
sp = gag.system_for("id")
check("prompt formats and asks for a real array", "Target language: id" in sp and "starts with [ and ends with ]" in sp)
check("prompt: lexicon is authoritative, one gloss per word, God pronouns, H853", all(k in sp for k in (
    "authoritative for terminology", 'never use "/"', "Pronouns referring to God", "(tanda objek)")))
check("TERM_RULES keep firman/Tuhan rules and carry no per-term baptizo rules",
      "H5002" in gag.TERM_RULES and "H136" in gag.TERM_RULES and "G907" not in gag.TERM_RULES)
w = (1, 1, 1, "x", "t", "H5002", "m", None, None, "ucapan")
check("term rules are sent only for words present", "H5002" in gag.term_rules_for([w]) and gag.term_rules_for([(1, 1, 1, "x", "t", "H0001", "m", None, None, "x")]) == "")
check("resolve_scope accepts comma lists", gag.resolve_scope("gen, jhn", None, None) == {"book_codes": ["GEN", "JHN"]}
      and gag.resolve_scope(None, "torah,gospels_acts", None)["book_codes"][-1] == "ACT")

# ---- sync, resume, dry-run ----
err, _ = run(gag.main, "sync", "--db", "db.sqlite", "--out", "out/GEN.jsonl", "--book", "GEN", "--max-words-per-batch", 5)
recs = jsonl("out/GEN.jsonl")
check("sync: 18 GEN words glossed, 3 requests (reply shapes JSON Lines / array / strict array all parsed)",
      len(recs) == 18 and "3 API calls" in err)
check("sync: every record carries book/chapter/verse/position/surface", all(
    all(k in r for k in ("book", "chapter", "verse", "position", "surface")) for r in recs))
err, _ = run(gag.main, "sync", "--db", "db.sqlite", "--out", "out/GEN.jsonl", "--book", "GEN")
check("sync: second run resumes and sends nothing", "0 API calls" in err and len(jsonl("out/GEN.jsonl")) == 18)
run(gag.main, "sync", "--db", "db.sqlite", "--out", "out/dry.jsonl", "--book", "EXO", "--dry-run")
check("--dry-run creates no output file", not Path("out/dry.jsonl").exists())

# ---- targeted mode ----
c = sqlite3.connect("db.sqlite")
c.executemany("insert into ai_gloss (word_id, gloss, prompt_version, model, generated_at) values (?,?,?,?,?)",
              [(2, "TUHAN-ku", "v1", "m", "t"), (5, "Allah", "v1", "m", "t")])
c.commit()
c.close()
calls["prompts"].clear(); calls["systems"].clear()
run(gag.main, "sync", "--db", "db.sqlite", "--out", "out/fix.jsonl", "--book", "GEN", "--strongs", "h5002", "--prompt-version", "v3")
fix = jsonl("out/fix.jsonl")
check("targeted: only the 6 H5002 words are regenerated", sorted(r["word_id"] for r in fix) == [1, 4, 7, 10, 13, 16])
check("targeted: other words are sent as context_only with their existing gloss",
      any('"gloss": "TUHAN-ku", "context_only": true' in p for p in calls["prompts"]))
check("targeted: system prompt (a cached block) carries the TARGETED MODE note and records carry the new prompt version",
      all("TARGETED MODE" in (s[0]["text"] if isinstance(s, list) else s) for s in calls["systems"])
      and {r["prompt_version"] for r in fix} == {"v3"})
err, _ = run(gag.main, "sync", "--db", "db.sqlite", "--out", "out/fix.jsonl", "--book", "GEN", "--strongs", "H5002")
check("targeted: rerun skips words already in the out file", "0 API calls" in err)

# ---- submit / retrieve with a fake batch API ----
store = {}


def fake_submit(client, requests, jobfile):
    jobfile.parent.mkdir(parents=True, exist_ok=True)
    jobfile.write_text(json.dumps({"batch_id": "msgbatch_test", "request_count": len(requests)}))
    store["req"] = requests


bah.submit_batch_job = fake_submit
bah.poll_batch_job = lambda *a, **k: NS(processing_status="ended")
bah.fetch_batch_results = lambda client, bid: {r[0]: reply(r[2]) for r in store["req"]}
_, _ = run(gag.main, "submit", "--db", "db.sqlite", "--out", "out/b.jsonl", "--book", "EXO", "--batch-job-file", "jobs/b.json", "--prompt-version", "v9")
job = json.loads(Path("jobs/b.json").read_text())
mem = json.loads(Path("jobs/b.membership.json").read_text())
check("submit: job file records model/language/prompt version; membership carries positions",
      job.get("prompt_version") == "v9" and next(iter(next(iter(mem.values())).values()))["book"] == "EXO")
err, _ = run(gag.main, "retrieve", "--out", "out/b.jsonl", "--batch-job-file", "jobs/b.json")
check("retrieve: writes records with positions and the job's prompt version",
      len(jsonl("out/b.jsonl")) == 3 and all("position" in r and r["prompt_version"] == "v9" for r in jsonl("out/b.jsonl")))
run(gag.main, "retrieve", "--out", "out/b.jsonl", "--batch-job-file", "jobs/b.json")
check("retrieve: running it twice adds no duplicates", len(jsonl("out/b.jsonl")) == 3)

# ---- loader: position matching after a rebuild, cleaning, legacy records ----
check("clean_gloss rules", all(load_ai_glosses.clean_gloss(a) == b for a, b in [
    ("-/ bumi", "bumi"), ("dan/ -", "dan"), ("sejak/ tanah", "sejak tanah"), ("[Obj.]", "(tanda objek)"),
    ("dan/ [Obj.]", "dan (tanda objek)"), ("-nya", "-nya"), ("-", "-"), ("(tanda objek) dia", "(tanda objek) dia")]))
rows = jsonl("out/GEN.jsonl")
rows[0]["gloss"] = "-/ bumi"                                      # a messy gloss to be cleaned at load
bad = dict(rows[1], position=rows[1]["position"], surface="WRONG", gloss="x")
legacy = {"word_id": 19, "gloss": "legacy", "language": "id", "model": "m", "prompt_version": "v1", "generated_at": "t"}
Path("load.jsonl").write_text("\n".join(json.dumps(r) for r in rows + [bad, legacy]) + "\n")
err, _ = run(load_ai_glosses.main, "--db", "db_rebuilt.sqlite", "--jsonl", "load.jsonl")
c = sqlite3.connect("db_rebuilt.sqlite")
got = dict(c.execute("select word_id, gloss from ai_gloss"))
check("loader: records land on the right words after word_ids moved by 2 (position match)",
      got.get(3) == "bumi" and got.get(4) == "g2")
check("loader: cleaned a gloss, skipped a changed surface, loaded a legacy word_id record, reported chapters/verses",
      "Cleaned 1 glosses" in err and "surface text differs" in err and "by word_id" in err and "chapters" in err)
c.close()

# ---- add_glosses_refs + import_batch_results ----
Path("old.jsonl").write_text("\n".join(json.dumps({"word_id": i, "gloss": f"o{i}", "language": "id", "model": "m",
                                                  "prompt_version": "v1", "generated_at": "t"}) for i in (1, 2)) + "\n")
err, _ = run(add_glosses_refs.main, "--db", "db.sqlite", "--in", "old.jsonl", "--out", "refs.jsonl")
check("add_glosses_refs: legacy records get positions", all("position" in r for r in jsonl("refs.jsonl")) and "2 given positions" in err)
res = {"custom_id": "gloss-00001", "result": {"type": "succeeded", "message": {"model": "m", "stop_reason": "end_turn", "content": [
    {"type": "text", "text": "\n".join(json.dumps({"word_id": i, "gloss": f"n{i}"}) for i in (2, 3, 4))}]}}}
Path("res.jsonl").write_text(json.dumps(res))
err, _ = run(import_batch_results.main, "--file", "res.jsonl", "--out", "refs.jsonl", "--db", "db.sqlite")
final = {r["word_id"]: r for r in jsonl("refs.jsonl")}
check("import_batch_results: reads JSON Lines replies, skips known words, writes positions",
      sorted(final) == [1, 2, 3, 4] and final[2]["gloss"] == "o2" and "position" in final[3] and "Wrote 2 new" in err)

# ---- remap ----
err, _ = run(remap_ai_glosses.main, "--old-db", "db.sqlite", "--new-db", "db_rebuilt.sqlite", "--in", "old.jsonl", "--out", "remapped.jsonl")
check("remap_ai_glosses: word_ids translated old -> rebuilt DB",
      [r["word_id"] for r in jsonl("remapped.jsonl")] == [3, 4] and "remapped 2" in err)

# ---- term checks ----
c = sqlite3.connect("db.sqlite")
c.execute("delete from ai_gloss")
c.executemany("insert into ai_gloss (word_id, gloss, prompt_version, model, generated_at) values (?,?,?,?,?)",
              [(22, "dibaptis", "v1", "m", "t"), (24, "membaptis", "v1", "m", "t"), (23, "firman", "v1", "m", "t")])
c.commit()
c.close()
_, out = run(check_term.main, "G907", "--db", "db.sqlite")
check("check_term: shows the lexicon gloss sent and the AI gloss returned", "membenamkan" in out and "dibaptis" in out)
_, out = run(find_term_drift.main, "--db", "db.sqlite", "--min-count", 1)
check("find_term_drift: flags G907 (membenamkan) as not followed, leaves G3056 (firman) alone",
      "G907" in out and "membenamkan" in out and "G3056" not in out.split("Targeted")[0])


# =====================  stage 4: natural verse translation  =====================
GUIDE = ROOT / "sources" / "eydv.md"
base = gvt.system_prompt("id")
check("stage 4 prompt: formats, tag braces rendered, one-backslash escaped-quote example",
      "langit{H8064}{H853}" in base and 'Ia berkata, \\"Marilah.\\"' in base and '\\\\"' not in base)
check("stage 4 prompt: paragraph, poetry, placeholder and God-pronoun rules present",
      all(k in base for k in ("¶", '" | "', "(tanda objek)", "Pronouns for God")))
withg = gvt.system_prompt("id", "RULES-GO-HERE")
check("guidelines are inserted before the response-format paragraph; no guidelines leaves the prompt unchanged",
      withg.index("RULES-GO-HERE") < withg.index("Respond with ONLY") and gvt.system_prompt("id", "") == base)
with contextlib.redirect_stderr(io.StringIO()) as e:
    missing_default = gvt.load_guidelines(None)                  # cwd is the temp dir: no sources/eydv.md here
check("load_guidelines: default file absent -> empty text and a NOTE", missing_default == "" and "NOTE" in e.getvalue())
try:
    gvt.load_guidelines("nope.md")
    check("load_guidelines: explicit missing file exits", False)
except SystemExit:
    check("load_guidelines: explicit missing file exits", True)
with contextlib.redirect_stderr(io.StringIO()):
    check("load_guidelines: reads sources/eydv.md and it carries the rule references", "[I.F.7]" in gvt.load_guidelines(str(GUIDE)))


def reply4(prompt):
    """Stage 4 answer: JSON Lines (no brackets) with quotes, a paragraph mark and one poetic verse."""
    vs = [int(v) for v in re.findall(r"^(\d+):", prompt, re.M)]
    rows = []
    for v in vs:
        text = f'Ia berkata, "Marilah{{H3068}} kita." Lalu \'ya\' {v}'
        if v == vs[0]:
            text = "¶ " + text
        if v == 3:
            text = "Berbahagialah | yang tidak berjalan{H5002} | dalam nasihat"
        rows.append(json.dumps({"verse": v, "text": text}, ensure_ascii=False))
    return "\n".join(rows)


class FakeClient4:
    def __init__(self, *a, **k):
        def create(model, max_tokens, system, messages):
            calls["systems"].append(system)
            return NS(content=[NS(type="text", text=reply4(messages[0]["content"]))])
        self.messages = NS(create=create)


anthropic.Anthropic = FakeClient4
calls["systems"].clear()
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_gen1.jsonl", "--book", "GEN", "--chapter", "1", "--guidelines", GUIDE)
v = jsonl("out/v_gen1.jsonl")
check("stage 4 sync: 4 verses written; quotes, paragraph mark and poetry bars survive",
      len(v) == 4 and all('"Marilah' in r["text"] for r in v if "Berbahagialah" not in r["text"])
      and v[0]["text"].startswith("¶") and " | " in v[2]["text"])
sysblock = calls["systems"][0]
check("stage 4 sync: system prompt is a cached block that carries the EYD rules",
      isinstance(sysblock, list) and sysblock[0]["cache_control"] == {"type": "ephemeral"} and "[I.F.7]" in sysblock[0]["text"])
run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_gen1.jsonl", "--book", "GEN", "--chapter", "1", "--guidelines", GUIDE)
check("stage 4 sync: rerun resumes without duplicating verses", len(jsonl("out/v_gen1.jsonl")) == 4)

# submit / retrieve
bah.fetch_batch_results = lambda client, bid: {r[0]: reply4(r[2]) for r in store["req"]}
run(gvt.main, "submit", "--db", "db.sqlite", "--out", "out/v_exo.jsonl", "--book", "EXO", "--guidelines", GUIDE, "--batch-job-file", "jobs/v.json")
check("stage 4 submit: one request per chapter chunk, system prompt carries the guidelines",
      len(store["req"]) == 1 and "[I.F.7]" in store["req"][0][1])
run(gvt.main, "retrieve", "--out", "out/v_exo.jsonl", "--batch-job-file", "jobs/v.json")
check("stage 4 retrieve: verses written once", len(jsonl("out/v_exo.jsonl")) == 1)

# load: unknown and zero-padded tags
rec = {"verse_id": 1, "language": "id", "text": "Allah{H3068}{H999} berfirman{H5002} tuan{H0113}",
       "model": "m", "prompt_version": "v2", "generated_at": "t"}
Path("load4.jsonl").write_text(json.dumps(rec) + "\n")
err, _ = run(gvt.main, "load", "--db", "db.sqlite", "--jsonl", "load4.jsonl")
c = sqlite3.connect("db.sqlite")
stored = c.execute("select text from verse_translation where verse_id = 1").fetchone()[0]
check("stage 4 load: unknown tag dropped, padded tag normalised, report printed",
      stored == "Allah{H3068} berfirman{H5002} tuan{H113}" and "1 unknown tag(s) dropped" in err)

# =====================  export_to_markdown (compact reading format)  =====================
c.executemany("insert or replace into verse_translation (verse_id, language, text, model, prompt_version, generated_at) values (?,?,?,?,?,?)", [
    (1, "id", "¶ Pada mula{H5002} Allah menciptakan", "m", "v", "t"),
    (2, "id", "Bumi belum berbentuk", "m", "v", "t"),
    (3, "id", "¶ Berbahagialah orang itu{H3068} | yang tidak berjalan | dalam nasihat", "m", "v", "t")])
c.execute("insert into verse values (60, 1, 3, 0)")                           # a Psalm-style title: verse 0 of GEN 3
c.execute("insert into verse values (61, 1, 3, 1)")
c.executemany("insert into verse_translation (verse_id, language, text, model, prompt_version, generated_at) values (?,?,?,?,?,?)",
              [(60, "id", "Untuk pemimpin pujian", "m", "v", "t"), (61, "id", "¶ Mengapa bangsa-bangsa ribut", "m", "v", "t")])
c.commit()
c.close()
err, _ = run(etm.main, "--db", "db.sqlite", "--out-dir", "md1", "--compact", "--book", "GEN")
md = Path("md1/01_GEN_Genesis_compact.md").read_text(encoding="utf-8")
lines = md.split("\n")
check("markdown: book-name headings, superscript verse numbers, summary shows chapters/verses",
      "## Genesis 1" in md and "¹ Pada mula Allah menciptakan" in md and "chapters" in err and "Chapter" not in md)
check("markdown: verses of a paragraph are consecutive lines (no blank line between them)",
      lines[lines.index("¹ Pada mula Allah menciptakan") + 1] == "² Bumi belum berbentuk")
check("markdown: a paragraph mark starts a new paragraph after a blank line",
      lines[lines.index("² Bumi belum berbentuk") + 1] == "" and "³ Berbahagialah orang itu  " in md)
check("markdown: poetry lines are indented and hard-broken; the last line of the paragraph is not",
      "   yang tidak berjalan  \n   dalam nasihat  \n⁴ ucapan TUHAN tuan\n" in md and "⁴ ucapan TUHAN tuan  " not in md)
check("markdown: a verse-0 title is an italic line without a number", "*Untuk pemimpin pujian*" in md)
check("markdown: a verse without a translation falls back to joined glosses without placeholders", "⁴ ucapan TUHAN tuan" in md)
run(etm.main, "--db", "db.sqlite", "--out-dir", "md2", "--compact", "--book", "GEN", "--verse-style", "caret", "--strongs")
md2 = Path("md2/01_GEN_Genesis_compact.md").read_text(encoding="utf-8")
check("markdown: --verse-style caret writes ^n^ and --strongs keeps the tags", "^1^ Pada mula{H5002} Allah menciptakan" in md2)
run(etm.main, "--db", "db.sqlite", "--out-dir", "md3", "--compact", "--book", "GEN", "--verse-style", "plain")
check("markdown: --verse-style plain writes a bare number", "\n1 Pada mula Allah menciptakan" in Path("md3/01_GEN_Genesis_compact.md").read_text(encoding="utf-8"))
run(etm.main, "--db", "db.sqlite", "--out-dir", "md4", "--book", "GEN", "--wikilink")
md4 = Path("md4/01_GEN_Genesis.md").read_text(encoding="utf-8")
check("markdown: interlinear mode keeps full references (and --wikilink wraps them)", "**[[GEN 1:1]]**" in md4)
err, _ = run(etm.main, "--db", "nope.sqlite", "--out-dir", "md5")
check("markdown: a missing database stops with a clear error instead of creating one", "missing or empty" in err and not Path("nope.sqlite").exists())

# =====================  parse_tahot  =====================
def trow(ref, n, flag):
    return f"{ref}#{n:02d}={flag}\tא\tx\tg\t{{H0001}}\tHNcmsa\n"


lines = ["Gen.1.1\tsummary-block line without #NN must be ignored\n"]
lines += [trow("Gen.1.1", i, "L") for i in (1, 2)]
lines += [trow("Psa.67.0(67.1)", i, "L") for i in (1, 2, 3)] + [trow("Psa.67.1(67.2)", i, "L") for i in (1, 2)]
lines += [trow("Num.26.1(25.19)", i, "L") for i in (1, 2, 3)] + [trow("Num.26.1", i, "L") for i in (1, 2, 3, 4, 5)]
lines += [trow("Deu.30.16", i, "X") for i in (1, 2, 3)] + [trow("Deu.30.16", i, "L") for i in (1, 2, 3, 4)]
lines += [trow("Mal.4.1(3.19)", i, "Q(K)") for i in (1, 2, 3)]
lines += [trow("Gen.1.2x", 1, "L")]
Path("tahot_src").mkdir()
Path("tahot_src/TAHOT Test.txt").write_text("".join(lines), encoding="utf-8")
err, _ = run(parse_tahot.main, "tahot_src", "tahot_out.jsonl")
rows = jsonl("tahot_out.jsonl")
keys = [(r["book_code"], r["chapter"], r["verse"], r["position"]) for r in rows]
check("parse_tahot: bracketed references parse; English numbering kept (Psa 67:0 title, Mal 4:1)",
      {(r["book_code"], r["chapter"], r["verse"]) for r in rows} >= {("PSA", 67, 0), ("PSA", 67, 1), ("MAL", 4, 1)})
check("parse_tahot: merged verses get unique, ordered positions (Num 26:1 = 1..8)",
      sorted(p for b, c, vv, p in keys if (b, c, vv) == ("NUM", 26, 1)) == list(range(1, 9)) and len(keys) == len(set(keys)))
check("parse_tahot: X rows skipped (counted), Ketiv/Qere rows kept",
      not any(r["source_flag"] == "X" for r in rows) and "3 X rows skipped" in err and any(r["source_flag"] == "Q(K)" for r in rows))
check("parse_tahot: an unparseable detail row is reported, not silently dropped",
      "1 detail rows could not be parsed" in err and "TOTAL:" in err)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
