"""`ParticipantState` — per-participant rolling state per brief §5.1.

The rules engine reads this every tick to decide whether to intervene.
Field semantics, names, and defaults match the brief verbatim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt


class UtteranceRef(BaseModel):
    """A pointer to a recent utterance with the timing metadata the rules need."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    utterance_id: str = Field(..., description="Stable id; references utterances.utterance_id.")
    text: str = Field(..., description="Verbatim transcribed text (may be partial).")
    spoken_at: datetime = Field(..., description="Wall-clock start time of the utterance.")
    duration_sec: NonNegativeFloat = Field(
        ...,
        description="Length of the utterance in seconds.",
    )


class ParticipantFlags(BaseModel):
    """Derived booleans recomputed each tick from rolling state.

    Predicates are documented in `docs/rules-reference.md`. Each rule's
    predicate may consume one or more of these flags.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dominating: bool = Field(
        default=False,
        description="Actual share of last 5 min exceeds fair share by configured factor.",
    )
    silent_too_long: bool = Field(
        default=False,
        description="Has not spoken in N minutes (configured per study).",
    )
    frequently_interrupted: bool = Field(
        default=False,
        description="`was_interrupted_count` exceeds threshold over the rolling window.",
    )
    disengaged: bool = Field(
        default=False,
        description="No backchannels and no speech in the recent window.",
    )


class ParticipantState(BaseModel):
    """Per-participant snapshot consumed by every rule on every tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    participant_id: str = Field(..., description="Stable application id.")
    display_name: str = Field(..., description="Human-readable label shown to researchers.")
    joined_at: datetime

    # ----- Speaking metrics (rolling) --------------------------------------
    speaking_time_total_sec: NonNegativeFloat = 0.0
    speaking_time_last_5min_sec: NonNegativeFloat = 0.0
    speaking_time_last_60sec: NonNegativeFloat = 0.0
    turn_count: NonNegativeInt = 0
    last_spoke_at: datetime | None = None
    last_spoke_duration_sec: NonNegativeFloat | None = None

    # ----- Engagement signals ----------------------------------------------
    is_currently_speaking: bool = False
    vad_active: bool = Field(
        default=False,
        description="Voice activity detected but not yet transcribed.",
    )
    backchannel_count_last_2min: NonNegativeInt = 0
    interruption_count: NonNegativeInt = Field(
        default=0,
        description="Times this participant cut someone else off.",
    )
    was_interrupted_count: NonNegativeInt = 0

    # ----- Content signals -------------------------------------------------
    recent_utterances: Annotated[list[UtteranceRef], Field(default_factory=list, max_length=5)]
    rolling_transcript_2min: str = ""

    # ----- Derived flags ---------------------------------------------------
    flags: ParticipantFlags = Field(default_factory=ParticipantFlags)

    # ----- Fairness math ---------------------------------------------------
    fair_share_pct: Annotated[float, Field(ge=0.0, le=100.0)] = 0.0
    actual_share_last_5min_pct: Annotated[float, Field(ge=0.0, le=100.0)] = 0.0
