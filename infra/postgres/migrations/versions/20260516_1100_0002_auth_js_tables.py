"""Auth.js v5 (next-auth) tables for the web app.

Per brief §14 Phase 0: "Vercel deploy of empty Next.js app with Auth.js
v5 (Postgres adapter over DATABASE_URL_POOLED) + Resend magic-link."

The Postgres adapter for Auth.js requires four tables. Column shapes
are taken verbatim from
https://authjs.dev/getting-started/adapters/prisma and mirrored in
apps/web/prisma/schema.prisma. Snake_case here; Prisma's @map annotations
project the camelCase fields next-auth expects.

Revision ID: 0002_auth_js_tables
Revises: 0001_initial_health
Create Date: 2026-05-16 11:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth_js_tables"
down_revision: str | None = "0001_initial_health"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("email_verified", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("image", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_account_id", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.Integer(), nullable=True),
        sa.Column("token_type", sa.Text(), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("id_token", sa.Text(), nullable=True),
        sa.Column("session_state", sa.Text(), nullable=True),
        sa.Column("refresh_token_expires_in", sa.Integer(), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_account_id",
            name="accounts_provider_provider_account_id_key",
        ),
    )
    op.create_index("accounts_user_id_idx", "accounts", ["user_id"])

    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_token", sa.Text(), nullable=False, unique=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires", sa.TIMESTAMP(timezone=True), nullable=False),
    )
    op.create_index("sessions_user_id_idx", "sessions", ["user_id"])

    op.create_table(
        "verification_tokens",
        sa.Column("identifier", sa.Text(), nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("expires", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "identifier",
            "token",
            name="verification_tokens_identifier_token_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("verification_tokens")
    op.drop_index("sessions_user_id_idx", table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("accounts_user_id_idx", table_name="accounts")
    op.drop_table("accounts")
    op.drop_table("users")
