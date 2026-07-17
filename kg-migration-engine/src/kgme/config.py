"""Typed application configuration, loaded from environment / .env."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Fails fast if required values are missing."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    falkordb_host: str = Field(default="localhost")
    falkordb_port: int = Field(default=6379)
    falkordb_username: str | None = Field(default=None)
    falkordb_password: str = Field(...)
    falkordb_graph: str = Field(default="kgme")

    anthropic_api_key: str = Field(...)
    anthropic_model: str = Field(default="claude-sonnet-5")


def load_settings() -> Settings:
    """Load and validate settings."""
    return Settings()  # type: ignore[call-arg]  # values supplied via env/.env
