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
import generate_verse_translations as gvt, export_to_markdown as etm, parse_tahot, show_verse, parse_tr   # noqa: E402

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
    jobfile.write_text(json.dumps({"batch_id": "msgbatch_test", "request_count": len(requests)}), encoding="utf-8")
    store["req"] = requests


bah.submit_batch_job = fake_submit
bah.poll_batch_job = lambda *a, **k: NS(processing_status="ended")
bah.fetch_batch_results = lambda client, bid: {r[0]: reply(r[2]) for r in store["req"]}
_, _ = run(gag.main, "submit", "--db", "db.sqlite", "--out", "out/b.jsonl", "--book", "EXO", "--batch-job-file", "jobs/b.json", "--prompt-version", "v9")
job = json.loads(Path("jobs/b.json").read_text(encoding="utf-8"))
mem = json.loads(Path("jobs/b.membership.json").read_text(encoding="utf-8"))
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
Path("load.jsonl").write_text("\n".join(json.dumps(r) for r in rows + [bad, legacy]) + "\n", encoding="utf-8")
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
                                                  "prompt_version": "v1", "generated_at": "t"}) for i in (1, 2)) + "\n", encoding="utf-8")
err, _ = run(add_glosses_refs.main, "--db", "db.sqlite", "--in", "old.jsonl", "--out", "refs.jsonl")
check("add_glosses_refs: legacy records get positions", all("position" in r for r in jsonl("refs.jsonl")) and "2 given positions" in err)
res = {"custom_id": "gloss-00001", "result": {"type": "succeeded", "message": {"model": "m", "stop_reason": "end_turn", "content": [
    {"type": "text", "text": "\n".join(json.dumps({"word_id": i, "gloss": f"n{i}"}) for i in (2, 3, 4))}]}}}
Path("res.jsonl").write_text(json.dumps(res), encoding="utf-8")
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
_, out_g = run(find_term_drift.main, "--db", "db.sqlite", "--min-count", 1, "--only", "G")
_, out_h = run(find_term_drift.main, "--db", "db.sqlite", "--min-count", 1, "--only", "H")
check("find_term_drift --only G keeps Greek numbers and prints how many words a targeted run would redo; --only H drops them",
      "G907" in out_g and "--strongs G907" in out_g and "2 words carry these numbers" in out_g and "G907" not in out_h)
c = sqlite3.connect("db.sqlite")
c.execute("insert into ai_gloss (word_id, gloss, prompt_version, model, generated_at) values (3, 'raja', 'v1', 'm', 't')")   # H0113 'tuan' -> 'raja': a second drifting number
c.commit()
_, out_all = run(find_term_drift.main, "--db", "db.sqlite", "--min-count", 1)
_, out_one = run(find_term_drift.main, "--db", "db.sqlite", "--min-count", 1, "--regen", 1)
check("find_term_drift --regen: all offenders by impact by default, only the worst with --regen 1",
      "--strongs G907,H113" in out_all and "--strongs G907\n" in out_one and "H113" not in out_one.split("Targeted")[1])
c.execute("delete from ai_gloss where word_id = 3")
c.commit()
c.close()


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

# ---- stage 4: line-based reply format (no quote escaping) ----
lp = gvt.system_prompt("id", "RULES-GO-HERE", "lines")
check("lines prompt: asks for 'N<TAB>text', drops the JSON and escaping rules, keeps guidelines before the format paragraph",
      "a tab character" in lp and "1\t¶ Pada mula{G746}" in lp and "JSON array" not in lp and "escape every double quotation" not in lp
      and lp.index("RULES-GO-HERE") < lp.index("Respond with ONLY"))
check("json prompt (the default) still asks for a JSON array with escaped quotes", "JSON array" in base and "escape every double quotation" in base)
check("parse_reply: tab lines keep double quotes as written",
      gvt.parse_reply('1\tIa berkata, "Marilah." Lalu\n2\t¶ Kedua') == [{"verse": 1, "text": 'Ia berkata, "Marilah." Lalu'}, {"verse": 2, "text": "¶ Kedua"}])
check("parse_reply: a colon after the number works and a wrapped line continues the verse",
      gvt.parse_reply("1: satu\ndua lanjut\n2: tiga") == [{"verse": 1, "text": "satu dua lanjut"}, {"verse": 2, "text": "tiga"}])
check("parse_reply: JSON arrays, JSON Lines and fenced JSON are still accepted; garbage gives None",
      [x["verse"] for x in gvt.parse_reply('[{"verse": 1, "text": "a"}]')] == [1]
      and [x["verse"] for x in gvt.parse_reply('{"verse": 1, "text": "a"}\n{"verse": 2, "text": "b"}')] == [1, 2]
      and gvt.parse_reply('```json\n[{"verse": 3, "text": "c"}]\n```')[0]["verse"] == 3
      and gvt.parse_reply("Maaf, saya tidak bisa.") is None and gvt.parse_reply("") is None)


def reply4_lines(prompt):
    vs = [int(v) for v in re.findall(r"^(\d+):", prompt, re.M)]
    return "\n".join(f'{v}\t' + ("¶ " if v == vs[0] else "") + f'Ia berkata, "Marilah{{H3068}} kita." Lalu \'ya\' {v}' for v in vs)


class FakeClient4L:
    def __init__(self, *a, **k):
        def create(model, max_tokens, system, messages):
            calls["systems"].append(system)
            return NS(content=[NS(type="text", text=reply4_lines(messages[0]["content"]))])
        self.messages = NS(create=create)


anthropic.Anthropic = FakeClient4L
calls["systems"].clear()
run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_lines.jsonl", "--book", "GEN", "--chapter", "1", "--guidelines", GUIDE, "--format", "lines")
vl = jsonl("out/v_lines.jsonl")
check("lines sync: 4 verses written; unescaped double quotes and the paragraph mark survive",
      len(vl) == 4 and all('"Marilah' in r["text"] for r in vl) and vl[0]["text"].startswith("¶"))
check("lines sync: the system prompt sent is the lines version", "a tab character" in calls["systems"][0][0]["text"])
bah.fetch_batch_results = lambda client, bid: {r[0]: reply4_lines(r[2]) for r in store["req"]}
run(gvt.main, "submit", "--db", "db.sqlite", "--out", "out/v_lines2.jsonl", "--book", "EXO", "--guidelines", GUIDE, "--format", "lines", "--batch-job-file", "jobs/vl.json")
check("lines submit: requests carry the lines prompt", "a tab character" in store["req"][0][1])
run(gvt.main, "retrieve", "--out", "out/v_lines2.jsonl", "--batch-job-file", "jobs/vl.json")
check("lines retrieve: verses written with their quotes", len(jsonl("out/v_lines2.jsonl")) == 1 and '"Marilah' in jsonl("out/v_lines2.jsonl")[0]["text"])
anthropic.Anthropic = FakeClient4
bah.fetch_batch_results = lambda client, bid: {r[0]: reply4(r[2]) for r in store["req"]}

# ---- stage 4: a JSON reply with an unescaped quote is salvaged, retrieve is repeatable ----
bad = '[{"verse": 1, "text": "ok"}, {"verse": 2, "text": "Ia berkata, "Marilah." Lalu"}, {"verse": 3, "text": "hilang"}]'
with contextlib.redirect_stderr(io.StringIO()) as e:
    got = gvt.parse_reply(bad)
check("salvage: an unescaped quote loses nothing; the verses after it are kept",
      [x["verse"] for x in got] == [1, 2, 3] and got[1]["text"] == 'Ia berkata, "Marilah." Lalu' and "recovered 3 verses" in e.getvalue())
pretty = '[\n  {\n    "verse": 1,\n    "text": "Satu \\"benar\\""\n  },\n  {\n    "verse": 2,\n    "text": "Dua "salah" \u00b6 ini"\n  }\n]'
with contextlib.redirect_stderr(io.StringIO()):
    got = gvt.parse_reply(pretty)
check("salvage: pretty-printed objects work, escaped and bare quotes both come out as plain quotes",
      [x["text"] for x in got] == ['Satu "benar"', 'Dua "salah" \u00b6 ini'])
with contextlib.redirect_stderr(io.StringIO()):
    got = gvt.parse_reply('{"verse": 1, "text": "baris\\nbaru \\u00b6 \\"q\\""}\n{"verse": 2, "text": "Ia berkata, "Marilah.""}')
check("salvage: JSON escapes are decoded, and a text ending in a bare quote is recovered",
      got[0]["text"] == 'baris\nbaru \u00b6 "q"' and got[1]["text"] == 'Ia berkata, "Marilah."')
with contextlib.redirect_stderr(io.StringIO()):
    got = gvt.parse_reply('[{"verse": 1, "text": "a"}, {"verse": 2, "text": "setengah ja')
check("salvage: a reply cut off by max_tokens keeps the complete verses and drops the half-written one", [x["verse"] for x in got] == [1])


def reply_bad_quotes(prompt):
    vs = [int(v) for v in re.findall(r"^(\d+):", prompt, re.M)]
    objs = [f'{{"verse": {v}, "text": "Ia berkata, "Marilah" kita {v}"}}' if v == 2 else f'{{"verse": {v}, "text": "ayat {v}"}}' for v in vs]
    return "[" + ", ".join(objs) + "]"


bah.fetch_batch_results = lambda client, bid: {r[0]: reply_bad_quotes(r[2]) for r in store["req"]}
run(gvt.main, "submit", "--db", "db.sqlite", "--out", "out/v_salv.jsonl", "--book", "GEN", "--chapter", "1", "--batch-job-file", "jobs/sv.json")
with contextlib.redirect_stderr(io.StringIO()):
    run(gvt.main, "retrieve", "--out", "out/v_salv.jsonl", "--batch-job-file", "jobs/sv.json")
sv = jsonl("out/v_salv.jsonl")
check("retrieve: all 4 verses of a reply with a bad quote in verse 2 are written (before the fix: only verse 1)",
      [r["verse_id"] for r in sv] == [1, 2, 3, 4] and sv[1]["text"] == 'Ia berkata, "Marilah" kita 2')
err, _ = run(gvt.main, "retrieve", "--out", "out/v_salv.jsonl", "--batch-job-file", "jobs/sv.json")
check("retrieve run again: nothing is written twice and no false 'missing' warning", len(jsonl("out/v_salv.jsonl")) == 4 and "Wrote 0 new verses" in err and "missing" not in err)
bah.fetch_batch_results = lambda client, bid: {r[0]: reply4_lines(r[2]) for r in store["req"]}
run(gvt.main, "retrieve", "--out", "out/v_lines2.jsonl", "--batch-job-file", "jobs/vl.json")
check("retrieve run again (lines job): still one record", len(jsonl("out/v_lines2.jsonl")) == 1)

# ---- stage 4: --group / --testament / --book lists and --dry-run ----
made = []


class CountingClient:
    def __init__(self, *a, **k):
        made.append(1)


anthropic.Anthropic = CountingClient
store["req"] = None
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_dry.jsonl", "--group", "torah", "--dry-run")
check("dry run (--group torah): requests, chapters, verses and words reported; no client, no output file",
      "3 request(s): 3 chapters, 7 verses, 21 words to translate" in err and "Dry run" in err and not made and not Path("out/v_dry.jsonl").exists())
err, _ = run(gvt.main, "submit", "--db", "db.sqlite", "--out", "out/v_dry.jsonl", "--testament", "NT", "--dry-run", "--batch-job-file", "jobs/dry.json")
check("submit --testament NT --dry-run: only Matthew, nothing submitted", "1 request(s): 1 chapters, 1 verses, 3 words" in err and store["req"] is None
      and not Path("jobs/dry.json").exists())
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_dry.jsonl", "--book", "GEN,EXO", "--chapter", "1", "--dry-run")
check("--book takes a comma list and --chapter applies to each book", "2 request(s): 2 chapters, 5 verses, 15 words" in err)
check("--group and --testament together are refused; an unknown group is named",
      "only one of" in run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/x.jsonl", "--group", "torah", "--testament", "OT", "--dry-run")[0]
      and "unknown --group nonsense" in run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/x.jsonl", "--group", "nonsense", "--dry-run")[0])
anthropic.Anthropic = FakeClient4L
run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_group.jsonl", "--group", "torah", "--format", "lines", "--guidelines", GUIDE)
vg = jsonl("out/v_group.jsonl")
check("sync --group torah: all 7 verses of Genesis and Exodus are translated", sorted(r["verse_id"] for r in vg) == [1, 2, 3, 4, 5, 6, 7])
check("stage 4 prompt v3: poetry lines follow parallelism (two pairs = 4 lines, not 2), Selah stays on its line; v3 is the default version",
      "gets 4 lines, not 2" in base and '"Selah" stays at the end of its line' in base and "etnachta" in base and {r["prompt_version"] for r in vg} == {"v3"})
anthropic.Anthropic = FakeClient4
bah.fetch_batch_results = lambda client, bid: {r[0]: reply4(r[2]) for r in store["req"]}

# ---- stage 4: request size (a cut-off reply was the real cause of 350 missing verses) ----
c = sqlite3.connect("db.sqlite")
c.execute("insert into verse values (500, 1, 5, 0)") if False else None
for n in range(1, 26):                                                    # GEN 5: 25 verses of one word each
    c.execute("insert into verse values (?, 1, 5, ?)", (500 + n, n))
    c.execute("insert into word values (?, ?, 1, 'hebrew', 'w', 'w', 'H0113', '[\"H0113\"]', 'HN', NULL)", (900 + n, 500 + n))
c.commit()
c.close()
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_size.jsonl", "--book", "GEN", "--chapter", "5", "--dry-run")
check("default --max-verses is 20: a 25-verse chapter is sent as 2 requests", "2 request(s): 1 chapters, 25 verses" in err)
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_size.jsonl", "--book", "GEN", "--chapter", "5", "--max-verses", 40, "--dry-run")
check("--max-verses 40 puts them in one request", "1 request(s): 1 chapters, 25 verses" in err)


class CutOffClient:
    def __init__(self, *a, **k):
        def create(model, max_tokens, system, messages):
            return NS(stop_reason="max_tokens", content=[NS(type="text", text="1\tsatu\n2\tdua")])
        self.messages = NS(create=create)


anthropic.Anthropic = CutOffClient
err, _ = run(gvt.main, "sync", "--db", "db.sqlite", "--out", "out/v_cut.jsonl", "--book", "EXO", "--format", "lines")
check("sync: a reply with stop_reason max_tokens is reported as cut off, with the advice to lower --max-verses",
      "cut off by max_tokens" in err and "Lower --max-verses" in err)
anthropic.Anthropic = FakeClient4
c = sqlite3.connect("db.sqlite")
c.execute("delete from word where word_id > 900")
c.execute("delete from verse where verse_id > 500")
c.commit()
c.close()

# load: unknown and zero-padded tags
rec = {"verse_id": 1, "language": "id", "text": "Allah{H3068}{H999} berfirman{H5002} tuan{H0113}",
       "model": "m", "prompt_version": "v2", "generated_at": "t"}
Path("load4.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
err, _ = run(gvt.main, "load", "--db", "db.sqlite", "--jsonl", "load4.jsonl")
c = sqlite3.connect("db.sqlite")
stored = c.execute("select text from verse_translation where verse_id = 1").fetchone()[0]
check("stage 4 load: unknown tag dropped, padded tag normalised, report printed",
      stored == "Allah{H3068} berfirman{H5002} tuan{H113}" and "1 unknown tag(s) dropped" in err)
rec2 = dict(rec, verse_id=2, text="Bumi belum berbentuk")                     # no tags at all
rec3 = dict(rec, verse_id=3, text="Allah{H3068} berfirman{H5002}")            # tag for H0113 missing
Path("load4b.jsonl").write_text(json.dumps(rec2) + "\n" + json.dumps(rec3) + "\n", encoding="utf-8")
err, _ = run(gvt.main, "load", "--db", "db.sqlite", "--jsonl", "load4b.jsonl")
check("stage 4 load: names the verses to review (no tags / missing which Strong's) and how to look at them",
      "1 verse(s) with no tags" in err and "2 verse(s) missing some source Strong's" in err and "no tags: GEN 1:2" in err
      and "missing some Strong's: GEN 1:3 (H113)" in err and "show_verse.py --db <db> GEN.1.2" in err)
err, _ = run(gvt.main, "load", "--db", "db.sqlite", "--jsonl", "load4b.jsonl", "--show", 0)
check("stage 4 load --show 0: counts only, no listing", "  no tags:" not in err and "  missing some Strong's:" not in err and "2 verse(s) missing some source Strong's" in err)

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

# =====================  show_verse  =====================
err, out = run(show_verse.main, "--db", "db.sqlite", "GEN.1.1", "GEN.1.2-3", "GEN.3.0", "FOO.1.1", "bad")
check("show_verse: the exact model input line (surface|Strong's|gloss, unpadded numbers) and the stored tagged translation",
      "== GEN 1:1 (verse_id 1, 3 words) ==" in out and "INPUT: s1111|H5002|ucapan s1112|H3068|TUHAN s1113|H113|tuan" in out
      and "TEXT : \u00b6 Pada mula{H5002} Allah menciptakan" in out)
check("show_verse: a range shows each verse; a missing translation and a verse 0 title are handled",
      "== GEN 1:2 " in out and "== GEN 1:3 " in out and "== GEN 3:0 " in out and "(no translation stored yet)" in out + "(no translation stored yet)")
check("show_verse: a bad reference and an unknown book are reported, not fatal; the summary counts verses",
      "'bad' is not BOOK.CHAPTER" in err and "no verses found for FOO.1.1" in err and "4 verse(s) shown" in err)
_, out = run(show_verse.main, "--db", "db.sqlite", "GEN.1")
check("show_verse: BOOK.CHAPTER shows the whole chapter", out.count("== GEN 1:") == 4)
sys.argv = ["x", "--db", "nope.sqlite", "GEN.1.1"]
try:
    show_verse.main()
    exit_msg = ""
except SystemExit as e:                                   # bec.connect stops with sys.exit("ERROR: ...")
    exit_msg = str(e)
check("show_verse: a missing database stops with a clear error and creates nothing", "missing or empty" in exit_msg and not Path("nope.sqlite").exists())

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

# =====================  parse_tr  (variant readings)  =====================
utr = ("1:1 en 1722 {PREP} arch 746 {N-DSF} | etuqh | equqh | 2380 5681 {V-API-3S} o 3588 {T-NSM}\r\n"
       "1:2 adelfoi 80 {N-VPM} | | oti 3754 {CONJ} | o 3588 {T-NSM}\r\n kairov 2540 {N-NSM}\r\n"
       "1:3 kai 2532 {CONJ} | h 2228 {PRT} | h 1510 5725 {V-PAS-3S} | pornov 4205 {N-NSM}\r\n"
       "1:4 logov 3056 {N-NSM}\r\n"
       "1:5 qeov 2316 {N-NSM} | x 1 {PRT} oti 3754 {CONJ}\r\n")
Path("tr_src").mkdir()
Path("tr_src/MT.UTR").write_bytes(utr.encode("utf-8"))
err, _ = run(parse_tr.main, "tr_src", "tr_B.jsonl", "--report", "tr_rep.txt")                       # the CI call has no flags: default reading
B = {}
for row in jsonl("tr_B.jsonl"):
    B.setdefault(row["verse"], []).append((row["surface_translit"], row["strongs"]))
check("parse_tr: default reading B (Scrivener) keeps one reading per marker: respelled word, extra word, different word",
      [w for w, _ in B[1]] == ["en", "arch", "equqh", "o"] and [w for w, _ in B[2]] == ["adelfoi", "oti", "o", "kairov"]
      and B[3] == [("kai", "G2532"), ("h", "G1510"), ("pornov", "G4205")])
err_a, _ = run(parse_tr.main, "tr_src", "tr_A.jsonl", "--reading", "a")
A = {}
for row in jsonl("tr_A.jsonl"):
    A.setdefault(row["verse"], []).append((row["surface_translit"], row["strongs"]))
check("parse_tr: --reading A takes the other side (etuqh, no 'oti', G2228); verses without markers are identical",
      [w for w, _ in A[1]] == ["en", "arch", "etuqh", "o"] and [w for w, _ in A[2]] == ["adelfoi", "o", "kairov"]
      and A[3][1] == ("h", "G2228") and A[4] == B[4] == [("logov", "G3056")])
check("parse_tr: no '|' reaches the output and positions are consecutive from 1",
      not any("|" in json.dumps(x) for x in jsonl("tr_B.jsonl")) and all([i for i in range(1, len(v) + 1)] == [i + 1 for i in range(len(v))] for v in B.values())
      and [x["position"] for x in jsonl("tr_B.jsonl") if x["verse"] == 2] == [1, 2, 3, 4])
check("parse_tr: statistics and the variant report (word counts per reading) are printed and written",
      "TOTAL: 1 chapters, 5 verses, 15 words" in err and "Reading kept: B (Scrivener 1894); 4 verses had variant readings" in err
      and Path("tr_rep.txt").read_text(encoding="utf-8").splitlines()[:2] == ["MAT 1:1\tA=4\tB=4", "MAT 1:2\tA=3\tB=4"])
check("parse_tr: an unpaired '|' is reported, not fatal, and its words are still read", "MAT 1:5 has an unpaired '|'" in err and [w for w, _ in B[5]][:1] == ["qeov"])

# =====================  Windows encoding  =====================
import ast


def encoding_violations(path):
    """Text reads/writes that rely on the platform default encoding (cp1252 on Windows)."""
    out = []
    for n in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if not isinstance(n, ast.Call):
            continue
        f, kws = n.func, {k.arg for k in n.keywords}
        name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
        if name in ("read_text", "write_text") and "encoding" not in kws:
            out.append((n.lineno, name))
        elif name == "open":
            mode = next((k.value.value for k in n.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), None)
            if mode is None:
                pos = n.args[1:2] if isinstance(f, ast.Name) else n.args[:1]
                mode = pos[0].value if pos and isinstance(pos[0], ast.Constant) else "r"
            if "b" not in str(mode) and "encoding" not in kws:
                out.append((n.lineno, f"open({mode!r})"))
    return out


MAINTAINED = ["generate_ai_glosses", "load_ai_glosses", "add_glosses_refs", "import_batch_results", "remap_ai_glosses", "batch_api_helper",
              "check_term", "find_term_drift", "generate_verse_translations", "export_to_markdown", "parse_tahot", "parse_tr", "show_verse",
              "export_mysword", "export_esword", "export_osis", "export_to_d1", "generate_d1_reset", "bible_export_common", "export_ai_glosses"]
found = {n: encoding_violations(ROOT / "scripts" / f"{n}.py") for n in MAINTAINED if (ROOT / "scripts" / f"{n}.py").exists()}
check("windows: no maintained script reads or writes text without naming its encoding", not any(found.values()))
if any(found.values()):
    print("   ", {k: v for k, v in found.items() if v})
check("windows: this test file follows the same rule", not encoding_violations(__file__))

# the user's crash: retrieve read the membership file (UTF-8, with Hebrew) using the platform default encoding
c = sqlite3.connect("db.sqlite")
c.execute("update word set surface = ? where word_id = 19", ("\u05d0\u05b1\u05dc\u05b9\u05d4\u05b4\u05d9\u05dd",))
c.commit()
c.close()
anthropic.Anthropic = FakeClient
bah.submit_batch_job = fake_submit
bah.poll_batch_job = lambda *a, **k: NS(processing_status="ended")
bah.fetch_batch_results = lambda client, bid: {x[0]: reply(x[2]) for x in store["req"]}
run(gag.main, "submit", "--db", "db.sqlite", "--out", "out/heb.jsonl", "--book", "EXO", "--batch-job-file", "jobs/heb.json")
_orig_read = Path.read_text
Path.read_text = lambda self, encoding=None, errors=None: _orig_read(self, encoding=encoding or "cp1252", errors=errors)   # Windows default
try:
    err, _ = run(gag.main, "retrieve", "--out", "out/heb.jsonl", "--batch-job-file", "jobs/heb.json")
    crashed = None
except UnicodeDecodeError as e:
    crashed = e
finally:
    Path.read_text = _orig_read
check("windows: gloss retrieve with Hebrew in the membership file works when the default encoding is cp1252", crashed is None and len(jsonl("out/heb.jsonl")) == 3)

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
