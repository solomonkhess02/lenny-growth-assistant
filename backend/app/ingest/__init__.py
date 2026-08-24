"""Transcript ingestion: fetch -> parse -> chunk -> embed -> store.

Run it explicitly:  python -m app.ingest [--force] [--slug S] [--limit N]

Deliberately NOT automatic on boot. Ingestion takes ~45s and needs Ollama;
wiring it into container startup would make `docker compose up` slow and
unpredictable, and would couple the API's liveness to the embedding model.
"""
from .pipeline import IngestReport, IngestResult, ingest_all, ingest_one

__all__ = ["IngestReport", "IngestResult", "ingest_all", "ingest_one"]
