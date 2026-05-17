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

    # ----- LiveKit Cloud (Phase 1: room join + audio subscribe) -------------
    # Required at runtime when the agent worker starts. Optional at process
    # boot so the FastAPI health/admin server can still run in non-agent
    # roles (e.g., an admin-only deploy). The worker entrypoint asserts
    # presence before connecting.
    livekit_url: str | None = Field(default=None, alias="LIVEKIT_URL")
    livekit_api_key: SecretStr | None = Field(default=None, alias="LIVEKIT_API_KEY")
    livekit_api_secret: SecretStr | None = Field(default=None, alias="LIVEKIT_API_SECRET")
    # Identity the moderator agent uses when joining each room. Surfaces
    # in the dashboard's participant list; do not change without updating
    # web-side display filters (web hides participants with this identity
    # from the participant grid).
    moderator_identity: str = Field(default="verbio-moderator", alias="MODERATOR_IDENTITY")
    moderator_display_name: str = Field(default="Verbio", alias="MODERATOR_DISPLAY_NAME")

    # ----- Deepgram (Phase 1: per-track STT) --------------------------------
    # Required when the agent worker starts; the worker entrypoint asserts
    # presence so a missing key fails fast at boot, not on first audio
    # frame. Nova-3 is the default model; per-study overrides land in
    # Phase 3 when studies become first-class.
    deepgram_api_key: SecretStr | None = Field(default=None, alias="DEEPGRAM_API_KEY")
    deepgram_model: str = Field(default="nova-3", alias="DEEPGRAM_MODEL")
    deepgram_language: str = Field(default="en-US", alias="DEEPGRAM_LANGUAGE")

    # ----- OpenAI embeddings (Phase 3: topic_drift rule) --------------------
    # Optional at process boot so non-agent roles (admin server, schema
    # export, tests without networked deps) can still start. The session
    # bootstrapper asserts presence before the engine joins a room that
    # has topic_drift enabled. `text-embedding-3-small` is the cheapest
    # 1536-dim option and matches the brief's default.
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )
    openai_base_url: str | None = Field(
        default=None,
        alias="OPENAI_BASE_URL",
        description=(
            "Override only for tests or for a proxied OpenAI-compatible "
            "endpoint. DeepSeek (mouth layer, Phase 4) uses its own "
            "DEEPSEEK_BASE_URL, not this."
        ),
    )

    # ----- Cloudflare R2 (Phase 6: recording egress destination) ----------
    # Required when the recordings dispatcher runs — the engine never
    # uploads bytes itself, it hands these to LiveKit Cloud which writes
    # directly to R2. All four must be set together; when any is missing
    # the worker skips dispatcher construction and runs in
    # shadow-recording mode (decisions still persist, no audio file).
    # Mirrors the four vars consumed by the web service.
    r2_account_id: str | None = Field(default=None, alias="R2_ACCOUNT_ID")
    r2_access_key_id: SecretStr | None = Field(default=None, alias="R2_ACCESS_KEY_ID")
    r2_secret_access_key: SecretStr | None = Field(default=None, alias="R2_SECRET_ACCESS_KEY")
    r2_bucket: str | None = Field(default=None, alias="R2_BUCKET")

    # ----- Service name (for observability) --------------------------------
    service_name: str = Field(default="verbio-engine", alias="OTEL_SERVICE_NAME_ENGINE")

    # ----- Sentry (Phase 7: error reporting) -------------------------------
    # DSN absence is the disable switch — `configure_sentry()` becomes a
    # no-op, so dev/test environments don't need a real ingest URL.
    # `traces_sample_rate` is a float in [0, 1]; 0 disables performance traces
    # while error events still flow. Matches the web-side env contract.
    sentry_dsn: SecretStr | None = Field(default=None, alias="SENTRY_DSN")
    sentry_traces_sample_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        alias="SENTRY_TRACES_SAMPLE_RATE",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def load_settings() -> Settings:
    """Construct a fresh Settings instance.

    Wrapped in a function so tests can monkeypatch the environment before
    instantiation; avoids the module-import-time singleton pitfall.
    """
    return Settings()
