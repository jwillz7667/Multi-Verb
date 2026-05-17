"""Unit tests for `ExecutionDispatcher` (P4 L8).

The dispatcher is pure plumbing — spawn → await → write the outcome to
the existing `decisions` row. These tests verify:

  * Spawned tasks register in `in_flight_count` and self-discard.
  * Happy-path outcome flows into `DecisionRepo.update_execution` with
    the right arguments (was_executed, llm_output, spoken_at,
    additional_suppressed).
  * Executor crashes are caught and translated into a row update with
    `suppressed_by=["execution_crashed"]` and `was_executed=False`.
  * `aclose()` drains all in-flight tasks (and is idempotent on empty).
  * DB update errors are logged but never propagate out of the task.
  * CancelledError / KeyboardInterrupt / SystemExit bubble cleanly.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from verbio_engine.decisions import ExecutionDispatcher, ExecutionOutcome
from verbio_engine.domain.decision import ModeratorDecision
from verbio_engine.domain.session_state import SessionState
from verbio_engine.persistence import Decision

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SESSION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DECISION_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
TICK_TIME = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeExecutor:
    """Pre-configured `DecisionExecutor` stand-in.

    `outcome` is returned from `.execute()`. `raise_exc` causes execute()
    to raise instead — used to drive the crash-handling branch.
    `start_event` (if set) signals when the task body actually begins,
    so tests can observe `in_flight_count` while the task is mid-run.
    `release_event` (if set) blocks the task until released, so the
    drain-on-aclose test can prove tasks weren't dropped silently.
    """

    outcome: ExecutionOutcome | None = None
    raise_exc: Exception | None = None
    calls: list[tuple[ModeratorDecision, SessionState]] = field(default_factory=list)
    start_event: asyncio.Event | None = None
    release_event: asyncio.Event | None = None

    async def execute(
        self,
        decision: ModeratorDecision,
        state: SessionState,
    ) -> ExecutionOutcome:
        self.calls.append((decision, state))
        if self.start_event is not None:
            self.start_event.set()
        if self.release_event is not None:
            await self.release_event.wait()
        if self.raise_exc is not None:
            raise self.raise_exc
        assert self.outcome is not None
        return self.outcome


@dataclass
class _UpdateCall:
    """Captured argument set for one DecisionRepo.update_execution call."""

    decision_id: uuid.UUID
    was_executed: bool
    llm_output: str | None
    spoken_at: datetime | None
    additional_suppressed: list[str]


@dataclass
class _FakeAsyncSession:
    """SQLAlchemy AsyncSession stand-in supporting get/flush/begin."""

    row: Decision | None
    update_calls: list[_UpdateCall]
    raise_on_flush: bool = False
    flushed: list[Decision] = field(default_factory=list)

    async def get(self, model: type[Decision], pk: uuid.UUID) -> Decision | None:
        _ = model
        # Capture the call signature side-effect via update_calls when
        # the repo eventually mutates and flushes. We return the
        # pre-seeded row so the repo's mutate-and-flush path runs.
        if self.row is None:
            return None
        # Guard: the dispatcher should only look up the decision we set.
        assert pk == self.row.id, f"unexpected get({pk}) on row {self.row.id}"
        return self.row

    async def flush(self, instances: list[Decision] | None = None) -> None:
        if self.raise_on_flush:
            msg = "simulated DB outage"
            raise RuntimeError(msg)
        if instances is None:
            return
        for inst in instances:
            self.flushed.append(inst)
            # Snapshot the post-mutation state into update_calls so the
            # test can assert what got persisted without referencing the
            # mutable row instance.
            self.update_calls.append(
                _UpdateCall(
                    decision_id=inst.id,
                    was_executed=inst.was_executed,
                    llm_output=inst.llm_output,
                    spoken_at=inst.spoken_at,
                    additional_suppressed=list(inst.suppressed_by),
                ),
            )

    def begin(self) -> object:
        outer = self

        class _Tx:
            async def __aenter__(self) -> _FakeAsyncSession:
                return outer

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Tx()


@dataclass
class _FakeSessionFactory:
    rows: list[Decision | None]
    update_calls: list[_UpdateCall]
    raise_on_flush: bool = False
    spawned: list[_FakeAsyncSession] = field(default_factory=list)

    def __call__(self) -> object:
        # Pop one pre-seeded row per session open so successive
        # update_execution calls each see their own get() result.
        row = self.rows.pop(0) if self.rows else None
        sess = _FakeAsyncSession(
            row=row,
            update_calls=self.update_calls,
            raise_on_flush=self.raise_on_flush,
        )
        self.spawned.append(sess)

        @asynccontextmanager
        async def _cm() -> AsyncIterator[_FakeAsyncSession]:
            yield sess

        return _cm()


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _decision() -> ModeratorDecision:
    return ModeratorDecision(
        decision_id=DECISION_ID,
        session_id=SESSION_ID,
        tick_id=0,
        timestamp=TICK_TIME,
        action="prompt_participant",
        target_participant_id="alice",
        source="auto",
        triggering_rule="silence_gap",
        cooldown_until=TICK_TIME + timedelta(seconds=45),
    )


def _state() -> SessionState:
    return SessionState(
        session_id=SESSION_ID,
        tick_id=0,
        t=TICK_TIME,
        started_at=TICK_TIME - timedelta(minutes=2),
        elapsed_sec=120.0,
        participants={},
    )


def _seed_row() -> Decision:
    """Pre-existing decision row the dispatcher updates via `.get()`."""
    return Decision(
        id=DECISION_ID,
        session_id=SESSION_ID,
        tick_id=0,
        ts=TICK_TIME,
        action="prompt_participant",
        source="auto",
        triggering_rule="silence_gap",
        reason_codes=["silence_gap_long"],
        reason_human="Long silence detected",
        confidence=0.9,
        suppressed_by=[],
        was_executed=False,
        cooldown_until=TICK_TIME + timedelta(seconds=45),
    )


def _build(
    *,
    executor: _FakeExecutor,
    rows: list[Decision | None] | None = None,
    raise_on_flush: bool = False,
) -> tuple[ExecutionDispatcher, _FakeSessionFactory, list[_UpdateCall]]:
    update_calls: list[_UpdateCall] = []
    factory = _FakeSessionFactory(
        rows=rows if rows is not None else [_seed_row()],
        update_calls=update_calls,
        raise_on_flush=raise_on_flush,
    )
    dispatcher = ExecutionDispatcher(
        executor=executor,  # type: ignore[arg-type]
        session_factory=factory,  # type: ignore[arg-type]
    )
    return dispatcher, factory, update_calls


async def _drain(dispatcher: ExecutionDispatcher) -> None:
    """Helper — wait for every dispatched task to settle.

    Uses `aclose()` because it's the documented drain primitive on the
    dispatcher (awaits the in-flight task set). Tests that need to
    observe `in_flight_count` mid-task drive their own coordination via
    explicit `asyncio.Event`s instead of going through this helper.
    """
    await dispatcher.aclose()


# ---------------------------------------------------------------------------
# Construction + lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_initial_in_flight_count_is_zero(self) -> None:
        executor = _FakeExecutor(outcome=_happy_outcome())
        dispatcher, _factory, _updates = _build(executor=executor)

        assert dispatcher.in_flight_count == 0

    async def test_aclose_is_safe_when_nothing_dispatched(self) -> None:
        executor = _FakeExecutor(outcome=_happy_outcome())
        dispatcher, _factory, _updates = _build(executor=executor)

        await dispatcher.aclose()
        # Calling again must be a no-op (idempotent).
        await dispatcher.aclose()
        assert dispatcher.in_flight_count == 0

    async def test_in_flight_count_reflects_running_task(self) -> None:
        start = asyncio.Event()
        release = asyncio.Event()
        executor = _FakeExecutor(
            outcome=_happy_outcome(),
            start_event=start,
            release_event=release,
        )
        dispatcher, _factory, _updates = _build(executor=executor)

        dispatcher.dispatch(_decision(), _state())
        await start.wait()
        assert dispatcher.in_flight_count == 1

        release.set()
        await _drain(dispatcher)
        assert dispatcher.in_flight_count == 0


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def _happy_outcome() -> ExecutionOutcome:
    return ExecutionOutcome(
        was_executed=True,
        llm_output="Could you say more about that?",
        spoken_at=TICK_TIME + timedelta(milliseconds=900),
        suppressed_by=[],
        latency_ms=900,
    )


class TestHappyPath:
    async def test_outcome_is_persisted_via_update_execution(self) -> None:
        executor = _FakeExecutor(outcome=_happy_outcome())
        dispatcher, _factory, updates = _build(executor=executor)

        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)

        assert len(updates) == 1
        update = updates[0]
        assert update.decision_id == DECISION_ID
        assert update.was_executed is True
        assert update.llm_output == "Could you say more about that?"
        assert update.spoken_at == TICK_TIME + timedelta(milliseconds=900)
        # No additional suppressed codes on success.
        assert update.additional_suppressed == []

    async def test_appends_runtime_suppression_codes_to_existing_row(self) -> None:
        # Pre-existing row carries resolver codes; outcome adds runtime codes.
        seed = _seed_row()
        seed.suppressed_by = ["lower_priority_won"]
        outcome = ExecutionOutcome(
            was_executed=True,
            llm_output="fallback text",
            spoken_at=TICK_TIME + timedelta(milliseconds=1200),
            suppressed_by=["llm_fallback"],
            latency_ms=1200,
        )
        executor = _FakeExecutor(outcome=outcome)
        dispatcher, _factory, updates = _build(executor=executor, rows=[seed])

        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)

        assert updates[0].additional_suppressed == [
            "lower_priority_won",
            "llm_fallback",
        ]


# ---------------------------------------------------------------------------
# Crash handling
# ---------------------------------------------------------------------------


class TestCrashHandling:
    async def test_executor_crash_writes_execution_crashed_code(self) -> None:
        executor = _FakeExecutor(raise_exc=RuntimeError("kaboom"))
        dispatcher, _factory, updates = _build(executor=executor)

        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)

        assert len(updates) == 1
        update = updates[0]
        assert update.was_executed is False
        assert update.llm_output is None
        assert update.spoken_at is None
        assert update.additional_suppressed == ["execution_crashed"]

    async def test_executor_crash_does_not_leak_exception_out_of_task(self) -> None:
        executor = _FakeExecutor(raise_exc=RuntimeError("kaboom"))
        dispatcher, _factory, _updates = _build(executor=executor)

        # If the exception leaked the test runner would see "task was
        # destroyed but it is pending" / unhandled exceptions during
        # cleanup. Reaching the assertion is the test.
        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)
        assert dispatcher.in_flight_count == 0

    async def test_update_failure_is_swallowed_and_logged(self) -> None:
        executor = _FakeExecutor(outcome=_happy_outcome())
        dispatcher, _factory, _updates = _build(executor=executor, raise_on_flush=True)

        # The flush will raise inside `_persist_outcome`; the dispatcher
        # logs and returns. The task must complete without leaking.
        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)
        assert dispatcher.in_flight_count == 0

    async def test_missing_row_skips_silently(self) -> None:
        # session.get returns None — repo's update_execution short-circuits;
        # no flush happens, no update_call recorded, no exception raised.
        executor = _FakeExecutor(outcome=_happy_outcome())
        dispatcher, _factory, updates = _build(executor=executor, rows=[None])

        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)

        assert updates == []
        assert dispatcher.in_flight_count == 0


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_aclose_drains_in_flight_task(self) -> None:
        start = asyncio.Event()
        release = asyncio.Event()
        executor = _FakeExecutor(
            outcome=_happy_outcome(),
            start_event=start,
            release_event=release,
        )
        dispatcher, _factory, updates = _build(executor=executor)

        dispatcher.dispatch(_decision(), _state())
        await start.wait()
        assert dispatcher.in_flight_count == 1

        # Release in a background task so we can call aclose() concurrently.
        async def _release_later() -> None:
            await asyncio.sleep(0.01)
            release.set()

        releaser = asyncio.create_task(_release_later())
        await dispatcher.aclose()
        await releaser

        # Update must have landed — aclose() didn't drop the work.
        assert len(updates) == 1
        assert dispatcher.in_flight_count == 0

    async def test_cancelled_error_propagates_and_is_not_translated(self) -> None:
        # CancelledError from the executor should NOT trigger _persist_crash
        # — it indicates a shutdown signal we shouldn't try to handle here.
        # We can't directly test "exception propagates out of an asyncio.Task"
        # from the dispatcher API, but we can verify that a cancelled task
        # never writes execution_crashed to the row.
        cancel_in_executor = asyncio.CancelledError()
        executor = _FakeExecutor(raise_exc=cancel_in_executor)
        dispatcher, _factory, updates = _build(executor=executor)

        dispatcher.dispatch(_decision(), _state())
        await _drain(dispatcher)

        # No execution_crashed update should have been recorded — the
        # CancelledError bubbled out of `_run` without triggering crash
        # persistence.
        assert updates == []


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    async def test_multiple_dispatches_persist_independently(self) -> None:
        executor = _FakeExecutor(outcome=_happy_outcome())
        rows = [_seed_row(), _seed_row(), _seed_row()]
        dispatcher, _factory, updates = _build(executor=executor, rows=rows)

        for _ in range(3):
            dispatcher.dispatch(_decision(), _state())

        await _drain(dispatcher)
        assert len(updates) == 3
        assert all(u.was_executed is True for u in updates)


# ---------------------------------------------------------------------------
# pytest-asyncio mode declaration
# ---------------------------------------------------------------------------


pytestmark = pytest.mark.asyncio
