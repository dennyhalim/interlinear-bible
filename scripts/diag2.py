import json, collections
c = collections.Counter()
for l in open("output/staging/tahot.jsonl", encoding="utf-8"):
    r = json.loads(l); c[(r["book_code"], r["chapter"], r["verse"], r["position"])] += 1
dups = {k: n for k, n in c.items() if n > 1}
print(len(dups), "duplicate keys,", sum(n - 1 for n in dups.values()), "extra rows"); print(list(dups)[:10])
