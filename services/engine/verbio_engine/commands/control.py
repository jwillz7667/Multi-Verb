"""Non-spoken researcher commands → live runtime control plane (P5 L5).

Force commands (P5 L3 / L4) override the resolver and produce a
`ModeratorDecision`. The commands in this module are different: they
never produce a decision. Instead they mutate the live session — they
mute the moderator, pause the tick loop, end the session.

How they slot into the tick (brief §6, augmented for P5):

  1. Drain commands from the bus.
  2. Persist every command to `researcher_actions` (audit-first).
  3. **(this module)** Apply control-plane effects in batch order:
     mute / unmute, pause / resume, end_session. Each is delegated to a
     `RuntimeControl` provider — typically `SessionRuntime` — so this
     module stays transport-agnostic.
  4. Resolver + override path produce a `ModeratorDecision`.
  5. Dispatch gate consults the *effective* mute/pause/end state we
     computed in step 3. If mute or pause is on at end-of-batch, a
     non-silent decision still persists with `suppressed_by` extended
     by ``"muted"`` / ``"paused"`` / ``"session_ended"`` — the audit
     trail keeps the moderator's intent even when the execution path
     was gated off.

Why in-tick effective state, not just the snapshot?
---------------------------------------------------
The state snapshot the listener receives was projected by
`StateStore.advance_to(t)` *before* commands were drained. If a
researcher mutes the moderator at tick T, the snapshot for tick T
still shows ``moderator_muted=False`` — the store change applied here
only surfaces in tick T+1's snapshot. To honour the researcher's
intent *this* tick, we compute `ControlEffects` over the drained batch
and feed it to the dispatcher gate directly.

Why FIFO walk over the batch?
-----------------------------
Two commands in the same batch (rare but well-defined) compose in
arrival order. A `mute_moderator` followed by `unmute_moderator` ends
with mute off; reversed, ends with mute on. The final state matches
"as if each command had been processed at its issued time" — which is
the same FIFO semantics overriding commands (force_*, whisper) use.

`end_session` is terminal: once observed in a batch, the runtime is
asked to mark the session ended and stop the tick loop. The current
tick still finishes (persist + publish + audit), but no future ticks
will fire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol, get_args

from verbio_engine.domain.command import EndSessionPayload, ResearcherCommandType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from verbio_engine.domain.command import ResearcherCommand


CONTROL_COMMAND_TYPES: Final[frozenset[ResearcherCommandType]] = frozenset(
    {
        "mute_moderator",
        "unmute_moderator",
        "pause_session",
        "resume_session",
        "end_session",
    },
)
"""Commands that mutate runtime state without producing a decision row.

`set_quietness_budget` is intentionally NOT in here even though it also
mutates runtime state — it gets its own handler in P5 L6 because the
budget object has structured payload semantics. `flag_moment` is also
out (P5 L7): it's a bookmark, with no live-runtime effect.
"""


_VALID_COMMAND_TYPES: Final[frozenset[str]] = frozenset(get_args(ResearcherCommandType))
assert (
    CONTROL_COMMAND_TYPES <= _VALID_COMMAND_TYPES
), "CONTROL_COMMAND_TYPES drifted from ResearcherCommandType"


class RuntimeControl(Protocol):
    """Live control surface the listener uses to apply non-spoken commands.

    The concrete implementation is `SessionRuntime`; this Protocol exists
    so tests can inject a fake without dragging in the whole runtime.

    Mute and pause are synchronous: they flip a flag on the state store
    and return. `request_end_session` is async: it marks the session
    ended in Postgres and stops the tick loop — both of which the
    listener should await before its tick completes so the audit write
    and the loop teardown observe each other consistently.
    """

    def set_muted(self, *, muted: bool) -> None:
        """Flip `SessionState.moderator_muted`. Idempotent."""
        ...

    def set_pause(self, *, paused: bool) -> None:
        """Flip `SessionState.is_paused`. Idempotent."""
        ...

    async def request_end_session(self, *, reason: str | None) -> None:
        """Mark the session ended in Postgres and stop the tick loop.

        `reason` is informational — persisted on the matching
        `researcher_actions` row (handled by the listener via the audit
        write); the runtime itself does not need it for shutdown. Pass
        through anyway so a future logging hook has the context.
        """
        ...


@dataclass(slots=True, frozen=True)
class ControlEffects:
    """Aggregate of what the batch did to the live control surface.

    Returned by `apply_control_commands` and consumed by the listener
    to drive the dispatch gate + the decision row's `suppressed_by`.

    Attributes:
        muted_after: Final value of `moderator_muted` after walking the
            batch. Equal to `initial_muted` when no mute/unmute landed.
        paused_after: Final value of `is_paused` after walking the
            batch. Equal to `initial_paused` when no pause/resume landed.
        end_requested: True iff at least one `end_session` command was
            observed in the batch. The runtime has already been signalled
            (mark-ended + stop) by the time the caller reads this; the
            field is informational for the listener's gating logic.
        counts: per-command-type counts for structured logging. Lets
            operators see "mute_moderator x2 in same batch" — usually a
            UI double-click but also a clear signal of intent.
    """

    muted_after: bool
    paused_after: bool
    end_requested: bool
    counts: dict[ResearcherCommandType, int] = field(default_factory=dict)


async def apply_control_commands(
    *,
    commands: Iterable[ResearcherCommand],
    control: RuntimeControl,
    initial_muted: bool,
    initial_paused: bool,
) -> ControlEffects:
    """Walk the batch in FIFO order, mutating runtime state in step.

    Each control command is dispatched to the matching `RuntimeControl`
    method. Non-control commands (`force_*`, `whisper`,
    `set_quietness_budget`, `flag_moment`) are skipped — they're owned
    by other handlers. `initial_muted` / `initial_paused` are typically
    `state.moderator_muted` / `state.is_paused` from the snapshot the
    listener received; threading them in means callers don't have to
    re-derive what the pre-batch state was.

    `end_session` is awaited at the end of the walk (not inline) so
    that any mute/unmute issued *after* it in the same batch still
    completes its store mutation — terminal effects shouldn't reorder
    earlier effects, and the audit-row order is what researchers see.

    Returns:
        ControlEffects with the post-batch state, ready for the
        listener's dispatch gate.
    """
    muted = initial_muted
    paused = initial_paused
    end_requested = False
    end_reason: str | None = None
    counts: dict[ResearcherCommandType, int] = {}

    for cmd in commands:
        if cmd.command_type not in CONTROL_COMMAND_TYPES:
            continue
        counts[cmd.command_type] = counts.get(cmd.command_type, 0) + 1

        if cmd.command_type == "mute_moderator":
            muted = True
            control.set_muted(muted=True)
        elif cmd.command_type == "unmute_moderator":
            muted = False
            control.set_muted(muted=False)
        elif cmd.command_type == "pause_session":
            paused = True
            control.set_pause(paused=True)
        elif cmd.command_type == "resume_session":
            paused = False
            control.set_pause(paused=False)
        elif cmd.command_type == "end_session":
            end_requested = True
            # `payload` is an open dict on the wire; validate now so a
            # malformed reason fails at this single call-site rather than
            # spreading into the runtime. The bus's per-command validator
            # already ran, so this is belt-and-braces — the additional
            # cost is one model_validate per terminal command.
            payload = EndSessionPayload.model_validate(cmd.payload)
            end_reason = payload.reason

    if end_requested:
        await control.request_end_session(reason=end_reason)

    return ControlEffects(
        muted_after=muted,
        paused_after=paused,
        end_requested=end_requested,
        counts=counts,
    )
