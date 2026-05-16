"""Phase 2 L3 — state_snapshots table.

Per brief §10.1 + §14 Phase 2: "State snapshots persisted to Postgres
every tick." The TickLoop runs at 2 Hz, so a 60-minute session writes
7200 rows. Storage cost is intentional; the audit value (replay,
post-hoc rule reasoning, dashboard backfill) is what justifies it.

`state` carries the full Pydantic SessionState as JSONB so each row is
self-contained — replay reconstructs the moment without joining back
to participants / utterances. A retention job (later phase) downsamples
to 1 Hz after 30 days.

Revision ID: 0004_state_snapshots
Revises: 0003_phase1_persistence
Create Date: 2026-05-16 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_state_snapshots"
down_revision: str | None = "0003_phase1_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "state_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "sessions.id",
                ondelete="CASCADE",
                name="fk_state_snapshots_session_id_sessions",
            ),
            nullable=False,
        ),
        sa.Column("tick_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "tick_id >= 0",
            name="ck_state_snapshots_tick_id_non_negative",
        ),
    )
    op.create_index(
        "ix_state_snapshots_session_id_tick_id",
        "state_snapshots",
        ["session_id", "tick_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_state_snapshots_session_id_tick_id",
        table_name="state_snapshots",
    )
    op.drop_table("state_snapshots")
