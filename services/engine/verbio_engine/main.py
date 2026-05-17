"""FastAPI entry point for verbio-engine.

Phase 0: exposes `/health` and `/ready` for orchestrator probes. The 2 Hz
tick loop and LiveKit agent join arrive in Phase 1+.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from pydantic import BaseModel, Field

from verbio_engine import __version__
from verbio_engine.config import Settings, load_settings
from verbio_engine.logging import configure_logging, get_logger
from verbio_engine.sentry import configure_sentry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class HealthResponse(BaseModel):
    """Liveness + version metadata for orchestrators."""

    status: Literal["ok"] = "ok"
    service: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    """Readiness — Phase 0 has no downstream deps to probe yet."""

    status: Literal["ready"] = "ready"
    checks: dict[str, Literal["pass", "skip"]] = Field(default_factory=dict)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a FastAPI app. Factory form keeps tests and uvicorn aligned."""
    settings = settings or load_settings()
    configure_logging(level=settings.log_level, json_format=settings.is_production)
    sentry_enabled = configure_sentry(settings)
    log = get_logger(__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        log.info(
            "engine.startup",
            version=__version__,
            environment=settings.environment,
            tick_interval_ms=settings.tick_interval_ms,
            sentry=sentry_enabled,
        )
        yield
        log.info("engine.shutdown")

    app = FastAPI(
        title="verbio-engine",
        version=__version__,
        description="Real-time AI moderator engine for multi-participant research sessions.",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(
            service=settings.service_name,
            version=__version__,
            environment=settings.environment,
        )

    @app.get("/ready", response_model=ReadinessResponse, tags=["meta"])
    async def ready() -> ReadinessResponse:
        # Phase 0 has no downstream dependencies. Future phases probe
        # Postgres, Redis, and LiveKit and surface per-check status here.
        return ReadinessResponse(checks={"postgres": "skip", "redis": "skip", "livekit": "skip"})

    return app


# Module-level app for `uvicorn verbio_engine.main:app`.
app = create_app()


def run() -> None:
    """CLI entrypoint registered as `verbio-engine` in pyproject.toml."""
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "verbio_engine.main:app",
        host="0.0.0.0",  # noqa: S104 — container exposure intended; firewalled at infra.
        port=settings.port,
        log_level=settings.log_level,
        reload=settings.environment == "development",
    )
