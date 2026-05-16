"""Shared test fixtures for rule predicate tests.

Rule tests are table-driven: they construct a `SessionState`, call
`rule.predicate(state, t)`, and assert on the result. SessionState
has a lot of required fields, so we centralise builders here to keep
each rule's test file readable and to make 'change one field, keep
the rest at sane defaults' the easy path.

Builders return frozen Pydantic models — tests that need a variant
build a new one via `.model_copy(update=...)`. Keeping the builders
small and parameterless-by-default forces tests to be explicit about
which fields drive the rule under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from verbio_engine.domain import (
    ParticipantFlags,
    ParticipantState,
    QuietnessBudget,
    SessionState,
    UtteranceRef,
)

# Fixed clock anchor so timestamps in tests are readable. Mid-2026
# matches the project's currentDate; arbitrary aside from that.
NOW = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)
"""Wall-clock 't' used by tests unless overridden."""

SESSION_ID = UUID("11111111-1111-4111-8111-111111111111")
"""Stable session UUID so failure messages are diff-friendly."""


def make_participant(
    *,
    participant_id: str = "p-1",
    display_name: str = "Alice",
    joined_at: datetime | None = None,
    last_spoke_at: datetime | None = None,
    last_spoke_duration_sec: float | None = None,
    speaking_time_total_sec: float = 0.0,
    speaking_time_last_5min_sec: float = 0.0,
    speaking_time_last_60sec: float = 0.0,
    turn_count: int = 0,
    is_currently_speaking: bool = False,
    vad_active: bool = False,
    backchannel_count_last_2min: int = 0,
    interruption_count: int = 0,
    was_interrupted_count: int = 0,
    recent_utterances: list[UtteranceRef] | None = None,
    rolling_transcript_2min: str = "",
    flags: ParticipantFlags | None = None,
    fair_share_pct: float = 25.0,
    actual_share_last_5min_pct: float = 25.0,
) -> ParticipantState:
    """Build a `ParticipantState` with rule-test-friendly defaults.

    Defaults represent a 'fresh participant who has not spoken yet':
    every speaking metric is zero, no recent utterances, idle flags.
    Tests override only the fields they care about.
    """
    return ParticipantState(
        participant_id=participant_id,
        display_name=display_name,
        joined_at=joined_at if joined_at is not None else NOW - timedelta(minutes=10),
        speaking_time_total_sec=speaking_time_total_sec,
        speaking_time_last_5min_sec=speaking_time_last_5min_sec,
        speaking_time_last_60sec=speaking_time_last_60sec,
        turn_count=turn_count,
        last_spoke_at=last_spoke_at,
        last_spoke_duration_sec=last_spoke_duration_sec,
        is_currently_speaking=is_currently_speaking,
        vad_active=vad_active,
        backchannel_count_last_2min=backchannel_count_last_2min,
        interruption_count=interruption_count,
        was_interrupted_count=was_interrupted_count,
        recent_utterances=recent_utterances if recent_utterances is not None else [],
        rolling_transcript_2min=rolling_transcript_2min,
        flags=flags if flags is not None else ParticipantFlags(),
        fair_share_pct=fair_share_pct,
        actual_share_last_5min_pct=actual_share_last_5min_pct,
    )


def make_session_state(
    *,
    tick_id: int = 0,
    t: datetime | None = None,
    started_at: datetime | None = None,
    scheduled_end_at: datetime | None = None,
    participants: dict[str, ParticipantState] | None = None,
    currently_speaking_count: int = 0,
    silence_run_sec: float = 0.0,
    rolling_global_transcript_2min: str = "",
    is_paused: bool = False,
    moderator_muted: bool = False,
    quietness_budget: QuietnessBudget | None = None,
    study_prompt: str = "",
    study_prompt_embedding: list[float] | None = None,
    rolling_transcript_30s_embedding: list[float] | None = None,
    embedding_model_name: str | None = None,
) -> SessionState:
    """Build a `SessionState` with rule-test-friendly defaults.

    Defaults model a 10-minute-old session with no participants and
    no recent activity — every rule should evaluate to `fired=False`
    against this baseline. Tests then inject the specific deviation
    they want to probe.
    """
    resolved_t = t if t is not None else NOW
    resolved_started = started_at if started_at is not None else resolved_t - timedelta(minutes=10)
    elapsed = (resolved_t - resolved_started).total_seconds()
    return SessionState(
        session_id=SESSION_ID,
        tick_id=tick_id,
        t=resolved_t,
        started_at=resolved_started,
        scheduled_end_at=scheduled_end_at,
        elapsed_sec=elapsed,
        participants=participants if participants is not None else {},
        currently_speaking_count=currently_speaking_count,
        silence_run_sec=silence_run_sec,
        rolling_global_transcript_2min=rolling_global_transcript_2min,
        is_paused=is_paused,
        moderator_muted=moderator_muted,
        quietness_budget=quietness_budget if quietness_budget is not None else QuietnessBudget(),
        study_prompt=study_prompt,
        study_prompt_embedding=study_prompt_embedding,
        rolling_transcript_30s_embedding=rolling_transcript_30s_embedding,
        embedding_model_name=embedding_model_name,
    )
