import json, re, sqlite3
c = sqlite3.connect("output/interlinear.sqlite")
ids = []
for wid, sp in c.execute("SELECT word_id, strongs_parts FROM word WHERE strongs LIKE 'H0853%'").fetchall():
    parts = json.loads(sp) if sp else []
    if parts and re.fullmatch(r"H0*853[A-Za-z]?", parts[-1]):
        ids.append(wid)
c.executemany("UPDATE ai_gloss SET gloss = ? WHERE word_id = ?", [("(tanda objek)", i) for i in ids])
c.commit()
print(f"updated {c.total_changes} ai_gloss rows ({len(ids)} plain H853 words)")
