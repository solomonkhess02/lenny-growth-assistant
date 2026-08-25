"""Configuration loading — the single place environment is read.

Nothing else in the app may call os.environ. Provider choice is configuration,
never a hardcoded branch in business logic (skill 04).
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["ollama", "deepseek"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- provider selection -------------------------------------------------
    llm_provider: Provider = "ollama"

    # --- Ollama (local, mandated demo path) ---------------------------------
    # 127.0.0.1 not localhost: localhost resolves ::1 first, Ollama binds IPv4
    # only, and every new connection stalls ~2s. Measured 2026-08-25.
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_context_length: int = 8192

    # --- DeepSeek (cloud) ---------------------------------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/anthropic"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_disable_thinking: bool = True
    deepseek_max_tokens: int = 16384

    # --- embeddings (locked Phase 2A/OD-2) ----------------------------------
    embedding_model: str = "all-minilm"
    embedding_dim: int = 384

    # --- agent framework: Pi Coding Agent (Phase 4 adoption) ----------------
    # §3.1 agent layer. Chosen over the Claude Agent SDK by measurement:
    # 111 tokens of harness overhead vs 24,472 against a locked 8,192 context.
    pi_cli_path: str = "pi"

    # Pi discovers and injects project context files (CLAUDE.md, AGENTS.md)
    # from its working directory. Measured: running from the repo root added
    # 1,311 tokens to EVERY request. Empty string -> a dedicated directory
    # under the system temp root, which is never the repository.
    pi_working_dir: str = ""

    # Phase 8: generation has no wall-clock bound without these. IDLE fires
    # when the provider goes quiet mid-turn (a hung Ollama, a stalled cloud
    # connection) and is the primary mechanism; TOTAL is a backstop for a
    # provider that keeps emitting slowly forever. 120s idle is well above
    # Phase 1's measured 31s time-to-first-token on the local demo path; 900s
    # total is well above the measured 254.7s local essay
    # (verification-matrix.md M16) with headroom for a slower machine.
    generation_idle_timeout_s: int = 120
    generation_timeout_s: int = 900

    # --- retrieval (Phase 3) -------------------------------------------------
    # Number of chunks fed to the model. ~3 is a UX decision, not a guess:
    # Phase 1 measured 30 of a 48s local answer as prompt processing at
    # ~118 tok/s prefill, so evidence size directly sets perceived latency.
    retrieval_k: int = 3

    # Cosine-similarity floor. Below it, retrieval returns NOTHING rather than
    # its least-bad guess. Set by pre-registered calibration -- see
    # docs/retrieval-calibration.md for the frozen question set, the two score
    # distributions and the confusion matrix at this value.
    retrieval_min_similarity: float = 0.40

    # Max chunks from any single episode, so k=3 cannot collapse onto one
    # source. None disables the cap.
    retrieval_max_per_source: int | None = 2

    # --- Ship 30 essays (Phase 6) -------------------------------------------
    # Evidence for an essay: the source answer's chunks are always pinned
    # first, then topped up from the same query to this total. Larger than
    # retrieval_k because 1,250 words from 2-3 chunks is thin, and a model that
    # runs out of material is a model under pressure to invent some. The
    # similarity floor and per-source cap are UNCHANGED, so the pre-registered
    # calibration still holds -- see docs/retrieval-calibration.md.
    essay_retrieval_k: int = 6

    # Per-source cap for the essay top-up only. RETRIEVAL_MAX_PER_SOURCE=2 is a
    # DIVERSITY guarantee for short answers -- "so k=3 cannot collapse onto one
    # source". Measured against the corpus, it is also the only thing limiting
    # an episode-specific question:
    #
    #   "How does Duolingo use streaks...?"   cap=2 -> 2 chunks   cap=4 -> 4 chunks
    #   "What makes a growth team effective?" cap=2 -> 6 chunks from 5 episodes
    #
    # So the cap binds exactly where an essay needs more material, and the
    # chunks it was withholding score 0.66-0.67 -- well above the 0.40 floor,
    # not barrel-scraping. An essay about one episode's topic may legitimately
    # draw more deeply from that episode. 4 rather than None: unbounded would
    # surrender diversity even where diversity is available.
    #
    # The floor and the search itself are untouched, so the pre-registered
    # calibration is unaffected -- this is an existing retrieve() parameter,
    # given a different value for a different task.
    essay_max_per_source: int = 4

    # The Ship 30 target. Measured, reported, and never enforced by truncation:
    # cutting an essay to length would sever quotes and citation tags and could
    # turn verified prose into a fabrication.
    essay_target_words: int = 1250
    essay_word_tolerance: float = 0.20      # 1,000-1,500 counts as on target

    # Override the Ship 30 skill file location. Empty -> app/skills/ (the
    # runtime image) then .claude/skills/ (the host dev loop). See app/ship30.py.
    ship30_skill_path: str = ""

    # --- database -----------------------------------------------------------
    database_url: str = "postgresql://lenny:CHANGE_ME@127.0.0.1:5432/lenny"

    # --- app ----------------------------------------------------------------
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, v: str) -> str:
        """Accept the plain URL humans write; drive it with asyncpg."""
        if v.startswith("postgresql+"):
            return v
        if v.startswith("postgresql://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        return v

    @property
    def sync_database_url(self) -> str:
        """Alembic runs sync; strip the async driver back off."""
        return self.database_url.replace("+asyncpg", "")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def redacted(self) -> dict:
        """Safe to log or return over HTTP. Never exposes the key itself."""
        return {
            "llm_provider": self.llm_provider,
            "ollama_base_url": self.ollama_base_url,
            "ollama_model": self.ollama_model,
            "ollama_context_length": self.ollama_context_length,
            "deepseek_base_url": self.deepseek_base_url,
            "deepseek_model": self.deepseek_model,
            "deepseek_api_key_present": bool(self.deepseek_api_key),
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "retrieval_k": self.retrieval_k,
            "retrieval_min_similarity": self.retrieval_min_similarity,
            "retrieval_max_per_source": self.retrieval_max_per_source,
            "essay_retrieval_k": self.essay_retrieval_k,
            "essay_max_per_source": self.essay_max_per_source,
            "essay_target_words": self.essay_target_words,
            "agent_framework": "pi",
            "pi_cli_path": self.pi_cli_path,
            "pi_working_dir": self.pi_working_dir or "(system temp)",
            "generation_idle_timeout_s": self.generation_idle_timeout_s,
            "generation_timeout_s": self.generation_timeout_s,
            "log_level": self.log_level,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
