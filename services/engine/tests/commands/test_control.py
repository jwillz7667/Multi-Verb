"""Unit tests for `commands.control` (P5 L5).

Covers `apply_control_commands` in isolation: a fake `RuntimeControl`
records every call so the test can assert order, idempotency, and
batch-composition semantics without dragging in the full runtime.

The listener-level wiring (effects → suppressed_by, dispatch gate) is
covered by `tests/decisions/test_listener.py`; this module stays
narrowly focused on the pure command-walking logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from verbio_engine.commands import (
    CONTROL_COMMAND_TYPES,
    ControlEffects,
    apply_control_commands,
)
from verbio_engine.domain.command import ResearcherCommand, ResearcherCommandType


@dataclass(slots=True)
class _FakeRuntimeControl:
    """Records every RuntimeControl call so tests can replay the order."""

    calls: list[tuple[str, object]] = field(default_factory=list)
    end_reason: str | None = None

    def set_muted(self, *, muted: bool) -> None:
        self.calls.append(("set_muted", muted))

    def set_pause(self, *, paused: bool) -> None:
        self.calls.append(("set_pause", paused))

    async def request_end_session(self, *, reason: str | None) -> None:
        self.calls.append(("request_end_session", reason))
        self.end_reason = reason


def _command(
    command_type: ResearcherCommandType,
    *,
    payload: dict[str, object] | None = None,
) -> ResearcherCommand:
    return ResearcherCommand(
        command_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        researcher_id=str(uuid.uuid4()),
        issued_at=datetime.now(UTC),
        command_type=command_type,
        payload=payload if payload is not None else {},
    )


class TestControlCommandTypes:
    """The frozen set of command types this module owns."""

    def test_set_membership_matches_brief(self) -> None:
        # Brief §5.4: mute/unmute, pause/resume, end_session are the
        # non-spoken control plane. set_quietness_budget is its own
        # layer (L6); flag_moment is L7.
        assert (
            frozenset(
                {
                    "mute_moderator",
                    "unmute_moderator",
                    "pause_session",
                    "resume_session",
                    "end_session",
                },
            )
            == CONTROL_COMMAND_TYPES
        )

    def test_set_quietness_budget_excluded(self) -> None:
        # Sanity-check the boundary against P5 L6's scope.
        assert "set_quietness_budget" not in CONTROL_COMMAND_TYPES

    def test_flag_moment_excluded(self) -> None:
        # P5 L7 owns flag_moment — the listener already persists the
        # audit row, but it has no runtime effect.
        assert "flag_moment" not in CONTROL_COMMAND_TYPES

    def test_spoken_command_types_excluded(self) -> None:
        # force_* and whisper produce decisions; they're handled by
        # the override path, not the control plane.
        assert "force_prompt" not in CONTROL_COMMAND_TYPES
        assert "force_redirect" not in CONTROL_COMMAND_TYPES
        assert "force_summary" not in CONTROL_COMMAND_TYPES
        assert "whisper" not in CONTROL_COMMAND_TYPES


class TestApplyControlCommands:
    """End-to-end batch walking semantics."""

    async def test_empty_batch_returns_initial_state(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects == ControlEffects(
            muted_after=False,
            paused_after=False,
            end_requested=False,
        )
        assert control.calls == []

    async def test_empty_batch_preserves_initial_muted_true(self) -> None:
        # Critical: a tick with no batch but a pre-existing mute must
        # still report muted_after=True so the listener's gate fires.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[],
            control=control,
            initial_muted=True,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert effects.paused_after is False
        assert control.calls == []

    async def test_non_control_commands_are_skipped(self) -> None:
        # force_prompt / whisper / set_quietness_budget / flag_moment
        # are owned by other layers; this walker must pass them by
        # silently rather than erroring.
        control = _FakeRuntimeControl()
        commands = [
            _command("force_prompt", payload={"prompt": "ask Maria"}),
            _command("whisper", payload={"text": "hello"}),
            _command("set_quietness_budget", payload={"max_utterances_per_10min": 2}),
            _command("flag_moment", payload={"note": "interesting"}),
        ]
        effects = await apply_control_commands(
            commands=commands,
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.counts == {}
        assert control.calls == []
        assert effects.muted_after is False

    async def test_mute_moderator_flips_state_and_calls_control(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("mute_moderator")],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert effects.paused_after is False
        assert effects.end_requested is False
        assert control.calls == [("set_muted", True)]
        assert effects.counts == {"mute_moderator": 1}

    async def test_unmute_moderator_flips_state_back(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("unmute_moderator")],
            control=control,
            initial_muted=True,
            initial_paused=False,
        )
        assert effects.muted_after is False
        assert control.calls == [("set_muted", False)]

    async def test_pause_session_sets_paused_true(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("pause_session")],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.paused_after is True
        assert control.calls == [("set_pause", True)]

    async def test_resume_session_clears_paused(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("resume_session")],
            control=control,
            initial_muted=False,
            initial_paused=True,
        )
        assert effects.paused_after is False
        assert control.calls == [("set_pause", False)]

    async def test_end_session_marks_end_requested_and_awaits_runtime(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("end_session", payload={"reason": "wrapped up"})],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.end_requested is True
        assert control.calls == [("request_end_session", "wrapped up")]
        assert control.end_reason == "wrapped up"

    async def test_end_session_with_no_reason_passes_none(self) -> None:
        # The EndSessionPayload allows reason=None — the runtime
        # should still be told to end, just without a reason string.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("end_session", payload={})],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.end_requested is True
        assert control.calls == [("request_end_session", None)]


class TestBatchOrdering:
    """FIFO composition when multiple control commands land in one batch."""

    async def test_mute_then_unmute_ends_unmuted(self) -> None:
        # Researcher double-tap: mute, then immediately unmute in the
        # same drain. Final state is unmuted.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("mute_moderator"), _command("unmute_moderator")],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is False
        # Both control calls fire in order — the runtime gets each
        # event, not just the net change.
        assert control.calls == [("set_muted", True), ("set_muted", False)]
        assert effects.counts == {"mute_moderator": 1, "unmute_moderator": 1}

    async def test_unmute_then_mute_ends_muted(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("unmute_moderator"), _command("mute_moderator")],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert control.calls == [("set_muted", False), ("set_muted", True)]

    async def test_pause_then_mute_sets_both(self) -> None:
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[_command("pause_session"), _command("mute_moderator")],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert effects.paused_after is True
        assert control.calls == [("set_pause", True), ("set_muted", True)]

    async def test_end_session_does_not_short_circuit_earlier_commands(self) -> None:
        # The runtime should still see the mute event even when
        # end_session is in the same batch — auditable intent matters.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[
                _command("mute_moderator"),
                _command("end_session", payload={"reason": "done"}),
            ],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert effects.end_requested is True
        # set_muted runs inline; request_end_session is awaited at the
        # end of the walk, so it lands last in the call log.
        assert control.calls == [
            ("set_muted", True),
            ("request_end_session", "done"),
        ]

    async def test_end_session_runs_after_later_commands_in_same_batch(self) -> None:
        # The contract is "end_session is awaited at the END of the
        # walk", not "first". This protects against re-ordering
        # ambiguity for a researcher who hit End first then realised
        # they wanted to mute first.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[
                _command("end_session", payload={}),
                _command("pause_session"),
            ],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.paused_after is True
        # set_pause runs inline; request_end_session lands last.
        assert control.calls == [
            ("set_pause", True),
            ("request_end_session", None),
        ]

    async def test_multiple_end_sessions_in_same_batch_call_runtime_once(self) -> None:
        # A UI bug or double-click could send two end_sessions; we
        # only ask the runtime to end once. The audit log still has
        # both rows (the listener persists each).
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[
                _command("end_session", payload={"reason": "first"}),
                _command("end_session", payload={"reason": "second"}),
            ],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.end_requested is True
        assert effects.counts == {"end_session": 2}
        # Only one request_end_session call — the LAST reason wins
        # (overwritten on the second iteration before the awaited call).
        end_calls = [c for c in control.calls if c[0] == "request_end_session"]
        assert len(end_calls) == 1
        assert end_calls[0] == ("request_end_session", "second")

    async def test_mix_of_control_and_non_control_walks_only_control(self) -> None:
        # The walker silently passes over force_prompt / whisper /
        # budget / flag commands. Only control entries hit the runtime.
        control = _FakeRuntimeControl()
        effects = await apply_control_commands(
            commands=[
                _command("force_prompt", payload={"prompt": "ask"}),
                _command("mute_moderator"),
                _command("flag_moment", payload={"note": "x"}),
                _command("pause_session"),
            ],
            control=control,
            initial_muted=False,
            initial_paused=False,
        )
        assert effects.muted_after is True
        assert effects.paused_after is True
        assert control.calls == [("set_muted", True), ("set_pause", True)]
        assert effects.counts == {"mute_moderator": 1, "pause_session": 1}


class TestEndSessionPayloadValidation:
    """The walker re-validates EndSessionPayload defensively."""

    async def test_end_session_with_empty_reason_string_rejected(self) -> None:
        # EndSessionPayload requires reason to be either None or a
        # min_length=1 string. An empty string is the kind of thing a
        # UI bug could send; the bus's per-command validator should
        # already have caught it, but this walker validates again so
        # any drift is surfaced at the call site rather than leaking
        # into the runtime as a silently-stored bad value.
        control = _FakeRuntimeControl()
        # Bypass ResearcherCommand's own validator by constructing the
        # raw command with a payload that the EndSessionPayload would
        # reject — we do that by issuing a valid one then mutating
        # via model_copy on the underlying field. Easier: rely on
        # ResearcherCommand.model_validate failing too, which is the
        # expected behaviour and demonstrates the layered defence.
        with pytest.raises(ValueError, match="invalid payload"):
            ResearcherCommand(
                command_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                researcher_id=str(uuid.uuid4()),
                issued_at=datetime.now(UTC),
                command_type="end_session",
                payload={"reason": ""},
            )
        # And on the path where ResearcherCommand somehow passed an
        # invalid payload (e.g., extra key), the walker also fails:
        cmd = _command("end_session", payload={})
        # Mutate payload past Pydantic's validation by reaching into
        # the immutable frozen field via __dict__ — only possible
        # because frozen=True in Pydantic uses __setattr__, not
        # __dict__-level locking.
        cmd.__dict__["payload"] = {"unknown_field": "value"}
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            await apply_control_commands(
                commands=[cmd],
                control=control,
                initial_muted=False,
                initial_paused=False,
            )
