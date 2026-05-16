"""Typed events ingested by `StateStore`.

The state store is event-sourced: every change to participant or session
state flows in as one of these records. Two consequences:

  * Tests are trivial — no LiveKit, no Deepgram, no clock. Construct
    events, hand them to the store, assert on the projection.
  * The audit story Phase 6 needs is the event log; today the events
    are in-memory, but the schema is fixed now so we can persist them
    when replay arrives.

Discrimination is on the literal `type` field — Pydantic's tagged-union
discriminator gives us O(1) parsing of mixed event streams and JSON
Schema generation for the cross-service contract.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat


class _EventBase(BaseModel):
    """Common config + timestamp for every state event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ts: datetime = Field(
        ...,
        description="Wall-clock when the event was observed (UTC).",
    )


class ParticipantJoinEvent(_EventBase):
    """A participant entered the LiveKit room."""

    type: Literal["participant_join"] = "participant_join"
    participant_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)


class ParticipantLeaveEvent(_EventBase):
    """A participant disconnected. Their `ParticipantState` is removed."""

    type: Literal["participant_leave"] = "participant_leave"
    participant_id: str = Field(..., min_length=1)


class VadOnEvent(_EventBase):
    """VAD reports speech started on this track.

    Fires before STT produces text. The store uses it to maintain
    `vad_active`, `is_currently_speaking` (in the absence of an STT
    final to refute it), `silence_run_sec`, and interruption counters.
    """

    type: Literal["vad_on"] = "vad_on"
    participant_id: str = Field(..., min_length=1)


class VadOffEvent(_EventBase):
    """VAD reports speech stopped on this track."""

    type: Literal["vad_off"] = "vad_off"
    participant_id: str = Field(..., min_length=1)


class UtteranceFinalEvent(_EventBase):
    """A finalized STT result. Drives speaking-time accounting and transcripts.

    `start_ts` / `end_ts` come from the STT (Deepgram returns word
    timings). The store uses these — not `ts` — for window placement
    so a delayed final does not skew the rolling speaking time.
    """

    type: Literal["utterance_final"] = "utterance_final"
    utterance_id: str = Field(..., min_length=1)
    participant_id: str = Field(..., min_length=1)
    text: str
    start_ts: datetime
    end_ts: datetime
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    is_backchannel: bool = Field(
        default=False,
        description="Pre-classified short affirmation ('yeah', 'mm-hm'); "
        "increments `backchannel_count_last_2min` instead of `turn_count`.",
    )

    @property
    def duration_sec(self) -> NonNegativeFloat:
        """Length of the utterance in seconds. Never negative by construction."""
        return max((self.end_ts - self.start_ts).total_seconds(), 0.0)


StateEvent = (
    ParticipantJoinEvent | ParticipantLeaveEvent | VadOnEvent | VadOffEvent | UtteranceFinalEvent
)
"""Tagged union of every event the state store accepts."""
