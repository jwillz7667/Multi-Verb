"""Alembic migration environment for verbio-engine.

Reads `DATABASE_URL_DIRECT` from the environment (the non-pgBouncer
URL — pgBouncer rewrites prepared-statement names, which breaks
Alembic's introspection). The migrations themselves are hand-written
SQL/op DSL; autogenerate is not wired yet because the SQLAlchemy
metadata module (`verbio_engine.persistence.models`) lands in Phase 2.

To run:

    cd services/engine
    DATABASE_URL_DIRECT=postgresql://... uv run alembic upgrade head
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import MetaData, engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Placeholder MetaData — Phase 2 will replace this with the import of
# `verbio_engine.persistence.models.Base.metadata`. Until then,
# autogenerate is a no-op and migrations must be hand-authored.
target_metadata = MetaData()


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL_DIRECT") or os.environ.get("DATABASE_URL_ENGINE")
    if not url:
        msg = (
            "Set DATABASE_URL_DIRECT before running alembic. Use the direct "
            "(port 5432) URL — pgBouncer transaction mode (port 6543) breaks "
            "Alembic introspection."
        )
        raise RuntimeError(msg)
    # Alembic runs synchronously; engine runtime uses asyncpg. Normalize so
    # the same env var works in both places.
    if url.startswith("postgresql+asyncpg://"):
        url = "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg2://" + url[len("postgres://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://") :]
    return url


def run_migrations_offline() -> None:
    """Generate SQL without an active connection (for review or piping)."""
    context.configure(
        url=_resolve_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against a live database."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolve_database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
