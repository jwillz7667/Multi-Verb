"""ORM models for verbio-engine — Phase 1 through Phase 5 subset.

Mirrors the Postgres schema in brief §10.1. Tables introduced so far:

  - studies             : reusable session configuration (Phase 3 L11)
  - sessions            : one row per moderated session (Phase 1)
  - participants        : one row per joined participant (Phase 1)
  - utterances          : final + interim STT outputs with timing (Phase 1)
  - state_snapshots     : full SessionState frozen every tick (Phase 2 L3)
  - decisions           : one row per tick — action chosen or stay_silent (Phase 3 L9)
  - rule_evaluations    : one row per rule per tick — fired or not (Phase 3 L9)
  - researcher_actions  : researcher-issued commands (Phase 5 L1)
  - session_flags       : researcher / auto-generated bookmarks (Phase 5 L1)

`sessions.study_id` is nullable so standalone Phase 1-2 sessions stay
creatable; production traffic always attaches a study (web enforces
this at creation time).

Column types match the SQL in §10.1; differences are called out inline.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP, UUID
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


class Study(Base):
    """A reusable session configuration — prompt, rules, persona, retention.

    Studies are created by researchers in the web app; the engine treats
    them as read-only inputs. Sessions reference their study and at
    start-time snapshot the study's `rules_config` + `rules_version`
    into `sessions.config_snapshot` so replay reads the frozen
    configuration even if the study is later edited (brief §7.5 — rule
    versioning is sacred).

    `prompt_embedding` is JSONB-typed today (a serialized float array);
    a later migration will introduce pgvector and migrate the column.
    The topic_drift rule embeds in-process for now so the absence of
    this cache only costs us a re-embed on cold start.
    """

    __tablename__ = "studies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    prompt: Mapped[str] = mapped_column(Text(), nullable=False)
    prompt_embedding: Mapped[list[float] | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    rules_config: Mapped[dict[str, object]] = mapped_column(
        JSONB(),
        nullable=False,
        default=dict,
    )
    rules_version: Mapped[str] = mapped_column(Text(), nullable=False)
    moderator_persona: Mapped[dict[str, object]] = mapped_column(
        JSONB(),
        nullable=False,
        default=dict,
    )
    retention_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB(),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Auth.js user UUID from the web service. No FK because the engine
    # doesn't own the users table; see migration docstring.
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )

    sessions: Mapped[list[Session]] = relationship(
        back_populates="study",
        lazy="raise",
    )


class Session(Base):
    """A moderated session — one LiveKit room, one engine process."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # FK landed in Phase 3 L11; nullable so standalone Phase 1-2 sessions
    # still work. Production web flow always attaches a study.
    study_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("studies.id", ondelete="SET NULL"),
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

    study: Mapped[Study | None] = relationship(
        back_populates="sessions",
        lazy="raise",
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


class StateSnapshot(Base):
    """Full SessionState frozen at one tick — the audit-trail backbone.

    Cardinality: 2 Hz x 60-min session = 7200 rows per session. Storage
    cost is intentional (brief §10.1); the audit value (replay, dominance
    review, post-hoc rule reasoning) is what justifies it. A retention
    job downsamples to 1 Hz after 30 days.

    `state` carries the entire Pydantic SessionState as JSONB so the row
    is self-contained — replay reconstructs the moment without joining
    back to participants / utterances. The shape is the canonical
    `SessionState.model_dump(mode="json")` form so cross-language
    consumers parse it with the shared schema.
    """

    __tablename__ = "state_snapshots"

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
    tick_id: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    state: Mapped[dict[str, object]] = mapped_column(JSONB(), nullable=False)

    __table_args__ = (
        # Replay + tile-rerender both scan by (session_id, tick_id),
        # newest-last; this index is the dominant query pattern.
        Index(
            "ix_state_snapshots_session_id_tick_id",
            "session_id",
            "tick_id",
        ),
    )


class Decision(Base):
    """One row per tick — the action the engine chose, even when silent.

    `action='stay_silent'` rows are persisted at the same fidelity as
    spoken ones (brief §2 principle #2: every decision is auditable —
    silence included). The reason the row exists at all is so the
    dashboard can answer "why didn't it speak here?".

    `target_participant_id` uses `ON DELETE SET NULL` at the DB layer so
    a participant purge for retention leaves the historical decisions
    intact. We lose the link but keep the moderator's intent.
    """

    __tablename__ = "decisions"

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
    tick_id: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    target_participant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("participants.id", ondelete="SET NULL"),
        nullable=True,
    )
    source: Mapped[str] = mapped_column(Text(), nullable=False)
    triggering_rule: Mapped[str | None] = mapped_column(Text(), nullable=True)
    researcher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    researcher_hint: Mapped[str | None] = mapped_column(Text(), nullable=True)
    reason_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        default=list,
    )
    reason_human: Mapped[str] = mapped_column(
        Text(),
        nullable=False,
        default="",
    )
    confidence: Mapped[float | None] = mapped_column(REAL(), nullable=True)
    suppressed_by: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        default=list,
    )
    was_executed: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    llm_prompt: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    llm_output: Mapped[str | None] = mapped_column(Text(), nullable=True)
    tts_audio_url: Mapped[str | None] = mapped_column(Text(), nullable=True)
    spoken_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    cooldown_until: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )

    evaluations: Mapped[list[RuleEvaluation]] = relationship(
        back_populates="decision",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        # (session_id, ts) covers the dashboard timeline scan.
        Index("ix_decisions_session_id_ts", "session_id", "ts"),
        # Partial index for the "spoken decisions only" filter — most
        # shadow-mode rows are silent so the partial form is tiny.
        Index(
            "ix_decisions_session_id_was_executed_true",
            "session_id",
            "was_executed",
            postgresql_where="was_executed = true",
        ),
    )


class RuleEvaluation(Base):
    """One row per rule per tick — fired or not.

    Persisted at the same fidelity as the decision so a researcher can
    answer "what did rule X see here, and why did it stay silent?".
    `predicate_inputs` carries the rule-specific snapshot of state that
    the predicate read; the shape varies per rule (hence JSONB).

    Cascades with the parent decision: dropping a decision (rare —
    retention or test cleanup) takes its evaluations with it.
    """

    __tablename__ = "rule_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_name: Mapped[str] = mapped_column(Text(), nullable=False)
    rule_version: Mapped[str] = mapped_column(Text(), nullable=False)
    fired: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    suppressed_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)
    predicate_inputs: Mapped[dict[str, object]] = mapped_column(
        JSONB(),
        nullable=False,
        default=dict,
    )
    confidence: Mapped[float] = mapped_column(
        REAL(),
        nullable=False,
        default=0.0,
    )

    decision: Mapped[Decision] = relationship(back_populates="evaluations")

    __table_args__ = (Index("ix_rule_evaluations_decision_id", "decision_id"),)


class ResearcherAction(Base):
    """A researcher-issued command, persisted for the audit trail.

    Every command from §5.4 lands here regardless of whether it produced
    a spoken decision. Non-spoken control-plane commands (mute, pause,
    set_quietness_budget, flag_moment) leave `resulting_decision_id`
    null; force_prompt / force_redirect / force_summary / whisper link
    back to the `decisions` row they produced so the dashboard can
    distinguish auto vs. researcher-driven interventions.

    `researcher_id` is the Auth.js user UUID from the web service. No FK
    because the engine doesn't own the users table — same pattern as
    `studies.created_by`.
    """

    __tablename__ = "researcher_actions"

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
    researcher_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    command_type: Mapped[str] = mapped_column(Text(), nullable=False)
    payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(),
        nullable=True,
    )
    resulting_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("decisions.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        # Dashboard's "what happened in this session?" scan is (session_id, ts).
        Index("ix_researcher_actions_session_id_ts", "session_id", "ts"),
    )


class SessionFlag(Base):
    """A bookmark on a session moment — for replay surfacing.

    Created by the `flag_moment` researcher command (brief §5.4) or by
    the engine when it auto-detects something the replay tooling should
    foreground (e.g., a participant rejoining mid-stalled-thread). The
    `auto_generated` flag distinguishes the two so the UI can render
    them differently.

    `researcher_id` is nullable — auto-generated flags have no human
    author.
    """

    __tablename__ = "session_flags"

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
    ts: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    researcher_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text(), nullable=True)
    auto_generated: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        default=False,
    )

    __table_args__ = (Index("ix_session_flags_session_id_ts", "session_id", "ts"),)
