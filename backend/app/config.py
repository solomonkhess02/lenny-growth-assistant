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
            "log_level": self.log_level,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
