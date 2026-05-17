"""`set_quietness_budget` — live runtime patches to the QuietnessBudget (P5 L6).

Sister module to `commands.control`. Control commands flip simple
boolean flags (`mute`, `pause`) or trigger terminal effects
(`end_session`); `set_quietness_budget` mutates a structured object —
the per-session `QuietnessBudget` — by merging the command payload onto
the current snapshot. That structural shape is why it lives in its own
walker rather than piggy-backing on `apply_control_commands`.

How a batch composes
--------------------
Multiple budget patches in the same drain compose in FIFO order. Each
payload is layered onto the running budget; unspecified payload fields
keep the running value untouched. The bookkeeping fields the resolver
relies on (`current_window_count`, `last_utterance_at`) are preserved
verbatim — researchers configure the *limit*, the runtime owns the
*counter*. Two patches in one batch therefore compose as:

  initial:    max=3, min=30.0,  count=2, last=11:30:00
  patch #1:   max=5
  patch #2:               min=10.0
  final:      max=5, min=10.0,  count=2, last=11:30:00

When `current_window_count` would already exceed a freshly lowered cap,
we leave the counter alone. The resolver's `_budget_blocks` predicate
will return True until the rolling window expires the over-the-cap
utterances; this is the desired "lower the cap and the moderator
clamps immediately" semantics.

Why apply ordering matters for THIS tick vs. NEXT tick
------------------------------------------------------
The listener calls `resolve()` BEFORE this walker runs (commands are
drained at the top of the tick, but the resolver consumes the snapshot
projected at tick boundary, which already pre-dates the drain). So a
budget patch issued at tick T applies to tick T+1 onwards. The audit
row for T records the patch (via the listener's `_persist_commands`
write) so the researcher's intent is logged at T even though the
resolver's gate at T runs against the older budget. Pairing the patch
with `mute_moderator` is the way to enforce silence within the *same*
tick when researchers need an immediate clamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, get_args

from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.domain.command import (
    ResearcherCommandType,
    SetQuietnessBudgetPayload,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from verbio_engine.domain.command import ResearcherCommand


BUDGET_COMMAND_TYPES: Final[frozenset[ResearcherCommandType]] = frozenset(
    {"set_quietness_budget"},
)
"""Command types this module owns.

Intentionally a one-element set rather than a bare literal: the
listener iterates a few of these "what does this batch want to do?"
sets, and a frozenset lets it ask "is this a budget command?" with the
same membership-test pattern as `CONTROL_COMMAND_TYPES` and
`SPOKEN_COMMAND_TYPES`.
"""


_VALID_COMMAND_TYPES: Final[frozenset[str]] = frozenset(get_args(ResearcherCommandType))
assert (
    BUDGET_COMMAND_TYPES <= _VALID_COMMAND_TYPES
), "BUDGET_COMMAND_TYPES drifted from ResearcherCommandType"


class BudgetControl(Protocol):
    """Live control surface for replacing the active QuietnessBudget.

    A narrow Protocol so the walker can be unit-tested with a tiny fake
    without dragging in the full runtime. The concrete implementation
    is `SessionRuntime.update_quietness_budget`, which delegates to the
    state store; both layers exist mostly so the listener can be
    composed against either a fake (tests) or the real runtime.

    Idempotent: calling with an equal budget is a no-op from the
    runtime's point of view (the store just stamps the same value).
    """

    def update_quietness_budget(self, budget: QuietnessBudget) -> None:
        """Replace the live QuietnessBudget. Synchronous."""
        ...


@dataclass(slots=True, frozen=True)
class BudgetEffects:
    """Aggregate of what the budget batch did to the runtime.

    Returned by `apply_budget_commands` and consumed by the listener
    mostly for structured logging — there is no dispatch gate equivalent
    of `ControlEffects.muted_after` because budget patches apply to the
    NEXT tick, not the current one. (See module docstring.)

    Attributes:
        budget_after: Final budget after walking the batch. Equal to
            `initial_budget` when no patch landed.
        applied_count: Number of `set_quietness_budget` commands the
            walker actually merged onto the running budget. Useful for
            "researcher hammered the slider" detection in logs.
        applied: Convenience flag — True iff `applied_count > 0`. The
            listener uses this to skip the no-op runtime call when the
            batch had no budget patches at all.
    """

    budget_after: QuietnessBudget
    applied_count: int = 0
    applied: bool = False
    counts: dict[ResearcherCommandType, int] = field(default_factory=dict)


def apply_budget_commands(
    *,
    commands: Iterable[ResearcherCommand],
    control: BudgetControl,
    initial_budget: QuietnessBudget,
) -> BudgetEffects:
    """Walk the batch in FIFO order, merging each payload onto the running budget.

    Non-budget commands (`force_*`, `whisper`, `mute_*`, `pause_*`,
    `end_session`, `flag_moment`) are skipped — they're owned by other
    handlers. `initial_budget` is typically `state.quietness_budget`
    from the snapshot the listener received.

    Field-level merge: an unset payload field leaves the running value
    untouched, so a payload that only sets `max_utterances_per_10min`
    preserves the existing `min_seconds_between_utterances`. The
    bookkeeping fields (`current_window_count`, `last_utterance_at`)
    are always carried forward — researchers configure the limit; the
    runtime owns the counter.

    The runtime is told only the FINAL budget (one call per batch), not
    one call per patch. The intermediate budgets exist only inside this
    function. The audit trail captures the full per-command sequence
    via the listener's `_persist_commands` write; the runtime only
    cares about "what is the active budget now."

    Returns:
        BudgetEffects with `budget_after` equal to the merged result
        and `applied` set iff at least one patch was walked.
    """
    running = initial_budget
    counts: dict[ResearcherCommandType, int] = {}

    for cmd in commands:
        if cmd.command_type not in BUDGET_COMMAND_TYPES:
            continue
        counts[cmd.command_type] = counts.get(cmd.command_type, 0) + 1

        # Re-validate the payload defensively. The bus's per-command
        # validator already ran at drain time, so this is belt-and-
        # braces — the additional cost is one model_validate per patch.
        # Same pattern as control.py for `EndSessionPayload`.
        payload = SetQuietnessBudgetPayload.model_validate(cmd.payload)

        # Per-field merge: unset payload fields keep the running value.
        # Bookkeeping fields (current_window_count, last_utterance_at)
        # are never patchable by the wire schema and ride through
        # unchanged — see module docstring for the why.
        running = QuietnessBudget(
            max_utterances_per_10min=(
                payload.max_utterances_per_10min
                if payload.max_utterances_per_10min is not None
                else running.max_utterances_per_10min
            ),
            min_seconds_between_utterances=(
                payload.min_seconds_between_utterances
                if payload.min_seconds_between_utterances is not None
                else running.min_seconds_between_utterances
            ),
            current_window_count=running.current_window_count,
            last_utterance_at=running.last_utterance_at,
        )

    applied_count = counts.get("set_quietness_budget", 0)
    applied = applied_count > 0
    if applied:
        # One runtime call per batch with the FINAL budget — see
        # module docstring on why intermediate states are local-only.
        control.update_quietness_budget(running)

    return BudgetEffects(
        budget_after=running,
        applied_count=applied_count,
        applied=applied,
        counts=counts,
    )
