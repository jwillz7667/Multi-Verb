"""Decision orchestration — bridge from rules to persistence + SSE.

This package owns the per-tick wiring that translates the pure rules
engine output into the audit-trail rows + dashboard events the brief
mandates (§6 invariants + §10.1 schema). Concrete moving parts:

  * `DecisionTickListener` — `SnapshotListener` impl that the tick loop
    calls each tick. Runs the resolver, persists the decision row plus
    one row per rule, publishes the SSE envelope.

The rules engine (`verbio_engine.rules`) stays pure; this package is
the only place where rule outputs touch I/O.
"""

from verbio_engine.decisions.listener import (
    DecisionTickListener,
    IdentityResolver,
)

__all__ = [
    "DecisionTickListener",
    "IdentityResolver",
]
