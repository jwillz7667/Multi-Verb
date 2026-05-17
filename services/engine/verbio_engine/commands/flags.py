"""`flag_moment` — researcher bookmark for replay (P5 L7).

Sister module to `commands.control` and `commands.budget`. Unlike
those two, `flag_moment` does not mutate any live runtime state and
does not produce a `ModeratorDecision`. It is pure projection:

  * Audit row in `researcher_actions` (written by the listener's
    `_persist_commands` like every other command — that's already in
    place since P5 L2).
  * Replay-surface row in `session_flags` (this module's reason for
    existing — written by the listener after walking the batch).
  * Live SSE envelope so the dashboard's flag rail surfaces the
    bookmark in real time, not just on replay (published by the
    listener after the row is durable).

Why a separate handler rather than folding into `_persist_commands`
--------------------------------------------------------------------
Two reasons. First, the audit and the bookmark are *different*
projections of the same intent — researchers can re-query their
actions vs. browse flagged moments without one query needing to know
the other exists. Second, this module owns the
`SessionFlagInsert`-shaping logic (mapping `command_id` → `flag_id`,
`payload.note` → `note`, etc.), so the listener stays a thin
orchestrator rather than a transform layer.

Idempotency mirrors the audit row
---------------------------------
The repo uses the wire-shape `command_id` as the row PK so a re-drain
after engine restart deduplicates. We carry that contract here by
using `cmd.command_id` as `SessionFlagInsert.id` rather than minting
a fresh UUID. The two rows (researcher_actions, session_flags) share
the same UUID across both tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, get_args

from verbio_engine.domain.command import (
    FlagMomentPayload,
    ResearcherCommandType,
)
from verbio_engine.persistence import SessionFlagInsert

if TYPE_CHECKING:
    import uuid
    from collections.abc import Iterable

    from verbio_engine.domain.command import ResearcherCommand


FLAG_COMMAND_TYPES: Final[frozenset[ResearcherCommandType]] = frozenset(
    {"flag_moment"},
)
"""Command types this module owns.

Single-element set for membership-test symmetry with
`CONTROL_COMMAND_TYPES`, `BUDGET_COMMAND_TYPES`, and
`SPOKEN_COMMAND_TYPES`. The listener's `_persist_commands` writes the
audit row for every type; this set is only consulted by
`apply_flag_commands` to decide which entries also need a
`session_flags` row.
"""


_VALID_COMMAND_TYPES: Final[frozenset[str]] = frozenset(get_args(ResearcherCommandType))
assert (
    FLAG_COMMAND_TYPES <= _VALID_COMMAND_TYPES
), "FLAG_COMMAND_TYPES drifted from ResearcherCommandType"


@dataclass(slots=True, frozen=True)
class FlagEffects:
    """Aggregate of what the flag_moment batch produced for the listener.

    Returned by `apply_flag_commands` and consumed by the listener to
    drive a single `session_flags` insert transaction plus a SSE
    publish per record. Pure data — no live-runtime side effects, so
    there is no `Control` Protocol equivalent of `BudgetControl` /
    `RuntimeControl` for this module.

    Attributes:
        records: `SessionFlagInsert` rows to persist, in batch FIFO
            order. Empty when no `flag_moment` command landed.
        applied_count: Number of `flag_moment` commands the walker
            shaped into rows. Equal to `len(records)` — the field is
            kept for structured-logging symmetry with the other
            handlers' `applied_count`.
        counts: per-command-type counts for structured logging.
    """

    records: tuple[SessionFlagInsert, ...] = ()
    applied_count: int = 0
    counts: dict[ResearcherCommandType, int] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        """True iff at least one `flag_moment` command was walked."""
        return self.applied_count > 0


def apply_flag_commands(
    *,
    commands: Iterable[ResearcherCommand],
    researcher_id_for: dict[str, uuid.UUID],
) -> FlagEffects:
    """Walk the batch in FIFO order, building SessionFlagInsert records.

    Non-flag commands are skipped — they're owned by other handlers.
    The walker validates the payload defensively (the bus already
    validated at drain time, so this is belt-and-braces — same pattern
    as the control / budget walkers) and shapes:

      * `id` ← `cmd.command_id` so the row deduplicates with the
        matching `researcher_actions` row's PK across restart-replay
        (both tables key on the same wire-shape UUID).
      * `session_id` ← `cmd.session_id`.
      * `ts` ← `cmd.issued_at` (the researcher's clock, not the tick
        wall-clock; the bookmark refers to the moment they clicked
        flag, not the next tick boundary).
      * `researcher_id` ← from the pre-validated map. Commands with
        a malformed researcher_id were already filtered out by
        `_persist_commands`; the listener passes only the
        already-parsed UUIDs in `researcher_id_for` so this walker
        does not re-do the UUID parse.
      * `note` ← payload `note` (may be None).
      * `auto_generated=False` — flag_moment is always researcher-
        issued. Engine-auto flags use the repo directly (no command
        path) with `auto_generated=True`.

    Records for commands whose researcher_id is missing from
    `researcher_id_for` (i.e., `_persist_commands` skipped them) are
    silently dropped — the audit row never existed, so writing a
    replay bookmark without it would create a dangling flag.

    Returns:
        FlagEffects with the shaped rows + counts.
    """
    records: list[SessionFlagInsert] = []
    counts: dict[ResearcherCommandType, int] = {}

    for cmd in commands:
        if cmd.command_type not in FLAG_COMMAND_TYPES:
            continue
        counts[cmd.command_type] = counts.get(cmd.command_type, 0) + 1

        researcher_uuid = researcher_id_for.get(cmd.researcher_id)
        if researcher_uuid is None:
            # Audit row was skipped upstream (malformed researcher_id);
            # drop the bookmark too so the dashboard never shows a
            # flag pin that has no audit trail behind it.
            continue

        # Re-validate the payload defensively. The bus's per-command
        # validator already ran at drain time, so this is belt-and-
        # braces — the additional cost is one model_validate per flag.
        # Same pattern as control.py / budget.py.
        payload = FlagMomentPayload.model_validate(cmd.payload)

        records.append(
            SessionFlagInsert(
                id=cmd.command_id,
                session_id=cmd.session_id,
                ts=cmd.issued_at,
                researcher_id=researcher_uuid,
                note=payload.note,
                auto_generated=False,
            ),
        )

    applied_count = counts.get("flag_moment", 0)

    return FlagEffects(
        records=tuple(records),
        applied_count=applied_count,
        counts=counts,
    )
