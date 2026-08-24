"""OD-2 embedding validation — Phase 2A follow-up.

Answers, by measurement rather than assertion:
  1. What is the ACTUAL embedding dimensionality? (Ollama's model page does not say.)
  2. What does loading an embedder do to GPU memory?
  3. Does the chat model stay resident when the embedder loads?
  4. What is query-time and ingestion-time embedding latency?

Read-only against the corpus; pulls nothing. stdlib only.
"""
from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
import http.client
import urllib.request
from pathlib import Path

HOST = "http://127.0.0.1:11434"  # NOT localhost: ::1 first-resolution costs ~2s on Windows
CHAT_MODEL = "qwen3:4b-instruct"
EMBED_MODELS = ["nomic-embed-text", "all-minilm"]
EVIDENCE = Path(__file__).parent / "evidence"

# Accepts BOTH timestamp formats — the casey-winters defect found in Phase 2A.
TURN = re.compile(
    r"^(?P<sp>[A-Z][A-Za-z.'\- ]+?) \((?P<ts>(?:\d{2}:)?\d{2}:\d{2})\):\s*$", re.M
)


_CONN: http.client.HTTPConnection | None = None


def _conn() -> http.client.HTTPConnection:
    """One reused connection. Per-request TCP setup costs ~2s via localhost
    and is pure overhead even on 127.0.0.1 -- the app must pool too."""
    global _CONN
    if _CONN is None:
        _CONN = http.client.HTTPConnection("127.0.0.1", 11434, timeout=900)
    return _CONN


def _req(method: str, path: str, payload: dict | None = None) -> dict:
    global _CONN
    body = json.dumps(payload) if payload is not None else None
    for attempt in (1, 2):
        try:
            c = _conn()
            c.request(method, path, body, {"Content-Type": "application/json"})
            return json.loads(c.getresponse().read())
        except (http.client.HTTPException, OSError):
            if _CONN:
                _CONN.close()
            _CONN = None
            if attempt == 2:
                raise
    raise RuntimeError("unreachable")


def _post(path: str, payload: dict, timeout: int = 600) -> dict:
    return _req("POST", path, payload)


def _get(path: str) -> dict:
    return _req("GET", path)


def embed(model: str, text: str) -> tuple[list[float], float]:
    t0 = time.perf_counter()
    out = _post("/api/embed", {"model": model, "input": text})
    dt = (time.perf_counter() - t0) * 1000
    return out["embeddings"][0], dt


def chat(model: str, prompt: str, num_predict: int = 24) -> float:
    t0 = time.perf_counter()
    _post(
        "/api/chat",
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": num_predict},
        },
    )
    return (time.perf_counter() - t0) * 1000


def loaded() -> list[tuple[str, float]]:
    """(model, VRAM GB) currently resident, per Ollama itself."""
    return [(m["name"], m.get("size_vram", 0) / 1e9) for m in _get("/api/ps")["models"]]


def gpu_free_mib() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    )
    return int(out.stdout.strip().splitlines()[0])


def unload(model: str) -> None:
    try:
        _post("/api/chat", {"model": model, "messages": [], "keep_alive": 0})
    except Exception:
        pass


def real_chunks(target_words: int = 300, limit: int = 30) -> list[str]:
    """Realistic ~400-token chunks packed from real speaker turns."""
    chunks: list[str] = []
    for f in sorted(EVIDENCE.glob("*.md")):
        txt = f.read_text(encoding="utf-8")
        marks = list(TURN.finditer(txt))
        turns = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(txt)
            body = txt[m.end():end].strip()
            if body:
                turns.append(f"{m.group('sp')} ({m.group('ts')}): {body}")
        buf, n = [], 0
        for t in turns:
            buf.append(t)
            n += len(t.split())
            if n >= target_words:
                chunks.append("\n".join(buf))
                buf, n = [], 0
        if buf:
            chunks.append("\n".join(buf))
    return chunks[:limit]


def banner(s: str) -> None:
    print(f"\n{'=' * 68}\n{s}\n{'=' * 68}")


def main() -> None:
    results: dict = {}

    banner("T0  Environment")
    print(f"  GPU free at rest       : {gpu_free_mib()} MiB")
    print(f"  Models resident        : {loaded() or 'none'}")

    # ---------------------------------------------------------------- T1 dim
    banner("T1  ACTUAL embedding dimensionality  (the schema-critical number)")
    probe = "How do you think about retention for a consumer subscription product?"
    dims = {}
    for m in EMBED_MODELS:
        vec, dt = embed(m, probe)
        dims[m] = len(vec)
        norm = sum(x * x for x in vec) ** 0.5
        print(f"  {m:20s} dim={len(vec):5d}  |v|={norm:6.3f}  first_call={dt:7.1f} ms")
    results["dimensions"] = dims

    # ------------------------------------------------------------ T2 latency
    banner("T2  Query-time latency  (one short question per user turn)")
    queries = [
        "How do I improve retention?",
        "What is a good activation metric?",
        "How should I price a B2B SaaS product?",
        "When should a startup hire a growth lead?",
        "What makes onboarding effective?",
    ] * 4
    qlat = {}
    for m in EMBED_MODELS:
        embed(m, "warmup")
        ts = [embed(m, q)[1] for q in queries]
        qlat[m] = {"p50": statistics.median(ts), "p95": sorted(ts)[int(0.95 * len(ts))],
                   "mean": statistics.mean(ts)}
        print(f"  {m:20s} p50={qlat[m]['p50']:6.1f} ms  p95={qlat[m]['p95']:6.1f} ms")
    results["query_latency_ms"] = qlat

    # ---------------------------------------------------------- T3 ingestion
    banner("T3  Ingestion throughput  (real ~400-token transcript chunks)")
    chunks = real_chunks()
    print(f"  built {len(chunks)} real chunks, "
          f"mean {statistics.mean(len(c.split()) for c in chunks):.0f} words")
    ing = {}
    for m in EMBED_MODELS:
        one = statistics.median([embed(m, c)[1] for c in chunks[:8]])
        t0 = time.perf_counter()
        out = _post("/api/embed", {"model": m, "input": chunks})
        bt = (time.perf_counter() - t0) * 1000
        per_b = bt / len(out["embeddings"])
        ing[m] = {"serial_ms_per_chunk": one, "batched_ms_per_chunk": per_b,
                  "batch_size": len(chunks),
                  "est_1935_serial_s": one * 1935 / 1000,
                  "est_1935_batched_s": per_b * 1935 / 1000}
        print(f"  {m:20s} serial {one:6.1f} ms/chunk | "
              f"batched({len(chunks)}) {per_b:6.1f} ms/chunk")
        print(f"  {'':20s}   -> 1,935 chunks: serial {one * 1935 / 1000:6.1f} s "
              f"| batched {per_b * 1935 / 1000:6.1f} s")
    results["ingestion"] = ing

    # ------------------------------------------------- T4 residency/eviction
    banner("T4  RESIDENCY — does the embedder evict the chat model?")
    residency = {}
    for m in EMBED_MODELS:
        print(f"\n  --- embedder: {m} ---")
        for x in EMBED_MODELS + [CHAT_MODEL]:
            unload(x)
        time.sleep(3)

        cold = chat(CHAT_MODEL, "Say OK.")
        print(f"  1. chat cold-load           : {cold:8.1f} ms")
        print(f"     resident: {loaded()}  GPU free {gpu_free_mib()} MiB")

        warm = chat(CHAT_MODEL, "Say OK again.")
        print(f"  2. chat warm (baseline)     : {warm:8.1f} ms")

        _, edt = embed(m, "How do I improve retention?")
        after = loaded()
        free_after = gpu_free_mib()
        print(f"  3. embed call               : {edt:8.1f} ms")
        print(f"     resident: {after}  GPU free {free_after} MiB")

        after2 = chat(CHAT_MODEL, "Say OK once more.")
        print(f"  4. chat AFTER embed         : {after2:8.1f} ms")

        chat_still = any(CHAT_MODEL in n for n, _ in after)
        both = chat_still and any(m in n for n, _ in after)
        penalty = after2 - warm
        evicted = after2 > warm * 3 and after2 > 2000

        print(f"     chat still resident      : {chat_still}")
        print(f"     BOTH co-resident         : {both}")
        print(f"     penalty vs warm baseline : {penalty:+8.1f} ms")
        print(f"     VERDICT                  : "
              f"{'EVICTED - reload cost per turn' if evicted else 'NO EVICTION'}")

        residency[m] = {
            "chat_cold_ms": cold, "chat_warm_ms": warm, "embed_ms": edt,
            "chat_after_ms": after2, "penalty_ms": penalty,
            "chat_still_resident": chat_still, "both_co_resident": both,
            "evicted": evicted, "gpu_free_after_mib": free_after,
            "resident_after": after,
        }
    results["residency"] = residency

    out = Path(__file__).parent / "results" / "embed_validation.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
