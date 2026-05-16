"""Async SQLAlchemy engine + session factory.

Process-level lifecycle:
  - `create_engine(url, pool_size=...)` returns an `AsyncEngine`.
  - `create_session_factory(engine)` returns an `async_sessionmaker`.
  - `dispose_engine(engine)` is called on shutdown so asyncpg can close
    its underlying connections cleanly.

URL normalisation: callers pass any of the standard Postgres URL forms
(`postgres://`, `postgresql://`, `postgresql+psycopg2://`,
`postgresql+asyncpg://`) and we coerce to `postgresql+asyncpg://`. This
mirrors `infra/postgres/migrations/env.py` which normalises in the
opposite direction for the sync Alembic driver.

Pooling notes:
  - When the engine talks to pgBouncer (transaction mode, port 6543),
    we MUST disable client-side prepared statements via
    `connect_args={"statement_cache_size": 0}` — pgBouncer rewrites
    statement names and asyncpg's cache breaks on the second prepare.
  - When talking directly to Postgres (port 5432), the default cache
    is fine and gives a meaningful perf win.
  - Callers indicate intent via `pgbouncer=True`. Defaults to False so
    local dev (direct connection) gets full performance.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def _to_asyncpg_url(url: str) -> str:
    """Normalise any standard Postgres URL form to the asyncpg driver."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+asyncpg://" + url[len("postgresql+psycopg2://") :]
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    msg = (
        f"Unrecognised Postgres URL scheme: {url!r}. Expected one of "
        "postgresql://, postgres://, postgresql+psycopg2://, postgresql+asyncpg://."
    )
    raise ValueError(msg)


def create_engine(
    url: str,
    *,
    pool_size: int = 10,
    max_overflow: int = 5,
    pool_pre_ping: bool = True,
    echo: bool = False,
    pgbouncer: bool = False,
) -> AsyncEngine:
    """Build an `AsyncEngine` for verbio-engine queries.

    Args:
        url: Postgres URL in any standard form; coerced to asyncpg driver.
        pool_size: persistent connection count (per process).
        max_overflow: burst capacity above `pool_size`.
        pool_pre_ping: lightweight "SELECT 1" before checkout to detect
            dropped backends (e.g., after a Railway restart).
        echo: SQL logging — leave off in production, very chatty.
        pgbouncer: set True when the URL points at pgBouncer transaction
            mode; disables asyncpg's prepared-statement cache.

    Returns:
        AsyncEngine ready for `create_session_factory`.
    """
    connect_args: dict[str, Any] = {}
    if pgbouncer:
        # pgBouncer transaction mode rewrites statement names across the
        # same backend connection; asyncpg's cache breaks on the next
        # prepare. Disabling the cache also disables auto-prepares.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0

    return create_async_engine(
        _to_asyncpg_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=pool_pre_ping,
        echo=echo,
        connect_args=connect_args,
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a session factory configured for verbio-engine workloads.

    `expire_on_commit=False` so attribute access after commit doesn't
    trigger refetches — important when the caller has already returned
    the row to the publish path and we want zero extra round-trips.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def dispose_engine(engine: AsyncEngine) -> None:
    """Cleanly tear down the async engine.

    Called from the FastAPI lifespan on shutdown. Without this, asyncpg
    can leak connections and Postgres logs spurious "connection reset"
    warnings on container restarts.
    """
    await engine.dispose()
