"""Tunable thresholds for `StateStore` projections.

These are the knobs that decide *how* derived fields are computed —
window sizes for speaking-time aggregates, factors for the `dominating`
flag, cutoffs for `silent_too_long` and `disengaged`.

They live separate from rule config because flags surface on the
dashboard whether the rules engine is active or not (Phase 2 ships the
tiles before Phase 3 ships rules). Per-study overrides arrive in
Phase 3 when `RulesConfig` lands; until then the defaults below are
authoritative.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt


class StateStoreConfig(BaseModel):
    """Window sizes and flag thresholds used by `StateStore`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ----- Rolling windows -------------------------------------------------
    last_5min_window_sec: PositiveFloat = Field(
        default=300.0,
        description="Window for `speaking_time_last_5min_sec` and `actual_share_last_5min_pct`.",
    )
    last_60sec_window_sec: PositiveFloat = Field(
        default=60.0,
        description="Window for `speaking_time_last_60sec`.",
    )
    rolling_transcript_window_sec: PositiveFloat = Field(
        default=120.0,
        description="Window for `rolling_transcript_2min` and the global counterpart.",
    )
    backchannel_window_sec: PositiveFloat = Field(
        default=120.0,
        description="Window for `backchannel_count_last_2min`.",
    )
    interruption_window_sec: PositiveFloat = Field(
        default=120.0,
        description="Window over which interruptions decay for `frequently_interrupted` flag.",
    )

    # ----- Flag thresholds -------------------------------------------------
    dominating_factor: PositiveFloat = Field(
        default=2.0,
        description="`dominating` fires when actual share >= factor * fair share.",
    )
    silent_too_long_sec: PositiveFloat = Field(
        default=240.0,
        description="`silent_too_long` fires after this many seconds without finals.",
    )
    frequently_interrupted_threshold: PositiveInt = Field(
        default=3,
        description="`frequently_interrupted` fires once `was_interrupted_count` >= this.",
    )
    disengaged_silence_sec: PositiveFloat = Field(
        default=120.0,
        description="`disengaged` requires no finals for this long AND no backchannels.",
    )

    # ----- Recent-utterance buffer ----------------------------------------
    recent_utterances_max: Annotated[PositiveInt, Field(le=5)] = Field(
        default=5,
        description="Brief §5.1 caps `recent_utterances` at 5; matches `ParticipantState` schema.",
    )
