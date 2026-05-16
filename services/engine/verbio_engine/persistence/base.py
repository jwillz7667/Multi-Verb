"""SQLAlchemy declarative base + naming conventions.

Alembic autogenerate produces stable, predictable identifier names when
the MetaData carries an explicit `naming_convention`. We adopt the
SQLAlchemy-recommended template so future migrations diff cleanly.
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Alembic autogen produces stable names with this convention. See
# https://alembic.sqlalchemy.org/en/latest/naming.html
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Base(DeclarativeBase):
    """Declarative base used by every ORM model in verbio-engine."""

    metadata = metadata
