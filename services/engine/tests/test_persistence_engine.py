"""Unit tests for the persistence engine factory (no DB required)."""

from __future__ import annotations

import pytest

from verbio_engine.persistence.engine import _to_asyncpg_url


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            "postgresql+asyncpg://u:p@h:5432/db",
            "postgresql+asyncpg://u:p@h:5432/db",
        ),
        (
            "postgresql+psycopg2://u:p@h:5432/db",
            "postgresql+asyncpg://u:p@h:5432/db",
        ),
        (
            "postgresql://u:p@h:5432/db",
            "postgresql+asyncpg://u:p@h:5432/db",
        ),
        (
            "postgres://u:p@h:5432/db",
            "postgresql+asyncpg://u:p@h:5432/db",
        ),
    ],
)
def test_to_asyncpg_url_normalises_schemes(given: str, expected: str) -> None:
    assert _to_asyncpg_url(given) == expected


def test_to_asyncpg_url_rejects_unknown_scheme() -> None:
    with pytest.raises(ValueError, match="Unrecognised Postgres URL scheme"):
        _to_asyncpg_url("mysql://u:p@h/db")
