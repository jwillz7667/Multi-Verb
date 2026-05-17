"""Unit tests for `commands.budget` (P5 L6).

Covers `apply_budget_commands` in isolation: a fake `BudgetControl`
records every replacement so the test can assert FIFO merge order,
field-level preservation, and bookkeeping-passthrough semantics
without touching the state store.

The listener-level wiring (commands → store → next-tick snapshot)
is covered by `tests/decisions/test_listener.py`; this module stays
narrowly focused on the pure batch-walking logic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from verbio_engine.commands import (
    BUDGET_COMMAND_TYPES,
    BudgetEffects,
    apply_budget_commands,
)
from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.domain.command import ResearcherCommand, ResearcherCommandType


@dataclass(slots=True)
class _FakeBudgetControl:
    """Records every `update_quietness_budget` call for assertion."""

    calls: list[QuietnessBudget] = field(default_factory=list)

    def update_quietness_budget(self, budget: QuietnessBudget) -> None:
        self.calls.append(budget)


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


class TestBudgetCommandTypes:
    """The frozen set of command types this module owns."""

    def test_set_membership_is_singleton(self) -> None:
        # Brief §5.4: set_quietness_budget is its own layer (P5 L6)
        # because the payload has structured merge semantics — it
        # does not live with the boolean control plane (L5).
        assert frozenset({"set_quietness_budget"}) == BUDGET_COMMAND_TYPES

    def test_control_command_types_excluded(self) -> None:
        for command_type in (
            "mute_moderator",
            "unmute_moderator",
            "pause_session",
            "resume_session",
            "end_session",
        ):
            assert command_type not in BUDGET_COMMAND_TYPES

    def test_spoken_and_flag_command_types_excluded(self) -> None:
        for command_type in (
            "force_prompt",
            "force_redirect",
            "force_summary",
            "whisper",
            "flag_moment",
        ):
            assert command_type not in BUDGET_COMMAND_TYPES


class TestApplyBudgetCommands:
    """End-to-end batch walking semantics."""

    def test_empty_batch_returns_initial_budget_unchanged(self) -> None:
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
        )
        effects = apply_budget_commands(
            commands=[],
            control=control,
            initial_budget=initial,
        )
        assert effects == BudgetEffects(budget_after=initial)
        assert effects.applied is False
        assert effects.applied_count == 0
        assert control.calls == []

    def test_non_budget_commands_are_skipped(self) -> None:
        # mute/pause/end/force/whisper/flag are owned by other layers.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=5,
            min_seconds_between_utterances=20.0,
        )
        commands = [
            _command("mute_moderator"),
            _command("force_prompt", payload={"prompt": "ask Maria"}),
            _command("whisper", payload={"text": "hello"}),
            _command("pause_session"),
            _command("flag_moment", payload={"note": "interesting"}),
            _command("end_session", payload={}),
        ]
        effects = apply_budget_commands(
            commands=commands,
            control=control,
            initial_budget=initial,
        )
        assert effects.applied is False
        assert effects.applied_count == 0
        assert effects.budget_after == initial
        assert effects.counts == {}
        assert control.calls == []

    def test_single_full_patch_replaces_both_limits(self) -> None:
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={
                        "max_utterances_per_10min": 5,
                        "min_seconds_between_utterances": 10.0,
                    },
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.applied is True
        assert effects.applied_count == 1
        assert effects.budget_after.max_utterances_per_10min == 5
        assert effects.budget_after.min_seconds_between_utterances == 10.0
        assert effects.counts == {"set_quietness_budget": 1}
        assert control.calls == [effects.budget_after]

    def test_partial_patch_only_max_preserves_min(self) -> None:
        # Unset payload fields must keep the running value untouched.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=45.0,
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 7},
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after.max_utterances_per_10min == 7
        assert effects.budget_after.min_seconds_between_utterances == 45.0
        assert control.calls == [effects.budget_after]

    def test_partial_patch_only_min_preserves_max(self) -> None:
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=4,
            min_seconds_between_utterances=20.0,
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={"min_seconds_between_utterances": 5.0},
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after.max_utterances_per_10min == 4
        assert effects.budget_after.min_seconds_between_utterances == 5.0


class TestBatchOrdering:
    """FIFO composition when multiple budget patches land in one batch."""

    def test_two_patches_compose_per_field(self) -> None:
        # Docstring example: patch#1 sets max, patch#2 sets min; both
        # carry forward, bookkeeping untouched.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
            current_window_count=2,
            last_utterance_at=datetime(2026, 5, 17, 11, 30, 0, tzinfo=UTC),
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 5},
                ),
                _command(
                    "set_quietness_budget",
                    payload={"min_seconds_between_utterances": 10.0},
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.applied_count == 2
        assert effects.counts == {"set_quietness_budget": 2}
        assert effects.budget_after.max_utterances_per_10min == 5
        assert effects.budget_after.min_seconds_between_utterances == 10.0
        # Runtime is called exactly ONCE per batch with the FINAL
        # budget — intermediate budgets are local-only.
        assert control.calls == [effects.budget_after]

    def test_later_patch_overrides_earlier_on_same_field(self) -> None:
        # Researcher slammed the slider twice in 500ms; the second
        # value wins.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 5},
                ),
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 8},
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after.max_utterances_per_10min == 8
        assert effects.budget_after.min_seconds_between_utterances == 30.0
        assert control.calls == [effects.budget_after]
        assert len(control.calls) == 1

    def test_mix_of_budget_and_non_budget_walks_only_budget(self) -> None:
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
        )
        effects = apply_budget_commands(
            commands=[
                _command("mute_moderator"),
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 6},
                ),
                _command("flag_moment", payload={"note": "x"}),
                _command(
                    "set_quietness_budget",
                    payload={"min_seconds_between_utterances": 15.0},
                ),
                _command("force_prompt", payload={"prompt": "ask"}),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.applied_count == 2
        assert effects.budget_after.max_utterances_per_10min == 6
        assert effects.budget_after.min_seconds_between_utterances == 15.0


class TestBookkeepingPreservation:
    """Researchers configure the limit; the runtime owns the counter."""

    def test_current_window_count_carried_through(self) -> None:
        # Even when the patch reshapes both limit fields, the
        # bookkeeping fields stay verbatim — the resolver's window
        # accounting cannot reset just because a slider moved.
        control = _FakeBudgetControl()
        last_ts = datetime(2026, 5, 17, 11, 30, 0, tzinfo=UTC)
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
            current_window_count=2,
            last_utterance_at=last_ts,
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={
                        "max_utterances_per_10min": 9,
                        "min_seconds_between_utterances": 5.0,
                    },
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after.current_window_count == 2
        assert effects.budget_after.last_utterance_at == last_ts

    def test_lowering_cap_below_current_count_does_not_reset(self) -> None:
        # "Lower the cap and the moderator clamps immediately"
        # semantics: the count is left over the cap; the resolver's
        # `_budget_blocks` predicate returns True until the rolling
        # window expires the over-the-cap utterances.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=5,
            min_seconds_between_utterances=30.0,
            current_window_count=4,
            last_utterance_at=datetime(2026, 5, 17, 11, 30, 0, tzinfo=UTC),
        )
        effects = apply_budget_commands(
            commands=[
                _command(
                    "set_quietness_budget",
                    payload={"max_utterances_per_10min": 2},
                ),
            ],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after.max_utterances_per_10min == 2
        assert effects.budget_after.current_window_count == 4

    def test_payload_cannot_overwrite_bookkeeping_fields(self) -> None:
        # The wire schema for `SetQuietnessBudgetPayload` only exposes
        # the two limit fields. If a payload tries to slip in a
        # bookkeeping field, the payload-level validation rejects it
        # (forbid extra), so by the time we walk the batch the
        # bookkeeping is necessarily the runtime's own value.
        control = _FakeBudgetControl()
        initial = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
            current_window_count=1,
        )
        with pytest.raises(ValueError, match="invalid payload"):
            # ResearcherCommand validates the payload at construction
            # time via the typed payload model.
            ResearcherCommand(
                command_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                researcher_id=str(uuid.uuid4()),
                issued_at=datetime.now(UTC),
                command_type="set_quietness_budget",
                payload={"current_window_count": 99},
            )
        # Sanity: no state mutation should have happened either way.
        effects = apply_budget_commands(
            commands=[],
            control=control,
            initial_budget=initial,
        )
        assert effects.budget_after == initial
        assert control.calls == []
