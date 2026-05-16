"""Tick loop — the heartbeat (brief §6).

Drives one `StateStore` per active session at a fixed cadence
(default 2 Hz). Phase 2 stops at step 1 of the brief's on_tick
pseudocode — `state = state_store.advance_to(t)` — and hands the
snapshot off to an optional async callback. Phase 3 swaps that
callback for the rules engine; Phase 2 L3 swaps it for persistence
plus the Redis publish that powers the dashboard tiles.

Why this lives separately from `StateStore`
-------------------------------------------

`StateStore` is pure (no clock, no I/O). The tick loop owns both:

  * the `Clock` protocol (so tests can substitute a deterministic clock),
  * the asyncio scheduler that advances the store at the configured rate,
  * the dispatch fan-out to side-effect listeners,
  * the latency-budget logging the brief mandates.

Test surface
------------

`TickLoop.tick_once()` is the single seam tests drive. Hypothesis-style
deterministic tests use `FakeClock(advance=...)` and call `tick_once`
N times — no asyncio sleep at all. Tests that need to exercise the
actual scheduler use `FakeClock` with a small `interval_sec` and drive
`run()` inside `asyncio.wait_for(...)`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from verbio_engine.domain.session_state import SessionState
from verbio_engine.state.store import StateStore

_DEFAULT_INTERVAL_SEC: float = 0.5
"""Brief §6 default. 2 Hz = 500ms tick interval."""

_log = logging.getLogger(__name__)


SnapshotListener = Callable[[SessionState], Awaitable[None]]
"""Async fan-out callback invoked after each successful tick.

Phase 2 L3 wires this to (persist → Redis publish). Multiple
subscribers can be served by wrapping them in `_fanout([a, b, c])`.
"""


class Clock(Protocol):
    """Time source the tick loop reads. Synchronously returns `now`;
    async `sleep` so a fake clock can yield control between ticks."""

    def now(self) -> datetime: ...

    async def sleep(self, seconds: float) -> None: ...


class WallClock:
    """Real wall-clock — the production scheduler."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    async def sleep(seconds: float) -> None:
        if seconds > 0.0:
            await asyncio.sleep(seconds)


@dataclass(slots=True)
class _PendingWaiter:
    """A pending `FakeClock.sleep` waiting for the clock to advance past its deadline."""

    deadline: datetime
    future: asyncio.Future[None]


class FakeClock:
    """Deterministic clock for tests.

    `sleep` registers a future that resolves only when `advance` rolls
    the virtual clock past the sleeper's deadline. The runtime asyncio
    loop still drives the coroutine, but the elapsed wall-clock time
    is irrelevant — tests can sequence ticks with no real waiting.
    """

    def __init__(self, start: datetime) -> None:
        self._now: datetime = start
        self._waiters: list[_PendingWaiter] = []

    def now(self) -> datetime:
        return self._now

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0.0:
            return
        loop = asyncio.get_running_loop()
        deadline = self._now + timedelta(seconds=seconds)
        waiter = _PendingWaiter(deadline=deadline, future=loop.create_future())
        self._waiters.append(waiter)
        try:
            await waiter.future
        finally:
            # Defensive: if cancellation removed the future before
            # `advance`, drop the waiter so we don't leak it.
            self._waiters = [w for w in self._waiters if w is not waiter]

    def advance(self, seconds: float) -> None:
        """Move the virtual clock forward and release any matured sleepers."""
        if seconds < 0.0:
            msg = f"FakeClock.advance: seconds must be >= 0, got {seconds}"
            raise ValueError(msg)
        self._now = self._now + timedelta(seconds=seconds)
        ready = [w for w in self._waiters if w.deadline <= self._now]
        self._waiters = [w for w in self._waiters if w.deadline > self._now]
        for w in ready:
            if not w.future.done():
                w.future.set_result(None)


@dataclass(slots=True)
class TickStats:
    """Per-loop counters surfaced for diagnostics."""

    ticks_completed: int = 0
    ticks_overrun: int = 0
    listener_failures: int = 0
    last_tick_at: datetime | None = None
    last_snapshot: SessionState | None = field(default=None, repr=False)


class TickLoop:
    """Async runner that drives `StateStore.advance_to` at a fixed cadence."""

    def __init__(
        self,
        *,
        store: StateStore,
        clock: Clock,
        interval_sec: float = _DEFAULT_INTERVAL_SEC,
        listener: SnapshotListener | None = None,
        scheduled_end_at: datetime | None = None,
    ) -> None:
        if interval_sec <= 0.0:
            msg = f"TickLoop.interval_sec must be > 0, got {interval_sec}"
            raise ValueError(msg)
        self._store = store
        self._clock = clock
        self._interval_sec = interval_sec
        self._listener = listener
        self._scheduled_end_at = scheduled_end_at
        self._stop_event = asyncio.Event()
        self._stats = TickStats()
        self._next_due_at: datetime | None = None

    # ------------------------------------------------------------- Lifecycle

    def stop(self) -> None:
        """Request a graceful exit. The current tick finishes first."""
        self._stop_event.set()

    @property
    def stats(self) -> TickStats:
        """Snapshot of counters; consumed by health endpoints + tests."""
        return self._stats

    # --------------------------------------------------------------- API

    async def tick_once(self) -> SessionState:
        """Execute one tick at the clock's current time.

        Returns the projected snapshot. If a listener is wired, it
        runs *after* the snapshot is captured — a listener exception
        is logged and counted but never aborts the loop, mirroring
        brief §6 ("the tick loop never blocks on LLM/TTS").
        """
        t = self._clock.now()
        snapshot = self._store.advance_to(t)
        self._stats.ticks_completed += 1
        self._stats.last_tick_at = t
        self._stats.last_snapshot = snapshot

        if self._listener is not None:
            try:
                await self._listener(snapshot)
            except Exception:
                self._stats.listener_failures += 1
                _log.exception(
                    "tick_loop.listener_failed",
                    extra={"session_id": str(snapshot.session_id), "tick_id": snapshot.tick_id},
                )

        return snapshot

    async def run(self) -> None:
        """Run ticks at `interval_sec` cadence until stopped or end-of-session.

        The deadline for tick N is `start + N*interval`, not `previous +
        interval` — this prevents cadence drift when a tick overruns.
        An overrun tick (computed sleep ≤ 0) fires immediately and
        bumps `stats.ticks_overrun`, so operators can spot a session
        that's falling behind.
        """
        anchor = self._clock.now()
        self._next_due_at = anchor

        while not self._stop_event.is_set():
            if self._scheduled_end_at is not None and self._clock.now() >= self._scheduled_end_at:
                _log.info("tick_loop.scheduled_end_reached")
                break

            await self.tick_once()
            self._next_due_at = self._next_due_at + timedelta(seconds=self._interval_sec)

            sleep_for = (self._next_due_at - self._clock.now()).total_seconds()
            if sleep_for <= 0.0:
                # Overrun: the next deadline has already passed. Skip the
                # sleep and fire the next tick at once, but log it — a
                # session whose ticks consistently overrun the budget is
                # not respecting the brief's 1500ms latency contract.
                self._stats.ticks_overrun += 1
                _log.warning(
                    "tick_loop.tick_overrun",
                    extra={
                        "overrun_sec": -sleep_for,
                        "ticks_completed": self._stats.ticks_completed,
                    },
                )
                continue

            await _race(self._clock.sleep(sleep_for), self._stop_event.wait())


async def _race(*coros: Awaitable[object]) -> None:
    """Wait for the first of the given coroutines to complete; cancel the rest.

    Used to break out of `clock.sleep` immediately when `stop()` flips
    the event, so callers don't have to wait one full interval before
    the loop exits.
    """
    tasks = [asyncio.ensure_future(c) for c in coros]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        # Drain cancellations so they don't surface as warnings.
        for task in pending:
            with _suppress_cancel():
                await task
        for task in done:
            # Re-raise any exception from the winning coroutine.
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


class _suppress_cancel:  # noqa: N801 — context-manager style, lowercase intentional
    """Context manager that swallows asyncio.CancelledError."""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: type[BaseException] | None, *_: object) -> bool:
        return exc_type is asyncio.CancelledError
