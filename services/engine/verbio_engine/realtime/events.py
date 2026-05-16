"""SSE event envelopes — Pydantic source of truth.

Per brief §11 the dashboard observes live state via Server-Sent Events
fed by Redis pub/sub on `verbio:events:{session_id}`. The envelope is
`{type, id, payload}` where `type` discriminates between utterance,
decision, and state-snapshot events.

Phase 1 emits **only** the `utterance` variant (the dashboard shows a
live transcript and nothing else, per Phase 1 done-when). Decision and
state-snapshot variants land in Phase 3 when the rules engine starts
firing; the union here will grow at that point.

Why Pydantic in the engine, not Zod in the web: the brief mandates a
single canonical source for cross-service shapes (Pydantic → JSON Schema
→ TS). The web side imports the generated TS type and pairs it with a
Zod schema for runtime wire-validation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def channel_for(session_id: uuid.UUID) -> str:
    """Canonical Redis channel name for a session.

    Single helper so the engine and the web side can't drift on naming;
    web mirrors this in `lib/redis.ts`. The `verbio:events:` prefix is
    fixed by brief §11.
    """
    return f"verbio:events:{session_id}"


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


class TranscriptEvent(BaseModel):
    """Envelope published to `verbio:events:{session_id}` for SSE fan-out.

    Phase 1 always has `type="utterance"`. The literal-narrowed union
    grows when Phase 3 introduces decision and state-snapshot events;
    web's discriminated parser then expands accordingly.

    `id` is the SSE event id — clients echo it back as `Last-Event-ID`
    on reconnect, and the web SSE route uses it to skip already-seen
    rows during the Postgres backfill. For utterance events we use the
    utterance UUID directly; it's globally unique and resolvable to a
    row for the backfill cursor.

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
) -> TranscriptEvent:
    """Build a `TranscriptEvent` for a freshly-persisted utterance.

    Convenience constructor so call-sites don't repeat the envelope
    boilerplate (id mirroring utterance_id, ts=now, type literal).
    """
    return TranscriptEvent(
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
