import collections, json
rows = collections.defaultdict(list)
for line in open("output/staging/tahot.jsonl", encoding="utf-8"):
    r = json.loads(line)
    rows[(r["book_code"], r["chapter"], r["verse"], r["position"])].append(r)
dups = {k: v for k, v in rows.items() if len(v) > 1}
print(len(dups), "duplicate keys")
for k, v in list(dups.items())[:20]:
    print(k, [(r["source_flag"], r["translit"], r["gloss"][:15]) for r in v])
