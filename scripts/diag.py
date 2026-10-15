import collections, glob, re, sys
REF = re.compile(r"^([1-3]?[A-Za-z]{2,3})\.(\d+)\.(\d+)(?:\([^)]*\))?#(\d+)=(\S*)\t(.*)$")
LOOSE = re.compile(r"^([1-3]?[A-Za-z]{2,3})\.(\d+)\.(\d+)")
miss, ex = collections.Counter(), {}
for f in glob.glob(sys.argv[1] + "/TAHOT*.txt"):
    for line in open(f, encoding="utf-8"):
        m = LOOSE.match(line)
        if m and "\t" in line and not REF.match(line):
            k = m.groups()[:2]; miss[k] += 1; ex.setdefault(k, line[:80].rstrip())
print(f"{sum(miss.values())} unparsed lines in {len(miss)} chapters")
for (b, c), n in sorted(miss.items(), key=lambda x: (x[0][0], int(x[0][1]))): print(b, c, n, "e.g.", ex[(b, c)])
