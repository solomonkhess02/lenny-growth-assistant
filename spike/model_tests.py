"""SPIKE ONLY - throwaway validation code. NOT the application.
Tests A/B/C for local model feasibility (Phase 1)."""
import json, re, sys, time, urllib.request
from pathlib import Path

OLLAMA = "http://localhost:11434"
import os
MODEL = os.environ.get("SPIKE_MODEL", "qwen3:4b-instruct")
EV = Path(__file__).parent / "evidence"
OUT = Path(__file__).parent / "results"
OUT.mkdir(exist_ok=True)

TURN = re.compile(r"^(?P<sp>[A-Z][A-Za-z.'\- ]+?) \((?P<ts>\d{2}:\d{2}:\d{2})\):\s*$")

def parse(path):
    raw = path.read_text(encoding="utf-8", errors="replace")
    parts = raw.split("---", 2)
    fm = {}
    for line in parts[1].splitlines():
        m = re.match(r"^(\w+):\s*(.*)$", line)
        if m and m.group(2).strip():
            fm[m.group(1)] = m.group(2).strip().strip("'\"")
    turns, sp, ts, buf = [], None, None, []
    for line in parts[2].splitlines():
        m = TURN.match(line.strip())
        if m:
            if sp and buf:
                turns.append({"speaker": sp, "ts": ts, "text": " ".join(buf).strip()})
            sp, ts, buf = m.group("sp"), m.group("ts"), []
        elif line.strip():
            buf.append(line.strip())
    if sp and buf:
        turns.append({"speaker": sp, "ts": ts, "text": " ".join(buf).strip()})
    return fm, turns

def secs(ts):
    h, m, s = (int(x) for x in ts.split(":"))
    return h * 3600 + m * 60 + s

def pick(fm, turns, keywords, n=3, minw=90):
    """Deterministic keyword selection - NOT a retrieval pipeline, just fixed test evidence."""
    hits = []
    for t in turns:
        if t["speaker"].lower().startswith("lenny"):
            continue
        w = len(t["text"].split())
        if w < minw:
            continue
        score = sum(t["text"].lower().count(k) for k in keywords)
        if score:
            hits.append((score, w, t))
    hits.sort(key=lambda x: -x[0])
    out = []
    for _, _, t in hits[:n]:
        out.append({
            "source_id": fm.get("video_id", "?"),
            "source_title": fm.get("title", "?"),
            "speaker": t["speaker"],
            "source_url": f"{fm.get('youtube_url','')}&t={secs(t['ts'])}s",
            "publish_date": fm.get("publish_date", "?"),
            "timestamp": t["ts"],
            "text": t["text"],
        })
    return out

def build_evidence():
    ev = []
    spec = [("brian-balfour.md", ["distribution", "platform", "channel"]),
            ("elena-verna.md", ["growth", "retention", "loop"]),
            ("casey-winters.md", ["retention", "growth", "marketplace"])]
    for fn, kw in spec:
        fm, turns = parse(EV / fn)
        ev += pick(fm, turns, kw, n=2)
    return ev

def fmt_evidence(ev):
    out = []
    for i, e in enumerate(ev, 1):
        out.append(f"[E{i}] {e['speaker']} — \"{e['source_title']}\" ({e['publish_date']}, {e['timestamp']})\n{e['text']}")
    return "\n\n".join(out)

SYSTEM = """You answer questions about product and growth using ONLY the transcript evidence provided.

Rules:
- Use only the numbered evidence blocks. Do not use outside knowledge.
- Cite every substantive claim with its tag, e.g. [E1], [E3].
- Never invent a quote, a speaker, or a citation tag that was not provided.
- If the evidence does not adequately support an answer, say so plainly and stop. Do not guess.
- Distinguish what the evidence states from your own inference."""

def chat(msgs, num_ctx=8192, num_predict=2048, think=False, system=SYSTEM):
    body = json.dumps({"model": MODEL,
                       "messages": [{"role": "system", "content": system}] + msgs,
                       "stream": False, "think": think,
                       "options": {"num_ctx": num_ctx, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as r:
        d = json.loads(r.read())
    el = time.time() - t0
    return d["message"]["content"], el, d.get("eval_count", 0), d.get("prompt_eval_count", 0)

if __name__ == "__main__":
    ev = build_evidence()
    ctx = fmt_evidence(ev)
    print(f"Evidence: {len(ev)} chunks, {len(ctx.split())} words, "
          f"sources: {sorted(set(e['source_id'] for e in ev))}\n")
    (OUT / "evidence_set.json").write_text(json.dumps(ev, indent=2), encoding="utf-8")

    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    results = {}

    if which in ("all", "A"):
        q = "What makes a new distribution platform worth betting on, and how should a startup approach it differently from a late-stage company?"
        txt, el, ec, pc = chat([{"role": "user", "content": f"EVIDENCE:\n{ctx}\n\nQUESTION: {q}"}])
        print(f"=== TEST A ({el:.1f}s, {pc} prompt tok, {ec} out tok, {ec/el:.1f} tok/s) ===\n{txt}\n")
        results["A"] = {"elapsed": el, "out_tok": ec, "prompt_tok": pc, "text": txt}

    if which in ("all", "B"):
        q = "According to the evidence, what were Figma's exact 2024 revenue figures and what pricing changes did they make?"
        txt, el, ec, pc = chat([{"role": "user", "content": f"EVIDENCE:\n{ctx}\n\nQUESTION: {q}"}])
        print(f"=== TEST B ({el:.1f}s, {ec} out tok) ===\n{txt}\n")
        results["B"] = {"elapsed": el, "out_tok": ec, "text": txt}

    if which in ("all", "C"):
        q = ("Write a Ship 30 for 30-style essay of approximately 1250 words on what the evidence says "
             "about distribution and growth. Requirements: a strong hook; clear narrative progression "
             "(hook, setup, tension, insight, explanation, practical application, takeaway); headings; "
             "bullets; selective bold; one specific actionable takeaway. Every substantive claim must "
             "cite its evidence tag. Do not fabricate quotes or citations. Output Markdown.")
        txt, el, ec, pc = chat([{"role": "user", "content": f"EVIDENCE:\n{ctx}\n\nTASK: {q}"}],
                               num_ctx=16384, num_predict=4096)
        wc = len(txt.split())
        print(f"=== TEST C ({el:.1f}s, {ec} out tok, {wc} words, {ec/el:.1f} tok/s) ===")
        print(txt[:1500] + "\n...[truncated]...\n")
        results["C"] = {"elapsed": el, "out_tok": ec, "words": wc, "text": txt}

    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved -> {OUT / 'results.json'}")
