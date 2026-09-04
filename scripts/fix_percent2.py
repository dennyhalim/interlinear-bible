import re

path = "scripts/generate_ai_glosses.py"
text = open(path, encoding="utf-8").read()

fixed = re.sub(r'(help="[^"]*?)%([^%])', r"\1%%\2", text)

if fixed == text:
    print("No changes made -- either already fixed, or the pattern didn't match.")
else:
    open(path, "w", encoding="utf-8").write(fixed)
    print("Done - file updated.")