"""Phase 1 — sessions, participants, utterances tables.

Per brief §14 Phase 1: "Utterance persistence to Postgres with proper
timing." Schema matches the relevant subset of §10.1; later phases add
the remaining tables (`studies`, `state_snapshots`, `decisions`,
`rule_evaluations`, `researcher_actions`, `session_flags`).

`sessions.study_id` is intentionally nullable until Phase 3 introduces
the `studies` table; we declare the column now so the later migration
is a simple ALTER ... SET NOT NULL + ADD FOREIGN KEY.

Revision ID: 0003_phase1_persistence
Revises: 0002_auth_js_tables
Create Date: 2026-05-16 11:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Kept short (≤ 32 chars) so it fits the default `alembic_version.version_num`
# VARCHAR(32) column without needing per-environment configuration.
revision: str = "0003_phase1_persistence"
down_revision: str | None = "0002_auth_js_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "study_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="scheduled"),
        sa.Column("scheduled_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("actual_start", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("actual_end", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "livekit_room_name",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "config_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("recording_url", sa.Text(), nullable=True),
        sa.Column(
            "per_participant_recording_urls",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "livekit_room_name",
            name="uq_sessions_livekit_room_name",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'live', 'ended', 'aborted')",
            name="ck_sessions_status",
        ),
    )
    op.create_index("ix_sessions_study_id", "sessions", ["study_id"])
    op.create_index("ix_sessions_status", "sessions", ["status"])

    op.create_table(
        "participants",
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
                name="fk_participants_session_id_sessions",
            ),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("joined_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("left_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("livekit_identity", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "session_id",
            "livekit_identity",
            name="uq_participants_session_id_livekit_identity",
        ),
        sa.CheckConstraint(
            "role IN ('participant', 'researcher', 'moderator')",
            name="ck_participants_role",
        ),
    )
    op.create_index("ix_participants_session_id", "participants", ["session_id"])

    op.create_table(
        "utterances",
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
                name="fk_utterances_session_id_sessions",
            ),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "participants.id",
                ondelete="CASCADE",
                name="fk_utterances_participant_id_participants",
            ),
            nullable=False,
        ),
        sa.Column("start_ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("end_ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "end_ts >= start_ts",
            name="ck_utterances_end_ts_after_start_ts",
        ),
    )
    op.create_index(
        "ix_utterances_session_id_start_ts",
        "utterances",
        ["session_id", "start_ts"],
    )
    op.create_index(
        "ix_utterances_participant_id_start_ts",
        "utterances",
        ["participant_id", "start_ts"],
    )


def downgrade() -> None:
    op.drop_index("ix_utterances_participant_id_start_ts", table_name="utterances")
    op.drop_index("ix_utterances_session_id_start_ts", table_name="utterances")
    op.drop_table("utterances")

    op.drop_index("ix_participants_session_id", table_name="participants")
    op.drop_table("participants")

    op.drop_index("ix_sessions_status", table_name="sessions")
    op.drop_index("ix_sessions_study_id", table_name="sessions")
    op.drop_table("sessions")
