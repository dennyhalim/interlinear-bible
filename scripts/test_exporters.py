#!/usr/bin/env python3
"""
Tests for the Bible-module exporters on a toy database (no network).
Now: export_mysword.py. Add e-Sword and OSIS checks here as those files are updated.

Usage, from the package root:   python testkit/test_exporters.py
"""
import contextlib
import io
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "testkit")]
import mkdb                      # noqa: E402
import export_mysword as mysword  # noqa: E402

results = []


def check(name, cond):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name)


def run(main, *argv):
    old, err = sys.argv, io.StringIO()
    sys.argv = ["x", *map(str, argv)]
    try:
        with contextlib.redirect_stderr(err):
            main()
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return err.getvalue()


def fixture(path):
    """mkdb's toy Bible plus stage 4 translations, a Psalm-style title (GEN 3:0), and a Greek verse (MAT 3:1)."""
    mkdb.build(path)
    c = sqlite3.connect(path)
    c.executescript("create table verse_translation(verse_id integer, language text, text text, model text, "
                    "prompt_version text, generated_at text, reviewed integer default 0, primary key(verse_id, language));")
    c.execute("insert into verse values (60, 1, 3, 0)")
    c.execute("insert into verse values (61, 1, 3, 1)")
    c.execute("insert into word values (90, 60, 1, 'hebrew', 'mizmor', 'miz', 'H4210', '[\"H4210\"]', 'HNcmsa', NULL)")
    c.execute("insert into word values (91, 61, 1, 'hebrew', 'lamah', 'la', 'H3068G', '[\"H3068G\"]', 'HNcmsa', NULL)")
    c.execute("insert into ai_gloss (word_id, gloss) values (90, 'pujian')")
    c.executemany("insert into verse_translation (verse_id, language, text, model, prompt_version, generated_at) values (?,?,?,?,?,?)", [
        (1, "id", "¶ Pada mula{H5002} Allah menciptakan", "m", "v", "t"),
        (2, "id", "Bumi belum berbentuk", "m", "v", "t"),
        (3, "id", "¶ Berbahagialah orang itu{H3068} | yang tidak berjalan | dalam & nasihat", "m", "v", "t"),
        (60, "id", "Untuk pemimpin | pujian{H4210}", "m", "v", "t"),
        (61, "id", "¶ Mengapa bangsa-bangsa ribut{H3068G}", "m", "v", "t"),
        (8, "id", "Pada mulanya{G3056} adalah Firman{G907}", "m", "v", "t")])
    c.commit()
    c.close()


def rows(path, where="1=1"):
    c = sqlite3.connect(path)
    r = c.execute(f"select Book, Chapter, Verse, Scripture from Bible where {where} order by 1,2,3").fetchall()
    d = c.execute("select * from Details").fetchone()
    c.close()
    return r, d


os.chdir(tempfile.mkdtemp())
fixture("db.sqlite")
common = ["--db", "db.sqlite", "--language", "id", "--description", "T", "--abbreviation", "VAT"]

# ---------------- export_mysword ----------------
err = run(mysword.main, *common, "--out", "o/nat.mybible", "--layout", "natural")
r, d = rows("o/nat.mybible")
by = {(b, c, v): s for b, c, v, s in r}
check("mysword natural: Strong's tags become unpadded <WH..>/<WG..>",
      by[(1, 1, 1)].startswith("Pada mula<WH5002> Allah") and "<WG3056>" in by[(40, 3, 1)])
check("mysword natural: a paragraph-start verse ends the previous verse with <CM>; the mark itself is removed",
      by[(1, 1, 2)].endswith("<CM>") and not by[(1, 1, 1)].endswith("<CM>") and "\u00b6" not in "".join(by.values()))
check("mysword natural: poetry lines joined with <br>; text escaped",
      "orang itu<WH3068><br>yang tidak berjalan<br>dalam &amp; nasihat" in by[(1, 1, 3)])
check("mysword natural: verse 0 folded into verse 1 as <TS>..<Ts> (no tags or bars inside); no verse-0 row",
      by[(1, 3, 1)].startswith("<TS>Untuk pemimpin pujian<Ts>Mengapa") and not any(v == 0 for b, c, v, s in r))
check("mysword natural: verses with no translation are skipped and reported", (1, 1, 4) not in by and "verses skipped" in err)
check("mysword natural: the summary prints chapters and verses", "chapters" in err and "verses ->" in err)
check("mysword Details: Language is the 3-letter code; Strong, OT and NT flags set", d[6] == "ind" and d[8] == 1 and d[9] == 1 and d[10] == 1)
run(mysword.main, *common, "--out", "o/sp.mybible", "--layout", "natural", "--poetry", "space")
r2, _ = rows("o/sp.mybible", "Book=1 and Chapter=1 and Verse=3")
check("mysword natural --poetry space: lines joined by a space", "<br>" not in r2[0][3] and "orang itu<WH3068> yang tidak berjalan" in r2[0][3])
run(mysword.main, *common, "--out", "o/int.mybible", "--layout", "interlinear")
r3, _ = rows("o/int.mybible")
by3 = {(b, c, v): s for b, c, v, s in r3}
check("mysword interlinear: Hebrew words get Strong's but no <WT>; Greek words get both",
      "<WH5002>" in by3[(1, 1, 1)] and "<WT" not in by3[(1, 1, 1)] and "<WG907><WTHN>" in by3[(40, 3, 1)])
check("mysword interlinear: no <CM>; the title blocks come first in verse 1",
      "<CM>" not in "".join(by3.values()) and by3[(1, 3, 1)].startswith("<Q>mizmor"))
run(mysword.main, *common, "--out", "o/pl.mybible", "--layout", "plain")
r4, _ = rows("o/pl.mybible", "Book=1 and Chapter=3 and Verse=1")
check("mysword plain: the title becomes <TS>gloss<Ts> in verse 1", r4[0][3].startswith("<TS>") and "<Ts>" in r4[0][3])
err = run(mysword.main, "--db", "nope.sqlite", "--language", "id", "--description", "T", "--abbreviation", "VAT", "--out", "o/x.mybible")
check("mysword: a missing database stops with a clear error and creates nothing", "missing or empty" in err and not os.path.exists("nope.sqlite"))
check("mysword: an abbreviation with a space is rejected",
      "must not contain spaces" in run(mysword.main, "--db", "db.sqlite", "--out", "o/y.mybible", "--description", "T", "--abbreviation", "A B"))

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)