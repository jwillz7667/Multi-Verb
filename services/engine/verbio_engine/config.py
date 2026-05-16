"""Engine settings loaded from the environment, validated at boot.

Pydantic Settings reads `.env` in development and the live environment in
production. Missing required values raise at import time so the process
fails fast (brief §13 — secrets validated at boot).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["development", "staging", "production"]
LogLevel = Literal["debug", "info", "warning", "error"]


class Settings(BaseSettings):
    """Process-level configuration for verbio-engine."""

    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
        frozen=True,
    )

    # ----- Runtime ----------------------------------------------------------
    environment: Environment = Field(default="development", alias="SENTRY_ENVIRONMENT")
    log_level: LogLevel = Field(default="info", alias="LOG_LEVEL")
    tick_interval_ms: int = Field(default=500, ge=50, le=5000, alias="TICK_INTERVAL_MS")

    # ----- HTTP -------------------------------------------------------------
    port: int = Field(default=8000, ge=1, le=65535, alias="VERBIO_ENGINE_PORT")
    admin_token: SecretStr | None = Field(default=None, alias="VERBIO_ENGINE_ADMIN_TOKEN")

    # ----- Outbound endpoints (not consumed yet in Phase 0) -----------------
    database_url_engine: SecretStr | None = Field(default=None, alias="DATABASE_URL_ENGINE")
    redis_url: SecretStr | None = Field(default=None, alias="REDIS_URL")
    redis_namespace: str = Field(default="verbio", alias="REDIS_NAMESPACE")

    # ----- Service name (for observability) --------------------------------
    service_name: str = Field(default="verbio-engine", alias="OTEL_SERVICE_NAME_ENGINE")

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def load_settings() -> Settings:
    """Construct a fresh Settings instance.

    Wrapped in a function so tests can monkeypatch the environment before
    instantiation; avoids the module-import-time singleton pitfall.
    """
    return Settings()
