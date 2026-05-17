"""Pydantic model boundary tests for the canonical domain shapes.

These tests assert the *contract* — required fields, defaults, value
constraints, and frozen/extra-forbidden behaviour. Brief §5 is the
source of truth for the shapes; this file is the executable form of it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
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
from verbio_engine.domain.command import ResearcherCommandType


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# ParticipantState
# ---------------------------------------------------------------------------


def test_participant_state_minimal_construction() -> None:
    # `recent_utterances` and `flags` have factory defaults declared via
    # Field(default_factory=...); pydantic accepts the call at runtime but
    # the pydantic-mypy plugin under init_forbid_extra still flags it.
    state = ParticipantState(  # type: ignore[call-arg]
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
        ParticipantState(  # type: ignore[call-arg]
            participant_id="p-1",
            display_name="Alex",
            joined_at=_now(),
            unknown_field="oops",
        )
    assert "Extra inputs are not permitted" in str(excinfo.value)


def test_participant_state_is_frozen() -> None:
    state = ParticipantState(  # type: ignore[call-arg]
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
        ParticipantState(  # type: ignore[call-arg]
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
        ModeratorDecision(  # type: ignore[call-arg]
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
# ResearcherCommand — typed payload validation (brief §5.4 + Phase 5)
# ---------------------------------------------------------------------------


def _cmd(command_type: ResearcherCommandType, payload: dict[str, Any]) -> ResearcherCommand:
    """Helper — build a ResearcherCommand with the boring fields filled in."""
    return ResearcherCommand(
        command_id=uuid4(),
        session_id=uuid4(),
        researcher_id="r-1",
        issued_at=_now(),
        command_type=command_type,
        payload=payload,
    )


@pytest.mark.parametrize(
    "command_type",
    [
        "mute_moderator",
        "unmute_moderator",
        "pause_session",
        "resume_session",
    ],
)
def test_no_payload_commands_accept_empty_dict(command_type: ResearcherCommandType) -> None:
    # Control-plane toggles take no payload; an empty dict is the canonical wire form.
    cmd = _cmd(command_type, {})
    assert cmd.payload == {}


@pytest.mark.parametrize(
    "command_type",
    [
        "mute_moderator",
        "unmute_moderator",
        "pause_session",
        "resume_session",
    ],
)
def test_no_payload_commands_reject_extra_keys(command_type: ResearcherCommandType) -> None:
    # extra='forbid' on the payload model surfaces typos before they reach the engine.
    with pytest.raises(ValidationError):
        _cmd(command_type, {"unexpected_field": True})


def test_force_prompt_payload_requires_prompt_text() -> None:
    with pytest.raises(ValidationError):
        _cmd("force_prompt", {})


def test_force_prompt_payload_rejects_empty_prompt() -> None:
    with pytest.raises(ValidationError):
        _cmd("force_prompt", {"prompt": ""})


def test_force_prompt_payload_accepts_target_participant() -> None:
    target_id = uuid4()
    cmd = _cmd("force_prompt", {"prompt": "ask Maria why", "target_participant_id": str(target_id)})
    # Wire is JSON: dict survives unchanged; engine handlers parse it typed.
    assert cmd.payload["prompt"] == "ask Maria why"
    assert cmd.payload["target_participant_id"] == str(target_id)


def test_force_redirect_requires_topic() -> None:
    with pytest.raises(ValidationError):
        _cmd("force_redirect", {})

    cmd = _cmd("force_redirect", {"topic": "pricing concerns"})
    assert cmd.payload == {"topic": "pricing concerns"}


def test_force_summary_focus_is_optional() -> None:
    # No focus is valid: summarises the whole discussion.
    assert _cmd("force_summary", {}).payload == {}
    cmd = _cmd("force_summary", {"focus": "the pricing pushback"})
    assert cmd.payload["focus"] == "the pricing pushback"


def test_whisper_requires_text() -> None:
    with pytest.raises(ValidationError):
        _cmd("whisper", {})

    cmd = _cmd("whisper", {"text": "the floor is yours, Maria"})
    assert cmd.payload["text"] == "the floor is yours, Maria"


def test_set_quietness_budget_requires_at_least_one_field() -> None:
    # An empty patch is ambiguous and would silently no-op — reject it.
    with pytest.raises(ValidationError):
        _cmd("set_quietness_budget", {})


def test_set_quietness_budget_accepts_partial_patch() -> None:
    cmd = _cmd("set_quietness_budget", {"max_utterances_per_10min": 5})
    assert cmd.payload == {"max_utterances_per_10min": 5}


def test_set_quietness_budget_rejects_negative_floor() -> None:
    with pytest.raises(ValidationError):
        _cmd("set_quietness_budget", {"min_seconds_between_utterances": -1.0})


def test_set_quietness_budget_rejects_zero_max_utterances() -> None:
    # Mirrors QuietnessBudget's PositiveInt — zero would silence the moderator
    # forever and is better expressed via mute_moderator.
    with pytest.raises(ValidationError):
        _cmd("set_quietness_budget", {"max_utterances_per_10min": 0})


def test_flag_moment_note_is_optional() -> None:
    assert _cmd("flag_moment", {}).payload == {}
    cmd = _cmd("flag_moment", {"note": "interesting reaction here"})
    assert cmd.payload["note"] == "interesting reaction here"


def test_end_session_reason_is_optional() -> None:
    assert _cmd("end_session", {}).payload == {}
    cmd = _cmd("end_session", {"reason": "researcher ended early"})
    assert cmd.payload["reason"] == "researcher ended early"


def test_command_is_frozen() -> None:
    # ResearcherCommand carries a researcher_id; making it mutable would be
    # an audit-trail hole. Confirm the frozen marker on model_config holds.
    cmd = _cmd("pause_session", {})
    with pytest.raises(ValidationError):
        cmd.researcher_id = "r-2"  # type: ignore[misc]


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
