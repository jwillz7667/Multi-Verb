"""initial _health table for connectivity smoke tests.

Per brief §14 Phase 0: "first migration applies a `_health` table used
by both services for connectivity smoke tests."

Schema is intentionally minimal — a single row per service, written on
startup, read by /api/ready in the web tier. The leading underscore
marks it as infrastructure, not a domain table.

Revision ID: 0001_initial_health
Revises:
Create Date: 2026-05-16 10:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_health"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "_health",
        sa.Column("service", sa.Text(), primary_key=True),
        sa.Column(
            "checked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "version",
            sa.Text(),
            nullable=False,
        ),
    )

    op.execute(
        sa.text(
            """
            COMMENT ON TABLE _health IS
                'Connectivity smoke-test marker. One row per Verbio service, '
                'upserted on startup. Read by /api/ready in verbio-web to '
                'confirm engine + web share the same Postgres.';
            """,
        ),
    )


def downgrade() -> None:
    op.drop_table("_health")
