"""SPIKE ONLY. Measures Pi's prompt-token budget under controlled variations.

Ground truth is `usage.input` from the model response, surfaced by Pi's
--mode json events. Component costs are obtained DIFFERENTIALLY (config A minus
config B) and are labelled as such -- nothing here is a token estimate.
"""
import json, subprocess, sys, time
from pathlib import Path

PI = r"C:\Users\solom\AppData\Roaming\npm\pi.cmd"
MODEL = ["--provider", "ollama", "--model", "qwen3:4b-instruct"]
TINY = "Say BANANA."


def run(name, args, prompt=TINY, timeout=300):
    cmd = [PI, "-p", "--mode", "json"] + MODEL + args + [prompt]
    t0 = time.perf_counter()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return {"name": name, "error": "timeout", "seconds": timeout}
    dt = time.perf_counter() - t0

    usage, text, err = None, "", None
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("type") == "turn_end":
            m = ev.get("message", {})
            usage = m.get("usage")
            text = "".join(c.get("text", "") for c in m.get("content", [])
                           if c.get("type") == "text")
        if ev.get("type") == "error":
            err = str(ev)[:300]
    if usage is None:
        err = err or (p.stderr or p.stdout)[-300:]
    return {"name": name, "args": " ".join(args), "seconds": round(dt, 1),
            "input_tokens": (usage or {}).get("input"),
            "output_tokens": (usage or {}).get("output"),
            "answer": text[:120], "error": err}


if __name__ == "__main__":
    evidence = Path("evidence_block.txt").read_text(encoding="utf-8")
    sysprompt = Path("system_prompt.txt").read_text(encoding="utf-8")

    cases = [
        ("A_floor_notools_minsys",
         ["--no-tools", "--no-prompt-templates", "--system-prompt", "Answer briefly."]),
        ("B_notools_minsys_templates_on",
         ["--no-tools", "--system-prompt", "Answer briefly."]),
        ("C_notools_default_sysprompt",
         ["--no-tools", "--no-prompt-templates"]),
        ("D_builtin_tools_default_sysprompt",
         ["--no-prompt-templates"]),
        ("E_tools_restricted_read_only",
         ["--no-prompt-templates", "--tools", "read"]),
        ("F_rag_realistic_notools",
         ["--no-tools", "--no-prompt-templates", "--system-prompt", sysprompt]),
    ]
    out = []
    for name, args in cases:
        prompt = (evidence if name.startswith("F") else TINY)
        r = run(name, args, prompt=prompt)
        out.append(r)
        print(f"{name:36s} input={str(r['input_tokens']):>6s} "
              f"out={str(r['output_tokens']):>4s} {r['seconds']:6.1f}s "
              f"{'ERR:'+str(r['error'])[:60] if r.get('error') else r['answer'][:40]!r}",
              flush=True)
    Path("results_overhead.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
