"""Pydantic model boundary tests for the canonical domain shapes.

These tests assert the *contract* — required fields, defaults, value
constraints, and frozen/extra-forbidden behaviour. Brief §5 is the
source of truth for the shapes; this file is the executable form of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from verbio_engine.domain import (
    ModeratorDecision,
    ParticipantFlags,
    ParticipantState,
    QuietnessBudget,
    ResearcherCommand,
    RuleEvaluation,
    UtteranceRef,
)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# ParticipantState
# ---------------------------------------------------------------------------


def test_participant_state_minimal_construction() -> None:
    state = ParticipantState(
        participant_id="p-1",
        display_name="Alex",
        joined_at=_now(),
    )

    assert state.speaking_time_total_sec == 0.0
    assert state.turn_count == 0
    assert state.recent_utterances == []
    assert state.flags == ParticipantFlags()


def test_participant_state_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError) as excinfo:
        ParticipantState(
            participant_id="p-1",
            display_name="Alex",
            joined_at=_now(),
            unknown_field="oops",
        )
    assert "Extra inputs are not permitted" in str(excinfo.value)


def test_participant_state_is_frozen() -> None:
    state = ParticipantState(
        participant_id="p-1",
        display_name="Alex",
        joined_at=_now(),
    )
    with pytest.raises(ValidationError):
        state.turn_count = 5  # type: ignore[misc]


def test_recent_utterances_capped_at_five() -> None:
    base = _now()
    refs = [
        UtteranceRef(
            utterance_id=f"u-{i}",
            text=f"hi {i}",
            spoken_at=base + timedelta(seconds=i),
            duration_sec=1.0,
        )
        for i in range(6)
    ]
    with pytest.raises(ValidationError):
        ParticipantState(
            participant_id="p-1",
            display_name="Alex",
            joined_at=base,
            recent_utterances=refs,
        )


def test_fair_share_pct_must_be_within_0_to_100() -> None:
    with pytest.raises(ValidationError):
        ParticipantState(
            participant_id="p-1",
            display_name="Alex",
            joined_at=_now(),
            fair_share_pct=120.0,
        )


# ---------------------------------------------------------------------------
# ModeratorDecision
# ---------------------------------------------------------------------------


def test_moderator_decision_requires_cooldown_until() -> None:
    with pytest.raises(ValidationError):
        ModeratorDecision(
            decision_id=uuid4(),
            session_id=uuid4(),
            tick_id=0,
            timestamp=_now(),
            action="stay_silent",
            source="auto",
        )


def test_moderator_decision_silent_default_is_audit_complete() -> None:
    decision = ModeratorDecision(
        decision_id=uuid4(),
        session_id=uuid4(),
        tick_id=42,
        timestamp=_now(),
        action="stay_silent",
        source="auto",
        cooldown_until=_now() + timedelta(seconds=45),
    )

    assert decision.was_executed is False
    assert decision.suppressed_by == []
    assert decision.reason_codes == []
    assert decision.llm_prompt is None


def test_moderator_decision_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        ModeratorDecision(
            decision_id=uuid4(),
            session_id=uuid4(),
            tick_id=0,
            timestamp=_now(),
            action="stay_silent",
            source="auto",
            confidence=1.5,
            cooldown_until=_now(),
        )


# ---------------------------------------------------------------------------
# RuleEvaluation
# ---------------------------------------------------------------------------


def test_rule_evaluation_records_unfired_rules() -> None:
    eval_row = RuleEvaluation(
        evaluation_id=uuid4(),
        decision_id=uuid4(),
        rule_name="silence_gap",
        rule_version="1.0.0",
        fired=False,
        suppressed_reason="cooldown",
    )

    assert eval_row.fired is False
    assert eval_row.confidence == 0.0
    assert eval_row.predicate_inputs == {}


# ---------------------------------------------------------------------------
# ResearcherCommand
# ---------------------------------------------------------------------------


def test_researcher_command_rejects_unknown_command_type() -> None:
    with pytest.raises(ValidationError):
        ResearcherCommand(
            command_id=uuid4(),
            session_id=uuid4(),
            researcher_id="r-1",
            issued_at=_now(),
            command_type="not_a_command",  # type: ignore[arg-type]
            payload={},
        )


def test_researcher_command_payload_defaults_empty() -> None:
    cmd = ResearcherCommand(
        command_id=uuid4(),
        session_id=uuid4(),
        researcher_id="r-1",
        issued_at=_now(),
        command_type="pause_session",
    )

    assert cmd.payload == {}


# ---------------------------------------------------------------------------
# QuietnessBudget
# ---------------------------------------------------------------------------


def test_quietness_budget_defaults_match_brief() -> None:
    budget = QuietnessBudget()

    assert budget.max_utterances_per_10min == 3
    assert budget.min_seconds_between_utterances == 30.0
    assert budget.current_window_count == 0
    assert budget.last_utterance_at is None


def test_quietness_budget_rejects_zero_max() -> None:
    with pytest.raises(ValidationError):
        QuietnessBudget(max_utterances_per_10min=0)
