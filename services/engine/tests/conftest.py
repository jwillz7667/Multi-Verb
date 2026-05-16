"""Shared pytest fixtures for verbio-engine."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from verbio_engine.config import Settings
from verbio_engine.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings for tests — no .env, no surprises."""
    return Settings(
        SENTRY_ENVIRONMENT="development",
        LOG_LEVEL="warning",
        TICK_INTERVAL_MS=500,
        VERBIO_ENGINE_PORT=8000,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    """FastAPI test client wired to a fresh app instance."""
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
