"""CLI entry point:  python -m app.ingest [--force] [--slug S] [--limit N]

Explicit, never automatic on boot. Ingestion takes ~45s and requires Ollama;
running it at container startup would make `docker compose up` slow and
unpredictable, and would couple API liveness to the embedding model.
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from ..db import dispose
from ..errors import AppError
from ..logging_conf import configure_logging
from ..providers import close_http_client
from .pipeline import ingest_all


async def _run(args: argparse.Namespace) -> int:
    report = await ingest_all(force=args.force, limit=args.limit,
                              only=args.slug)

    print(f"\n{'slug':24s} {'status':10s} {'chunks':>7s} {'turns':>6s} {'ms':>8s}")
    print("-" * 60)
    for r in report.results:
        print(f"{r.slug:24s} {r.status:10s} {r.chunks:7d} {r.turns:6d} "
              f"{r.duration_ms:8.0f}")
        if r.error:
            print(f"    !! {r.error}")

    print(f"\ningested={len(report.ingested)} skipped={len(report.skipped)} "
          f"failed={len(report.failed)} total_chunks={report.total_chunks}")

    if report.failed:
        # Loud, and a non-zero exit. A corpus that is quietly incomplete
        # produces confident answers from material nobody knows is missing.
        print(f"\nFAILED: {len(report.failed)} transcript(s) did not ingest:",
              file=sys.stderr)
        for r in report.failed:
            print(f"  {r.slug}: {r.error}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="python -m app.ingest",
        description="Ingest the pinned Lenny's Podcast transcript corpus.")
    p.add_argument("--force", action="store_true",
                   help="re-ingest even when the content hash is unchanged")
    p.add_argument("--slug", help="ingest a single episode by slug")
    p.add_argument("--limit", type=int, help="ingest only the first N episodes")
    args = p.parse_args()

    configure_logging()

    async def wrapper() -> int:
        try:
            return await _run(args)
        except AppError as exc:
            # An operator typo deserves a sentence, not a stack trace. Real
            # bugs still propagate with their traceback intact.
            print(f"\nerror: {exc.message}", file=sys.stderr)
            return 2
        finally:
            await close_http_client()
            await dispose()

    return asyncio.run(wrapper())


if __name__ == "__main__":
    raise SystemExit(main())
