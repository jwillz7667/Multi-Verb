"""Phase 3 L11 — studies table + session→study foreign key.

Per brief §10.1 + §7.5: studies are the reusable configuration unit
that owns a research prompt, a `RulesConfig`, a moderator persona, and
a retention policy. Sessions reference their study (`sessions.study_id`,
column added in migration 0003) and at session start snapshot the
study's config into `sessions.config_snapshot` so replay reads the
frozen config even if the study is later edited.

Scope notes vs. the brief schema:

  * `prompt_embedding vector(1536)` is deferred — pgvector is not yet
    installed on Railway Postgres and the topic_drift rule runs from an
    in-memory embedding today (the coordinator re-embeds on cold start).
    A later migration will add the column + the extension; until then
    the prompt text is stored verbatim and embeddings live in the
    engine process. The deferral is invisible to consumers — studies
    can still be created and used; the embedding cache just stays warm
    in-process rather than across restarts.
  * `created_by` is `uuid NOT NULL` per brief. The web app's Auth.js
    user UUID is the writer; we don't add an FK to a web-owned table
    because the engine doesn't own the users table and the web service
    drops/recreates Auth.js tables out-of-band via Prisma. Storing the
    raw UUID keeps the audit field without coupling schemas.
  * The session→study FK uses `ON DELETE SET NULL` so a study purge
    leaves historical sessions intact (the `config_snapshot` jsonb is
    self-contained — the FK link is a convenience, not the source of
    truth). This mirrors the decisions→participants FK rationale.

Revision ID: 0006_studies_table
Revises: 0005_decisions_rule_evals
Create Date: 2026-05-16 13:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_studies_table"
down_revision: str | None = "0005_decisions_rule_evals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "studies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        # Deferred per brief; see module docstring. Stored as JSONB so a
        # future migration can either populate it from a pgvector
        # backfill or drop the column outright without a data-loss path.
        sa.Column(
            "prompt_embedding",
            postgresql.JSONB(),
            nullable=True,
        ),
        sa.Column(
            "rules_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("rules_version", sa.Text(), nullable=False),
        sa.Column(
            "moderator_persona",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "retention_policy",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
    )
    op.create_index("ix_studies_org_id", "studies", ["org_id"])

    # `sessions.study_id` was declared nullable in migration 0003 with
    # the FK deferred to this layer (the migration docstring there
    # spelled out the plan). Add the FK now; keep it nullable so
    # standalone Phase 1-2 sessions stay creatable without a study.
    op.create_foreign_key(
        "fk_sessions_study_id_studies",
        "sessions",
        "studies",
        ["study_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_sessions_study_id_studies",
        "sessions",
        type_="foreignkey",
    )
    op.drop_index("ix_studies_org_id", table_name="studies")
    op.drop_table("studies")
