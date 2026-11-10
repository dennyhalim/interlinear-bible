#!/usr/bin/env python3
"""
Smoke tests for the AI gloss pipeline on a toy database. No API calls, no network.

Covers generate_ai_glosses.py (prompt, sync, resume, --dry-run, targeted mode, submit/retrieve),
load_ai_glosses.py (position loading after a rebuild, cleaning), add_glosses_refs.py,
import_batch_results.py, remap_ai_glosses.py, check_term.py and find_term_drift.py.

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

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
