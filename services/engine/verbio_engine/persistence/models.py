"""ORM models for verbio-engine — Phase 1 subset.

Mirrors the Postgres schema in brief §10.1. Phase 1 introduces the
three tables needed for audio plumbing and transcription:

  - sessions     : one row per moderated session
  - participants : one row per joined participant (incl. moderator)
  - utterances   : final + interim STT outputs with timing

`studies` and `decisions`/`rule_evaluations`/`state_snapshots` land in
later phases per the brief. `sessions.study_id` is intentionally
nullable until Phase 3 introduces studies, so Phase 1 can create
sessions standalone.

Column types match the SQL in §10.1; differences are called out inline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    Boolean,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from verbio_engine.persistence.base import Base

SessionStatus = str
"""'scheduled' | 'live' | 'ended' | 'aborted'.

Modelled as plain TEXT in Postgres rather than an enum because adding a
new state needs a migration; TEXT + a CHECK constraint is more flexible
and `pgrx`-friendly.
"""

ParticipantRole = str
"""'participant' | 'researcher' | 'moderator'."""


class Session(Base):
    """A moderated session — one LiveKit room, one engine process."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # Nullable until Phase 3 wires studies in. The FK is *declared* now so
    # the migration only needs ALTER COLUMN ... SET NOT NULL later.
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    status: Mapped[SessionStatus] = mapped_column(
        Text(),
        nullable=False,
        default="scheduled",
    )
    scheduled_start: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    actual_start: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    actual_end: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    livekit_room_name: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        unique=True,
    )
    # `config_snapshot` is the frozen study config at session start. In
    # Phase 1 it's `{}`; Phase 3 fills it with the full study/rules snapshot.
    config_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB(),
        nullable=False,
        default=dict,
    )
    recording_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    per_participant_recording_urls: Mapped[dict[str, str] | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    participants: Mapped[list[Participant]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    utterances: Mapped[list[Utterance]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="raise",
    )


class Participant(Base):
    """A real-world joiner of a session (participant / researcher / moderator)."""

    __tablename__ = "participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(Text(), nullable=False)
    role: Mapped[ParticipantRole] = mapped_column(Text(), nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    left_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    # LiveKit's `identity` is the unique-per-room participant key; the
    # engine uses it to correlate audio tracks → participant rows.
    livekit_identity: Mapped[str] = mapped_column(Text(), nullable=False)

    session: Mapped[Session] = relationship(back_populates="participants")
    utterances: Mapped[list[Utterance]] = relationship(
        back_populates="participant",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        # A LiveKit identity is unique within a room (LiveKit enforces it
        # SFU-side). We enforce it in the DB too so duplicate-join bugs
        # surface here instead of as silent transcript loss.
        UniqueConstraint(
            "session_id",
            "livekit_identity",
            name="uq_participants_session_id_livekit_identity",
        ),
        Index("ix_participants_session_id", "session_id"),
    )


class Utterance(Base):
    """A speech segment from a participant — interim or final STT output.

    One row per Deepgram message we choose to persist. Interim rows are
    kept so the UI can render in-progress text; the final row arrives
    with `is_final=True` and represents the canonical transcript for that
    segment. Downstream rules consume the final rows only.
    """

    __tablename__ = "utterances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    participant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("participants.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Timing is wall-clock. The engine derives it from LiveKit track
    # timestamps + a Deepgram-provided offset so utterances align across
    # tracks within a session.
    start_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    end_ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text(), nullable=False)
    confidence: Mapped[float | None] = mapped_column(REAL(), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean(), nullable=False)

    session: Mapped[Session] = relationship(back_populates="utterances")
    participant: Mapped[Participant] = relationship(back_populates="utterances")

    __table_args__ = (
        # Rules read utterances ordered by start_ts within a session, so
        # this index covers the dominant query pattern.
        Index("ix_utterances_session_id_start_ts", "session_id", "start_ts"),
        Index("ix_utterances_participant_id_start_ts", "participant_id", "start_ts"),
    )
