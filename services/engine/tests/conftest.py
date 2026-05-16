"""Shared pytest fixtures for verbio-engine.

Two unrelated fixture families live here:

  - Process-level test helpers (`settings`, `client`) — no DB.
  - Postgres testcontainers fixtures for any test tree that needs a
    real database (currently `tests/persistence/`, `tests/agent/`).
    Those subtrees own the `pytestmark` that skips them when Docker
    isn't running, so this module stays import-safe on machines
    without Docker.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from verbio_engine.config import Settings
from verbio_engine.main import create_app
from verbio_engine.persistence import (
    create_engine,
    create_session_factory,
    dispose_engine,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession


# ---------------------------------------------------------------------------
# Process / app fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Postgres testcontainers fixtures (DB-touching tests only)
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    """Best-effort probe for a working local Docker daemon."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv, no shell.
            ["docker", "info"],  # noqa: S607 — full path checked above via shutil.
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


DOCKER_AVAILABLE = _docker_available()


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """Boot a Postgres 16 container for the test session."""
    # Import inside the fixture so module collection succeeds even when
    # testcontainers' optional Docker dep is missing locally.
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16-alpine")
    container.start()
    try:
        url = container.get_connection_url()
        # testcontainers returns `postgresql+psycopg2://...`; our engine
        # factory normalises any scheme, so we pass it through unchanged.
        yield url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def _migrated_url(postgres_url: str) -> str:
    """Apply every Alembic migration to the container before tests run."""
    # services/engine/tests/conftest.py -> parents[1] == services/engine
    alembic_cwd = Path(__file__).resolve().parents[1]
    env = {
        **__import__("os").environ,
        "DATABASE_URL_DIRECT": postgres_url,
    }
    subprocess.run(  # noqa: S603 — fixed argv.
        ["uv", "run", "alembic", "upgrade", "head"],  # noqa: S607
        check=True,
        cwd=alembic_cwd,
        env=env,
    )
    return postgres_url


@pytest.fixture
async def engine(_migrated_url: str) -> AsyncIterator[AsyncEngine]:
    """Async engine bound to the migrated test database."""
    eng = create_engine(_migrated_url, pool_size=2, max_overflow=0)
    try:
        yield eng
    finally:
        await dispose_engine(eng)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test AsyncSession wrapped in a transaction that rolls back."""
    factory = create_session_factory(engine)
    async with factory() as sess, sess.begin():
        try:
            yield sess
        finally:
            with contextlib.suppress(Exception):
                await sess.rollback()


@pytest.fixture
def session_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def participant_id() -> uuid.UUID:
    return uuid.uuid4()
