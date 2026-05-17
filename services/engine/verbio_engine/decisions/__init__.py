"""Decision orchestration — bridge from rules to persistence + SSE + audio.

This package owns the per-tick wiring that translates the pure rules
engine output into the audit-trail rows + dashboard events the brief
mandates (§6 invariants + §10.1 schema). Concrete moving parts:

  * `DecisionTickListener` — `SnapshotListener` impl that the tick loop
    calls each tick. Runs the resolver, persists the decision row plus
    one row per rule, publishes the SSE envelope. Optionally hands the
    decision to an `ExecutionDispatcher` for the mouth+TTS+publisher run.
  * `DecisionExecutor` (P4 L8) — pure pipeline: phrasing context →
    mouth (with §8.4 budget) → optional cached fallback → TTS → publisher.
  * `ExecutionDispatcher` (P4 L8) — fire-and-forget runner that the
    listener hands decisions to; spawns the executor as a background
    task so the tick loop stays unblocked, then writes the outcome back
    to the existing `decisions` row via `DecisionRepo.update_execution`.

The rules engine (`verbio_engine.rules`) stays pure; this package is
the only place where rule outputs touch I/O.
"""

from verbio_engine.decisions.dispatcher import ExecutionDispatcher
from verbio_engine.decisions.listener import (
    DecisionTickListener,
    IdentityResolver,
)
from verbio_engine.decisions.orchestrator import (
    DEFAULT_MOUTH_BUDGET_MS,
    DEFAULT_TOTAL_BUDGET_MS,
    ClockFn,
    DecisionExecutor,
    ExecutionOutcome,
)

__all__ = [
    "DEFAULT_MOUTH_BUDGET_MS",
    "DEFAULT_TOTAL_BUDGET_MS",
    "ClockFn",
    "DecisionExecutor",
    "DecisionTickListener",
    "ExecutionDispatcher",
    "ExecutionOutcome",
    "IdentityResolver",
]
