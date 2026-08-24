"""Score the frozen calibration set and choose the similarity floor.

Run:  python -m tests.eval.run_calibration    (from backend/)

Reads `calibration_set.json` -- which was committed BEFORE this script ever
ran -- scores every question, and reports the two distributions plus the
confusion matrix at each candidate threshold. It does not edit the question
set, and nothing here should ever be changed to make a result look better.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.db import dispose, session_factory  # noqa: E402
from app.embeddings import EmbeddingClient  # noqa: E402
from app.providers import close_http_client  # noqa: E402
from app.retrieval import retrieve  # noqa: E402

SET_PATH = Path(__file__).parent / "calibration_set.json"


async def score_all(cap: int | None = None) -> list[dict]:
    data = json.loads(SET_PATH.read_text(encoding="utf-8"))
    embedder = EmbeddingClient()
    rows: list[dict] = []
    async with session_factory()() as db:
        for q in data["questions"]:
            ev = await retrieve(db, q["question"], k=3, min_similarity=-1.0,
                                max_per_source=cap, embedder=embedder)
            top = ev[0] if ev else None
            rows.append({
                "id": q["id"],
                "label": q["label"],
                "kind": q.get("kind", "supported"),
                "question": q["question"],
                "expected": q["expected_source_id"],
                "top_similarity": top.similarity if top else None,
                "top_source": top.source_id if top else None,
                "sources": [e.source_id for e in ev],
                "hit": bool(top and q["expected_source_id"]
                            and q["expected_source_id"] in
                            [e.source_id for e in ev]),
                "top1_hit": bool(top and top.source_id == q["expected_source_id"]),
            })
    return rows


def confusion(rows: list[dict], threshold: float) -> dict:
    tp = sum(1 for r in rows if r["label"] == "supported"
             and r["top_similarity"] is not None
             and r["top_similarity"] >= threshold)
    fn = sum(1 for r in rows if r["label"] == "supported"
             and not (r["top_similarity"] is not None
                      and r["top_similarity"] >= threshold))
    fp = sum(1 for r in rows if r["label"] == "unsupported"
             and r["top_similarity"] is not None
             and r["top_similarity"] >= threshold)
    tn = sum(1 for r in rows if r["label"] == "unsupported"
             and not (r["top_similarity"] is not None
                      and r["top_similarity"] >= threshold))
    return {"threshold": round(threshold, 4), "tp": tp, "fn": fn,
            "fp": fp, "tn": tn,
            "accuracy": round((tp + tn) / len(rows), 4)}


async def main() -> None:
    cap_arg = None
    if "--cap" in sys.argv:
        raw = sys.argv[sys.argv.index("--cap") + 1]
        cap_arg = None if raw == "none" else int(raw)

    rows = await score_all(cap=cap_arg)

    sup = sorted(r["top_similarity"] for r in rows if r["label"] == "supported")
    near = sorted(r["top_similarity"] for r in rows if r["kind"] == "near_miss")
    off = sorted(r["top_similarity"] for r in rows if r["kind"] == "off_domain")

    print(f"\n{'id':5s} {'label':12s} {'kind':11s} {'top_sim':>8s} "
          f"{'top_source':22s} {'expected':22s} hit")
    print("-" * 96)
    for r in rows:
        mark = "OK" if (r["label"] == "unsupported" or r["hit"]) else "MISS"
        print(f"{r['id']:5s} {r['label']:12s} {r['kind']:11s} "
              f"{r['top_similarity']:8.4f} {str(r['top_source'])[:21]:22s} "
              f"{str(r['expected'])[:21]:22s} {mark}")

    def stats(name, xs):
        if not xs:
            return
        print(f"  {name:12s} n={len(xs):2d} min={min(xs):.4f} "
              f"p50={xs[len(xs)//2]:.4f} max={max(xs):.4f}")

    print("\n=== score distributions (top-1 similarity) ===")
    stats("supported", sup)
    stats("near_miss", near)
    stats("off_domain", off)

    unsup = sorted(near + off)
    print(f"\nlowest supported : {min(sup):.4f}")
    print(f"highest unsupported: {max(unsup):.4f}")
    margin = min(sup) - max(unsup)
    print(f"SEPARATION MARGIN : {margin:+.4f} "
          f"({'clean separation' if margin > 0 else 'POPULATIONS OVERLAP'})")

    print("\n=== confusion matrix by threshold ===")
    print(f"{'thr':>7s} {'TP':>3s} {'FN':>3s} {'FP':>3s} {'TN':>3s} {'acc':>7s}")
    best = None
    for i in range(0, 61):
        thr = i / 100
        c = confusion(rows, thr)
        if i % 5 == 0 or (best and c["accuracy"] > best["accuracy"]):
            print(f"{c['threshold']:7.2f} {c['tp']:3d} {c['fn']:3d} "
                  f"{c['fp']:3d} {c['tn']:3d} {c['accuracy']:7.2%}")
        if best is None or c["accuracy"] > best["accuracy"]:
            best = c

    print(f"\nBEST accuracy {best['accuracy']:.2%} at threshold {best['threshold']}")
    if margin > 0:
        mid = round((min(sup) + max(unsup)) / 2, 4)
        print(f"MIDPOINT of the gap = {mid}  <- maximises margin on both sides")

    print("\n=== attribution accuracy (supported only) ===")
    s_rows = [r for r in rows if r["label"] == "supported"]
    print(f"expected episode in top-3: {sum(r['hit'] for r in s_rows)}/{len(s_rows)}")
    print(f"expected episode at top-1: {sum(r['top1_hit'] for r in s_rows)}/{len(s_rows)}")

    out = Path(__file__).parent / f"calibration_results_cap-{cap_arg}.json"
    out.write_text(json.dumps({"cap": cap_arg, "rows": rows}, indent=2),
                   encoding="utf-8")
    print(f"\nraw -> {out.name}")

    await close_http_client()
    await dispose()


if __name__ == "__main__":
    asyncio.run(main())
