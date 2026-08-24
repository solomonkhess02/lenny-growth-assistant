"""SPIKE ONLY - throwaway. Finds practical num_ctx for qwen3:4b on 4GB VRAM."""
import json, subprocess, time, urllib.request

OLLAMA = "http://localhost:11434"
PS = r"D:\Ollama\ollama.exe"

def chat(model, msgs, num_ctx, think=False, num_predict=64):
    body = json.dumps({"model": model, "messages": msgs, "stream": False,
                       "think": think,
                       "options": {"num_ctx": num_ctx, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        d = json.loads(r.read())
    return d, time.time() - t0

def ps_line():
    out = subprocess.run([PS, "ps"], capture_output=True, text=True).stdout.strip().splitlines()
    return out[1] if len(out) > 1 else "(none)"

print(f"{'ctx':>7} {'cold_s':>8} {'warm_s':>8} {'tok/s':>7}  processor / size")
for ctx in (4096, 8192, 16384, 32768):
    try:
        _, cold = chat("qwen3:4b", [{"role": "user", "content": "Say OK."}], ctx)
        d, warm = chat("qwen3:4b", [{"role": "user", "content": "Count from 1 to 40."}], ctx, num_predict=128)
        ec = d.get("eval_count", 0); ed = d.get("eval_duration", 1) / 1e9
        tps = ec / ed if ed else 0
        info = ps_line()
        parts = info.split()
        proc = " ".join(parts[3:6]) if len(parts) > 6 else info
        print(f"{ctx:>7} {cold:>8.1f} {warm:>8.1f} {tps:>7.1f}  {proc}")
    except Exception as e:
        print(f"{ctx:>7}  FAILED: {type(e).__name__}: {e}")
