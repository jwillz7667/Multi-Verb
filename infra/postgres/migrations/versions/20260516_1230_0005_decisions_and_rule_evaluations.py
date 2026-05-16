"""Phase 3 — decisions + rule_evaluations tables.

Per brief sections 10.1 and 14 Phase 3: "Every tick writes a decisions
row + N rule_evaluations rows." Storage of every rule's verdict (fired
or not) is the load-bearing artifact behind the dashboard's "Why quiet
now?" panel and the post-hoc 70%-agreement review gate.

Schema notes beyond the brief:

  * `decisions.action` and `decisions.source` carry CHECK constraints
    bound to the Pydantic Literal sets in `verbio_engine.domain.decision`.
    A new action value is a brief revision plus a migration — refusing
    unknown values at the DB stops a typo from polluting the audit log.
  * `target_participant_id` uses `ON DELETE SET NULL` because a
    decision should outlive a deleted participant row; the historical
    record of *who the moderator was trying to address* is part of the
    audit trail even if the participant is later purged for retention.
  * `decisions.id` is cascaded by `rule_evaluations.decision_id` so a
    session purge takes its decisions and their evaluations cleanly.
  * Partial index `(session_id, was_executed) WHERE was_executed = true`
    is the brief's idiom for the dashboard's "spoken decisions" view —
    keeps the index small since most shadow-mode rows are silent.
  * `predicate_inputs` is JSONB because each rule reads a different
    subset of state; a typed column would either bloat with NULLs or
    require a per-rule table.

Revision ID: 0005_decisions_rule_evals
Revises: 0004_state_snapshots
Create Date: 2026-05-16 12:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_decisions_rule_evals"
down_revision: str | None = "0004_state_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DECISION_ACTIONS = (
    "stay_silent",
    "prompt_participant",
    "redirect_topic",
    "summarize_thread",
    "request_clarification",
    "suggest_turn_taking",
    "close_session",
)
_DECISION_SOURCES = ("auto", "researcher_manual", "researcher_whisper")
_SUPPRESSED_REASONS = ("cooldown", "lower_priority_won", "disabled", "quietness_budget")


def _quote(values: tuple[str, ...]) -> str:
    """Format a tuple of string literals for an `IN (...)` SQL clause."""
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "decisions",
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
                name="fk_decisions_session_id_sessions",
            ),
            nullable=False,
        ),
        sa.Column("tick_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "target_participant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "participants.id",
                ondelete="SET NULL",
                name="fk_decisions_target_participant_id_participants",
            ),
            nullable=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("triggering_rule", sa.Text(), nullable=True),
        sa.Column(
            "researcher_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("researcher_hint", sa.Text(), nullable=True),
        sa.Column(
            "reason_codes",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "reason_human",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("confidence", sa.REAL(), nullable=True),
        sa.Column(
            "suppressed_by",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("was_executed", sa.Boolean(), nullable=False),
        sa.Column("llm_prompt", postgresql.JSONB(), nullable=True),
        sa.Column("llm_output", sa.Text(), nullable=True),
        sa.Column("tts_audio_url", sa.Text(), nullable=True),
        sa.Column("spoken_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("cooldown_until", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tick_id >= 0",
            name="ck_decisions_tick_id_non_negative",
        ),
        sa.CheckConstraint(
            f"action IN ({_quote(_DECISION_ACTIONS)})",
            name="ck_decisions_action",
        ),
        sa.CheckConstraint(
            f"source IN ({_quote(_DECISION_SOURCES)})",
            name="ck_decisions_source",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_decisions_confidence_unit",
        ),
        # Auto-sourced decisions must name their rule; researcher overrides
        # may leave it null. Belt-and-braces against a writer forgetting
        # to thread the rule name through.
        sa.CheckConstraint(
            "(source <> 'auto') OR (triggering_rule IS NOT NULL) OR (action = 'stay_silent')",
            name="ck_decisions_auto_has_rule_or_silent",
        ),
    )
    op.create_index(
        "ix_decisions_session_id_ts",
        "decisions",
        ["session_id", "ts"],
    )
    # Partial index per brief — the dashboard's "spoken" filter is by
    # far the most-read predicate; storing only `true` rows keeps it
    # cheap on shadow-mode sessions that are mostly silent.
    op.create_index(
        "ix_decisions_session_id_was_executed_true",
        "decisions",
        ["session_id", "was_executed"],
        postgresql_where=sa.text("was_executed = true"),
    )

    op.create_table(
        "rule_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "decisions.id",
                ondelete="CASCADE",
                name="fk_rule_evaluations_decision_id_decisions",
            ),
            nullable=False,
        ),
        sa.Column("rule_name", sa.Text(), nullable=False),
        sa.Column("rule_version", sa.Text(), nullable=False),
        sa.Column("fired", sa.Boolean(), nullable=False),
        sa.Column("suppressed_reason", sa.Text(), nullable=True),
        sa.Column(
            "predicate_inputs",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "confidence",
            sa.REAL(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.CheckConstraint(
            f"suppressed_reason IS NULL OR suppressed_reason IN ({_quote(_SUPPRESSED_REASONS)})",
            name="ck_rule_evaluations_suppressed_reason",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_rule_evaluations_confidence_unit",
        ),
    )
    op.create_index(
        "ix_rule_evaluations_decision_id",
        "rule_evaluations",
        ["decision_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_rule_evaluations_decision_id", table_name="rule_evaluations")
    op.drop_table("rule_evaluations")

    op.drop_index(
        "ix_decisions_session_id_was_executed_true",
        table_name="decisions",
        postgresql_where=sa.text("was_executed = true"),
    )
    op.drop_index("ix_decisions_session_id_ts", table_name="decisions")
    op.drop_table("decisions")
