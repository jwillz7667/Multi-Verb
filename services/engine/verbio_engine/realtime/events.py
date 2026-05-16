"""SSE event envelopes — Pydantic source of truth.

Per brief §11 the dashboard observes live state via Server-Sent Events
fed by Redis pub/sub on `verbio:events:{session_id}`. Each envelope is
shaped `{type, id, session_id, ts, payload}` where `type` is the
discriminator across variants.

Variants today:
  - `utterance`      (Phase 1): one STT segment
  - `state_snapshot` (Phase 2 L3): full SessionState frozen at one tick
  - `decision`       (Phase 3 L10): rule-driven moderator decision

`TranscriptEvent` is a discriminated union built from the per-variant
envelope classes; consumers should construct via the typed envelope
classes (e.g., `UtteranceEventEnvelope(...)` or the `utterance_event`
helper) and validate inbound JSON via `TranscriptEventAdapter`. The
TypeAdapter route exposes a single `json_schema()` that the shared-types
pipeline ships to the web side intact.

Why Pydantic in the engine, not Zod in the web: the brief mandates a
single canonical source for cross-service shapes (Pydantic → JSON Schema
→ TS). The web side imports the generated TS type and pairs it with a
Zod schema for runtime wire-validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, TypeAdapter

from verbio_engine.domain.decision import ModeratorDecision
from verbio_engine.domain.session_state import SessionState


def channel_for(session_id: uuid.UUID) -> str:
    """Canonical Redis channel name for a session.

    Single helper so the engine and the web side can't drift on naming;
    web mirrors this in `lib/redis.ts`. The `verbio:events:` prefix is
    fixed by brief §11.
    """
    return f"verbio:events:{session_id}"


# ---------------------------------------------------------------------------
# Utterance variant (Phase 1)
# ---------------------------------------------------------------------------


class UtteranceEventPayload(BaseModel):
    """Snapshot of an utterance row at the moment it was persisted.

    Self-contained — the dashboard renders without joining back to
    Postgres. `participant_identity` + `participant_display_name` are
    denormalised so a single SSE message is enough to draw a row.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    utterance_id: uuid.UUID
    session_id: uuid.UUID
    participant_id: uuid.UUID
    # LiveKit identity (stable across reconnects within a room) — used by
    # the web client to group consecutive utterances under one speaker
    # without re-querying the participants table.
    participant_identity: str = Field(min_length=1)
    participant_display_name: str = Field(min_length=1)
    text: str
    is_final: bool
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    start_ts: datetime
    end_ts: datetime


class UtteranceEventEnvelope(BaseModel):
    """SSE envelope for an STT-derived utterance.

    `id` is the utterance UUID as a string — SSE clients echo it back as
    `Last-Event-ID` on reconnect, and the web SSE route uses it to skip
    already-seen rows during the Postgres backfill.

    `ts` is the server-side moment the event was created (not the
    utterance's start_ts), used purely for diagnostics — ordering is by
    payload start_ts on backfill.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["utterance"]
    id: str = Field(min_length=1)
    session_id: uuid.UUID
    ts: datetime
    payload: UtteranceEventPayload


# ---------------------------------------------------------------------------
# State-snapshot variant (Phase 2 L3)
# ---------------------------------------------------------------------------


class StateSnapshotEventPayload(BaseModel):
    """Snapshot of one full `SessionState` row at the moment it was persisted.

    Carries the entire SessionState so the dashboard can render every
    tile (speaking time, last spoke, currently_speaking, silence_run,
    flags) from a single message. `snapshot_id` is the Postgres row id;
    keeping it here as well as on the envelope mirrors the utterance
    variant and gives the web side typed-UUID access without re-parsing
    the envelope id.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: uuid.UUID
    state: SessionState


class StateSnapshotEventEnvelope(BaseModel):
    """SSE envelope for one per-tick SessionState snapshot.

    `id` is the snapshot row UUID as a string; `ts` is the wall-clock
    moment the tick projected the snapshot (mirrors `state.t`). The
    Last-Event-ID cursor uses `id` to backfill from
    `state_snapshots.id > {last_id}` on reconnect — the table's
    (session_id, tick_id) index covers that scan cheaply.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["state_snapshot"]
    id: str = Field(min_length=1)
    session_id: uuid.UUID
    ts: datetime
    payload: StateSnapshotEventPayload


# ---------------------------------------------------------------------------
# Decision variant (Phase 3 L10)
# ---------------------------------------------------------------------------


class DecisionEventPayload(BaseModel):
    """Snapshot of one moderator decision at the moment it was persisted.

    Carries the full `ModeratorDecision` plus the DB row id (`decision_id`
    matches `decisions.id`). The dashboard's decision log + "Why quiet
    now?" panel render from this payload alone — no second trip to
    Postgres for the common case.

    In shadow mode (Phase 3) `was_executed` is always False and the
    mouth-layer fields (`llm_prompt`, `llm_output`, `tts_audio_url`,
    `spoken_at`) are all None. The envelope ships anyway so the
    dashboard can show the silent-decision stream while researchers
    review.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: ModeratorDecision


class DecisionEventEnvelope(BaseModel):
    """SSE envelope for one per-tick moderator decision.

    `id` mirrors the `decisions.id` UUID as a string so the web SSE
    route's Last-Event-ID backfill maps it straight to the row. `ts`
    is the tick wall-clock (`decision.timestamp`) so the client can
    order across reconnects without unwrapping the payload.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["decision"]
    id: str = Field(min_length=1)
    session_id: uuid.UUID
    ts: datetime
    payload: DecisionEventPayload


# ---------------------------------------------------------------------------
# Union + validation entry-point
# ---------------------------------------------------------------------------


TranscriptEvent = Annotated[
    UtteranceEventEnvelope | StateSnapshotEventEnvelope | DecisionEventEnvelope,
    Field(discriminator="type"),
]
"""Discriminated union over every per-session SSE envelope variant.

Phase 3 L10 adds the `decision` variant; the discriminator pattern
keeps that change additive on the web side."""


TranscriptEventAdapter: TypeAdapter[
    UtteranceEventEnvelope | StateSnapshotEventEnvelope | DecisionEventEnvelope
] = TypeAdapter(TranscriptEvent)
"""Single entry-point for validation, JSON Schema export, and bulk dump.

Use this rather than per-class `model_validate` when parsing inbound
SSE bodies — it applies the discriminator and returns the correct
envelope subtype.
"""


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def utterance_event(
    *,
    utterance_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    participant_identity: str,
    participant_display_name: str,
    text: str,
    is_final: bool,
    confidence: float | None,
    start_ts: datetime,
    end_ts: datetime,
) -> UtteranceEventEnvelope:
    """Build a `UtteranceEventEnvelope` for a freshly-persisted utterance.

    Convenience constructor so call-sites don't repeat the envelope
    boilerplate (id mirroring utterance_id, ts=now, type literal).
    """
    return UtteranceEventEnvelope(
        type="utterance",
        id=str(utterance_id),
        session_id=session_id,
        ts=datetime.now(UTC),
        payload=UtteranceEventPayload(
            utterance_id=utterance_id,
            session_id=session_id,
            participant_id=participant_id,
            participant_identity=participant_identity,
            participant_display_name=participant_display_name,
            text=text,
            is_final=is_final,
            confidence=confidence,
            start_ts=start_ts,
            end_ts=end_ts,
        ),
    )


def decision_event(*, decision: ModeratorDecision) -> DecisionEventEnvelope:
    """Build a `DecisionEventEnvelope` for a freshly-persisted decision.

    `decision.decision_id` is the same UUID the resolver minted up front
    and the L9 repo persisted, so the envelope `id` is `str(decision_id)`
    and the SSE backfill resolves it directly to `decisions.id`.
    """
    return DecisionEventEnvelope(
        type="decision",
        id=str(decision.decision_id),
        session_id=decision.session_id,
        ts=decision.timestamp,
        payload=DecisionEventPayload(decision=decision),
    )


def state_snapshot_event(
    *,
    snapshot_id: uuid.UUID,
    state: SessionState,
) -> StateSnapshotEventEnvelope:
    """Build a `StateSnapshotEventEnvelope` for a freshly-persisted snapshot.

    Mirrors `utterance_event`: the envelope `id` echoes the row UUID,
    `ts` is `state.t` so consumers can order by tick wall-clock without
    parsing the nested payload.
    """
    return StateSnapshotEventEnvelope(
        type="state_snapshot",
        id=str(snapshot_id),
        session_id=state.session_id,
        ts=state.t,
        payload=StateSnapshotEventPayload(
            snapshot_id=snapshot_id,
            state=state,
        ),
    )


# Re-export `NonNegativeInt` so consumers of the module that need to
# annotate tick counters can pull it from here without a second import
# (keeps the public surface coherent).
__all__ = [
    "DecisionEventEnvelope",
    "DecisionEventPayload",
    "NonNegativeInt",
    "StateSnapshotEventEnvelope",
    "StateSnapshotEventPayload",
    "TranscriptEvent",
    "TranscriptEventAdapter",
    "UtteranceEventEnvelope",
    "UtteranceEventPayload",
    "channel_for",
    "decision_event",
    "state_snapshot_event",
    "utterance_event",
]
