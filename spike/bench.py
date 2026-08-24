"""SPIKE ONLY - throwaway. Serial A/B/C benchmark, one model at a time, clean GPU."""
import json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_tests import build_evidence, fmt_evidence, SYSTEM, OUT

OLLAMA = "http://localhost:11434"

def chat(model, msgs, num_ctx, num_predict, system=SYSTEM):
    body = json.dumps({"model": model,
                       "messages": [{"role": "system", "content": system}] + msgs,
                       "stream": False,
                       "options": {"num_ctx": num_ctx, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=5400) as r:
        d = json.loads(r.read())
    el = time.time() - t0
    return (d["message"]["content"], el, d.get("eval_count", 0),
            d.get("prompt_eval_count", 0), d.get("prompt_eval_duration", 0)/1e9)

ev = build_evidence(); CTX = fmt_evidence(ev)
QA = "According to the evidence, what happens to startups versus incumbents when a new distribution platform emerges?"
QB = "According to the evidence, what were Figma's exact 2024 revenue figures and what pricing changes did they make?"
QC = ("Write a Ship 30 for 30-style essay of approximately 1250 words on what the evidence says about "
      "distribution and growth. Include: a strong hook; progression (hook, setup, tension, insight, "
      "explanation, application, takeaway); headings; bullets; selective bold; one specific takeaway. "
      "Cite evidence tags for every substantive claim. Never fabricate quotes or citations. Markdown only.")

out = {}
for model in sys.argv[1:]:
    print(f"\n{'='*70}\nMODEL: {model}\n{'='*70}", flush=True)
    out[model] = {}
    for tag, q, ctx, pred in (("A", QA, 8192, 1024), ("B", QB, 8192, 512), ("C", QC, 16384, 3072)):
        try:
            txt, el, ec, pc, pd = chat(model, [{"role": "user",
                        "content": f"EVIDENCE:\n{CTX}\n\n{'TASK' if tag=='C' else 'QUESTION'}: {q}"}], ctx, pred)
            wc = len(txt.split())
            print(f"\n--- TEST {tag} | {el:.0f}s total | prompt {pc}tok in {pd:.0f}s | "
                  f"out {ec}tok @ {ec/max(el-pd,0.1):.1f} tok/s | {wc} words ---", flush=True)
            print(txt[:900] + ("\n...[trunc]..." if len(txt) > 900 else ""), flush=True)
            out[model][tag] = {"elapsed": el, "out_tok": ec, "prompt_tok": pc,
                               "prompt_s": pd, "words": wc, "text": txt}
        except Exception as e:
            print(f"--- TEST {tag} FAILED: {type(e).__name__}: {e}", flush=True)
            out[model][tag] = {"error": f"{type(e).__name__}: {e}"}
    (OUT / "bench.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print("\nSaved -> results/bench.json")
