"""SPIKE ONLY. Same A/B/C prompts as bench.py, against DeepSeek's Anthropic endpoint."""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_tests import build_evidence, fmt_evidence, SYSTEM, OUT

KEY = os.environ.get("DEEPSEEK_API_KEY", "")
assert KEY, "DEEPSEEK_API_KEY not set"
URL = "https://api.deepseek.com/anthropic/v1/messages"

def ask(prompt, max_tokens=4096, model="deepseek-v4-pro"):
    body = json.dumps({"model": model, "max_tokens": max_tokens,
                       "system": SYSTEM,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "content-type": "application/json", "anthropic-version": "2023-06-01",
        "x-api-key": KEY})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=900) as r:
        d = json.loads(r.read())
    el = time.time() - t0
    txt = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    return txt, el, d.get("usage", {})

ev = build_evidence(); CTX = fmt_evidence(ev)
QA = "According to the evidence, what happens to startups versus incumbents when a new distribution platform emerges?"
QB = "According to the evidence, what were Figma's exact 2024 revenue figures and what pricing changes did they make?"
QC = ("Write a Ship 30 for 30-style essay of approximately 1250 words on what the evidence says about "
      "distribution and growth. Include: a strong hook; progression (hook, setup, tension, insight, "
      "explanation, application, takeaway); headings; bullets; selective bold; one specific takeaway. "
      "Cite evidence tags for every substantive claim. Never fabricate quotes or citations. Markdown only.")

out = {}
for tag, q, mt in (("A", QA, 1024), ("B", QB, 512), ("C", QC, 4096)):
    txt, el, us = ask(f"EVIDENCE:\n{CTX}\n\n{'TASK' if tag=='C' else 'QUESTION'}: {q}", mt)
    wc = len(txt.split())
    print(f"--- TEST {tag} | {el:.1f}s | in {us.get('input_tokens')} out {us.get('output_tokens')} tok | {wc} words ---")
    print(txt[:600] + ("\n...[trunc]..." if len(txt) > 600 else ""))
    print()
    out[tag] = {"elapsed": el, "usage": us, "words": wc, "text": txt}

p = OUT / "deepseek_bench.json"
p.write_text(json.dumps({"deepseek-v4-pro": out}, indent=2), encoding="utf-8")
print(f"Saved -> {p}")
