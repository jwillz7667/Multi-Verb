"""`SessionState` — global, immutable snapshot of one room at one tick.

Brief §6 makes this the input to every rule:

    state = state_store.advance_to(t)
    evaluations = rules_engine.evaluate_all(state, t)

The store advances state by replaying typed `StateEvent` records into
the projection; `SessionState` is what the projection returns. The
snapshot is frozen so a rule predicate cannot mutate state it is
reading — that is the load-bearing guarantee the audit trail rests on.

The session-level fields (silence_run_sec, currently_speaking_count,
rolling_global_transcript_2min) are derived from the same event stream
that builds `ParticipantState`; the store is the single producer.

`is_paused`, `moderator_muted`, and `quietness_budget` are written by
researcher commands (Phase 5) and inspected by rule resolution; they
live on SessionState so a rule predicate that needs to know whether
the moderator is allowed to speak does not have to thread extra args.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.domain.participant import ParticipantState


class SessionState(BaseModel):
    """Per-tick projection of everything a rule needs to read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # ----- Identity --------------------------------------------------------
    session_id: UUID
    tick_id: NonNegativeInt = Field(
        ...,
        description="Monotonic per-session tick counter. First tick is 0.",
    )

    # ----- Clock -----------------------------------------------------------
    t: datetime = Field(..., description="Wall-clock for the current tick.")
    started_at: datetime = Field(..., description="Wall-clock when the session opened.")
    scheduled_end_at: datetime | None = Field(
        default=None,
        description="Planned end time; powers `time_remaining_pressure` rule.",
    )
    elapsed_sec: NonNegativeFloat = Field(
        ...,
        description="(t - started_at) in seconds; precomputed for convenience.",
    )

    # ----- Participants ----------------------------------------------------
    participants: dict[str, ParticipantState] = Field(
        default_factory=dict,
        description="Keyed by `participant_id`. Only currently-joined participants.",
    )

    # ----- Global aggregates -----------------------------------------------
    currently_speaking_count: NonNegativeInt = Field(
        default=0,
        description="Number of participants whose VAD or STT signals speech right now.",
    )
    silence_run_sec: NonNegativeFloat = Field(
        default=0.0,
        description="Seconds since the most recent global speech event; "
        "`silence_gap` rule reads this directly.",
    )
    rolling_global_transcript_2min: str = Field(
        default="",
        description="Concatenated finalized text from all speakers in the last 2 min, "
        "newest last. Used by `topic_drift` and `stalled_thread`.",
    )

    # ----- Researcher-controlled levers (Phase 5; defaulted now so rules can read safely) -
    is_paused: bool = Field(
        default=False,
        description="Set by `pause_session`; the tick loop still runs but the "
        "moderator is suppressed from speaking.",
    )
    moderator_muted: bool = Field(
        default=False,
        description="Set by `mute_moderator`; rules still evaluate so the audit "
        "trail remains complete.",
    )
    quietness_budget: QuietnessBudget = Field(
        default_factory=QuietnessBudget,
        description="Live-adjustable rate limit (brief §7.4).",
    )
