"""SPIKE ONLY. Seed of the Grounded Answer Rate metric: verify quotes + citation tags."""
import json, re
from pathlib import Path
ev = json.loads(Path("results/evidence_set.json").read_text(encoding="utf-8"))
norm = lambda s: re.sub(r"\s+", " ", s.lower().replace("’", "'")).strip()
# corpus = chunk text PLUS metadata the model legitimately sees (titles, speakers)
C = norm(" ".join(e["text"] + " " + e["source_title"] + " " + e["speaker"] for e in ev))
VALID = {f"E{i}" for i in range(1, len(ev) + 1)}

bench = json.loads(Path("results/bench.json").read_text(encoding="utf-8"))
for model, tests in bench.items():
    print(f"\n=== {model} ===")
    for tag, r in tests.items():
        if "text" not in r:
            continue
        t = r["text"]
        quotes = re.findall(r'"([^"]{25,})"', t)
        fake = [q for q in quotes if norm(q).strip(". ") not in C]
        tags = set(re.findall(r"\[(E\d+)\]", t))
        bad = sorted(tags - VALID)
        verdict = "PASS" if not fake and not bad else "FAIL"
        print(f"  TEST {tag}: {verdict} | {len(quotes)} quotes, {len(fake)} fabricated | "
              f"tags={sorted(tags)} invalid={bad or 'none'}")
        for q in fake[:3]:
            print(f"      FABRICATED -> \"{q[:110]}...\"")
