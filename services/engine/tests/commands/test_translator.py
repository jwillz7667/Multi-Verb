"""Unit tests for the `ResearcherCommand → ModeratorDecision` translator (P5 L3).

The translator is the pure mapping between the spoken-command wire shape
and the engine's `ModeratorDecision`. These tests pin:

  * Which command_types qualify as "spoken" (and which do not).
  * FIFO selection when multiple spoken commands land in one drained batch.
  * Per-command-type translation: action, researcher_hint, target.
  * Decision metadata that distinguishes a manual override from an
    auto decision (source, confidence, suppressed_by, triggering_rule).
  * The 3-second manual cooldown (debounce floor) and how it propagates
    to `decision.cooldown_until`.
  * Defensive ValueError for command_types the translator doesn't speak.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from verbio_engine.commands.translator import (
    MANUAL_COOLDOWN_SEC,
    SPOKEN_COMMAND_TYPES,
    build_manual_decision,
    first_spoken_command,
)
from verbio_engine.domain.command import ResearcherCommand
from verbio_engine.domain.session_state import QuietnessBudget, SessionState

SESSION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TICK_T = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _state(*, tick_id: int = 7) -> SessionState:
    return SessionState(
        session_id=SESSION_ID,
        tick_id=tick_id,
        t=TICK_T,
        started_at=TICK_T - timedelta(minutes=2),
        elapsed_sec=120.0,
        participants={},
        currently_speaking_count=0,
        silence_run_sec=0.0,
        quietness_budget=QuietnessBudget(),
    )


def _cmd(
    *,
    command_type: str,
    payload: dict[str, object] | None = None,
    researcher_id: str | None = None,
) -> ResearcherCommand:
    return ResearcherCommand(
        command_id=uuid.uuid4(),
        session_id=SESSION_ID,
        researcher_id=researcher_id or str(uuid.uuid4()),
        issued_at=TICK_T,
        command_type=command_type,  # type: ignore[arg-type]
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# SPOKEN_COMMAND_TYPES surface
# ---------------------------------------------------------------------------


def test_spoken_command_types_exact_membership() -> None:
    # Pin the exact set so an accidental include of `whisper`
    # (handled by P5 L4) shows up as a regression here.
    assert frozenset({"force_prompt", "force_redirect", "force_summary"}) == SPOKEN_COMMAND_TYPES


def test_manual_cooldown_stays_below_quietness_budget_floor() -> None:
    # Manual overrides bypass the quietness budget — the cooldown is a
    # debounce against UI double-presses, not throttling. If
    # MANUAL_COOLDOWN_SEC ever climbs to the budget floor, researchers
    # lose the responsiveness that's the whole reason for the override.
    assert QuietnessBudget().min_seconds_between_utterances > MANUAL_COOLDOWN_SEC


# ---------------------------------------------------------------------------
# first_spoken_command
# ---------------------------------------------------------------------------


class TestFirstSpokenCommand:
    def test_empty_input_returns_none(self) -> None:
        assert first_spoken_command([]) is None

    def test_no_spoken_in_batch_returns_none(self) -> None:
        batch = [
            _cmd(command_type="flag_moment"),
            _cmd(command_type="mute_moderator"),
            _cmd(command_type="set_quietness_budget", payload={"max_utterances_per_10min": 2}),
        ]
        assert first_spoken_command(batch) is None

    def test_returns_first_spoken_when_present(self) -> None:
        flag = _cmd(command_type="flag_moment")
        first_spoken = _cmd(command_type="force_prompt", payload={"prompt": "first"})
        second_spoken = _cmd(command_type="force_redirect", payload={"topic": "second"})
        result = first_spoken_command([flag, first_spoken, second_spoken])
        assert result is first_spoken

    def test_fifo_ordering_respects_input_order(self) -> None:
        # Brief §11.3 — the bus is ordered; the FIRST spoken wins.
        early = _cmd(command_type="force_redirect", payload={"topic": "early"})
        late = _cmd(command_type="force_summary", payload={})
        assert first_spoken_command([early, late]) is early
        assert first_spoken_command([late, early]) is late


# ---------------------------------------------------------------------------
# build_manual_decision: per-command-type translation
# ---------------------------------------------------------------------------


class TestBuildManualDecisionForcePrompt:
    def test_basic_force_prompt_translates_to_prompt_participant(self) -> None:
        decision_id = uuid.uuid4()
        cmd = _cmd(
            command_type="force_prompt",
            payload={"prompt": "ask Maria for her take on the price"},
        )
        state = _state(tick_id=3)

        decision = build_manual_decision(
            command=cmd,
            state=state,
            t=TICK_T,
            decision_id=decision_id,
        )

        assert decision.decision_id == decision_id
        assert decision.session_id == SESSION_ID
        assert decision.tick_id == 3
        assert decision.timestamp == TICK_T
        assert decision.action == "prompt_participant"
        assert decision.source == "researcher_manual"
        assert decision.triggering_rule is None
        assert decision.researcher_id == cmd.researcher_id
        assert decision.researcher_hint == "ask Maria for her take on the price"
        assert decision.reason_codes == ["researcher_command:force_prompt"]
        assert decision.reason_human == ""
        assert decision.confidence == pytest.approx(1.0)
        assert decision.suppressed_by == []
        assert decision.was_executed is False
        assert decision.llm_prompt is None
        assert decision.llm_output is None
        assert decision.tts_audio_url is None
        assert decision.spoken_at is None
        # 3s cooldown from `t`.
        assert decision.cooldown_until == TICK_T + timedelta(seconds=MANUAL_COOLDOWN_SEC)

    def test_target_participant_id_passes_through_when_provided(self) -> None:
        # The web producer ships a UUID string; the decision's
        # target_participant_id is also a string (LiveKit identity
        # convention). Round-trip should preserve the value.
        target = uuid.uuid4()
        cmd = _cmd(
            command_type="force_prompt",
            payload={"prompt": "...", "target_participant_id": str(target)},
        )
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=uuid.uuid4(),
        )
        assert decision.target_participant_id == str(target)

    def test_missing_target_participant_id_translates_to_none(self) -> None:
        cmd = _cmd(command_type="force_prompt", payload={"prompt": "no target"})
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=uuid.uuid4(),
        )
        assert decision.target_participant_id is None


class TestBuildManualDecisionForceRedirect:
    def test_force_redirect_uses_topic_as_hint(self) -> None:
        cmd = _cmd(
            command_type="force_redirect",
            payload={"topic": "let's circle back to the price point"},
        )
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=uuid.uuid4(),
        )
        assert decision.action == "redirect_topic"
        assert decision.researcher_hint == "let's circle back to the price point"
        # Redirects don't carry a target — they steer the whole group.
        assert decision.target_participant_id is None
        assert decision.reason_codes == ["researcher_command:force_redirect"]


class TestBuildManualDecisionForceSummary:
    def test_force_summary_with_focus(self) -> None:
        cmd = _cmd(
            command_type="force_summary",
            payload={"focus": "what we've heard about pricing concerns"},
        )
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=uuid.uuid4(),
        )
        assert decision.action == "summarize_thread"
        assert decision.researcher_hint == "what we've heard about pricing concerns"
        assert decision.reason_codes == ["researcher_command:force_summary"]

    def test_force_summary_without_focus_passes_through_none(self) -> None:
        # `focus` is optional. Absence means "summarise the full thread"
        # — the translator forwards None so the mouth layer can render
        # an unfocused recap.
        cmd = _cmd(command_type="force_summary", payload={})
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=uuid.uuid4(),
        )
        assert decision.action == "summarize_thread"
        assert decision.researcher_hint is None


# ---------------------------------------------------------------------------
# build_manual_decision: invariants + defensive paths
# ---------------------------------------------------------------------------


class TestBuildManualDecisionInvariants:
    def test_cooldown_until_is_t_plus_manual_cooldown(self) -> None:
        cmd = _cmd(command_type="force_redirect", payload={"topic": "anything"})
        custom_t = TICK_T + timedelta(seconds=42)
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=custom_t,
            decision_id=uuid.uuid4(),
        )
        assert decision.cooldown_until == custom_t + timedelta(seconds=MANUAL_COOLDOWN_SEC)

    def test_decision_id_is_caller_supplied_not_minted(self) -> None:
        # The listener reuses the resolver's tick decision_id; the
        # translator must honour it so per-rule evaluations stay FK-linked.
        caller_id = uuid.UUID("12345678-1234-4234-8234-123456789012")
        cmd = _cmd(command_type="force_prompt", payload={"prompt": "x"})
        decision = build_manual_decision(
            command=cmd,
            state=_state(),
            t=TICK_T,
            decision_id=caller_id,
        )
        assert decision.decision_id == caller_id

    def test_unspoken_command_type_raises_value_error(self) -> None:
        # `first_spoken_command` already guards the listener's call
        # site, but a defensive ValueError keeps the translator safe
        # to call directly from a future caller without a silent miss.
        cmd = _cmd(command_type="mute_moderator", payload={})
        with pytest.raises(ValueError, match="does not translate to a ModeratorDecision"):
            build_manual_decision(
                command=cmd,
                state=_state(),
                t=TICK_T,
                decision_id=uuid.uuid4(),
            )
