"""`ExecutionDispatcher` — fire-and-forget runner for `DecisionExecutor` (P4 L8).

The tick loop's §6 step-6 invariant ("the tick loop never blocks on
LLM/TTS") is enforced here, not in the executor. The dispatcher is the
piece that takes a persisted `ModeratorDecision`, spawns the executor
as an `asyncio.Task`, and writes the outcome back to the row when it
finishes. The tick loop's next 500 ms tick fires without waiting.

Two boundaries the dispatcher owns:

  * In-flight task tracking — every spawned task is added to a set and
    discarded on completion. `aclose()` awaits whatever is still running
    so session teardown doesn't drop half-spoken utterances.

  * DB write — the executor's `ExecutionOutcome` is translated into a
    `DecisionRepo.update_execution` call inside a fresh transaction.
    Wrapping in our own transaction (not piggybacking on the listener's)
    keeps the listener short and lets the dispatcher run long after
    the tick has moved on.

The dispatcher is intentionally narrow: it does NOT decide whether to
dispatch (the listener gates on `action != stay_silent` + `source ==
auto`) and does NOT know about budgets (those live in the executor).
It's pure plumbing — tasks, transactions, and an audit log of crashes.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from verbio_engine.logging import get_logger
from verbio_engine.persistence import DecisionRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.decisions.orchestrator import DecisionExecutor, ExecutionOutcome
    from verbio_engine.domain.decision import ModeratorDecision
    from verbio_engine.domain.session_state import SessionState

log = get_logger(__name__)


class ExecutionDispatcher:
    """Spawns + tracks `DecisionExecutor` tasks for the tick listener."""

    __slots__ = ("_executor", "_in_flight", "_session_factory")

    def __init__(
        self,
        *,
        executor: DecisionExecutor,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._executor = executor
        self._session_factory = session_factory
        self._in_flight: set[asyncio.Task[None]] = set()

    def dispatch(self, decision: ModeratorDecision, state: SessionState) -> None:
        """Spawn execution for `decision`; non-blocking.

        Caller (the tick listener) keeps no reference to the task — the
        dispatcher tracks it via `_in_flight` so `aclose()` can drain.
        """
        task = asyncio.create_task(
            self._run(decision, state),
            name=f"executor-{decision.decision_id}",
        )
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    @property
    def in_flight_count(self) -> int:
        """Number of executor tasks still running. Test + telemetry aid."""
        return len(self._in_flight)

    async def aclose(self) -> None:
        """Wait for every in-flight executor to finish. Idempotent."""
        if not self._in_flight:
            return
        # Snapshot — tasks scheduled after this call don't extend the wait.
        outstanding = list(self._in_flight)
        await asyncio.gather(*outstanding, return_exceptions=True)

    async def _run(self, decision: ModeratorDecision, state: SessionState) -> None:
        try:
            outcome = await self._executor.execute(decision, state)
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # The executor's contract is to return an ExecutionOutcome
            # describing failure rather than raise. If it raises anyway
            # we audit the crash AND mark the row, so the dashboard
            # never sees a "stale" was_executed=False with no codes.
            log.exception(
                "dispatcher.executor_crashed",
                decision_id=str(decision.decision_id),
            )
            await self._persist_crash(decision)
            return
        await self._persist_outcome(decision, outcome)

    async def _persist_outcome(
        self,
        decision: ModeratorDecision,
        outcome: ExecutionOutcome,
    ) -> None:
        try:
            async with self._session_factory() as db, db.begin():
                repo = DecisionRepo(db)
                await repo.update_execution(
                    decision_id=decision.decision_id,
                    was_executed=outcome.was_executed,
                    llm_output=outcome.llm_output,
                    spoken_at=outcome.spoken_at,
                    additional_suppressed=list(outcome.suppressed_by),
                )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            # Update failures are loud but non-fatal — the next executor
            # task and the next tick keep flowing. The audit row stays
            # at its post-insert state (`was_executed=False`), which is
            # the safer default if we can't confirm the spoken side.
            log.exception(
                "dispatcher.update_failed",
                decision_id=str(decision.decision_id),
            )

    async def _persist_crash(self, decision: ModeratorDecision) -> None:
        try:
            async with self._session_factory() as db, db.begin():
                repo = DecisionRepo(db)
                await repo.update_execution(
                    decision_id=decision.decision_id,
                    was_executed=False,
                    llm_output=None,
                    spoken_at=None,
                    additional_suppressed=["execution_crashed"],
                )
        except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            log.exception(
                "dispatcher.crash_update_failed",
                decision_id=str(decision.decision_id),
            )
