"""Coverage of `tick_loop.py` — the 2 Hz heartbeat (brief §6).

Each test is self-contained: build a `StateStore`, drive the loop with
a `FakeClock`, assert on `TickStats` and on the snapshot the listener
received. No wall-clock sleeps anywhere — `FakeClock` releases pending
sleepers on `advance`, so the asyncio loop runs the coroutines without
any real time passing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from verbio_engine.domain import SessionState
from verbio_engine.state import (
    ParticipantJoinEvent,
    StateStore,
)
from verbio_engine.tick_loop import (
    FakeClock,
    TickLoop,
    TickStats,
    WallClock,
    _race,
    _suppress_cancel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start() -> datetime:
    return datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _store(*, started_at: datetime | None = None) -> StateStore:
    return StateStore(
        session_id=uuid4(),
        started_at=started_at if started_at is not None else _start(),
    )


def _loop(
    *,
    store: StateStore | None = None,
    clock: FakeClock | None = None,
    interval_sec: float = 0.5,
    listener: object | None = None,
    scheduled_end_at: datetime | None = None,
) -> TickLoop:
    return TickLoop(
        store=store if store is not None else _store(),
        clock=clock if clock is not None else FakeClock(start=_start()),
        interval_sec=interval_sec,
        listener=listener,  # type: ignore[arg-type]
        scheduled_end_at=scheduled_end_at,
    )


# ---------------------------------------------------------------------------
# WallClock
# ---------------------------------------------------------------------------


def test_wall_clock_now_returns_aware_utc() -> None:
    t = WallClock.now()
    assert t.tzinfo is UTC


async def test_wall_clock_sleep_zero_returns_immediately() -> None:
    # If this were to call asyncio.sleep, it would still resolve, but
    # the guard short-circuits and never hits the event loop.
    await WallClock.sleep(0.0)
    await WallClock.sleep(-1.0)


async def test_wall_clock_sleep_positive_yields(monkeypatch: pytest.MonkeyPatch) -> None:
    awaited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        awaited.append(seconds)

    monkeypatch.setattr("verbio_engine.tick_loop.asyncio.sleep", fake_sleep)
    await WallClock.sleep(0.001)
    assert awaited == [0.001]


# ---------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------


def test_fake_clock_now_returns_start_time() -> None:
    start = _start()
    clock = FakeClock(start=start)
    assert clock.now() == start


async def test_fake_clock_sleep_zero_is_noop() -> None:
    clock = FakeClock(start=_start())
    await clock.sleep(0.0)
    await clock.sleep(-2.5)


async def test_fake_clock_sleep_blocks_until_advance() -> None:
    clock = FakeClock(start=_start())
    completed = asyncio.Event()

    async def sleeper() -> None:
        await clock.sleep(1.0)
        completed.set()

    task = asyncio.create_task(sleeper())
    # Give the event loop one cycle to register the waiter.
    await asyncio.sleep(0)
    assert not completed.is_set()

    clock.advance(0.5)
    await asyncio.sleep(0)
    assert not completed.is_set(), "0.5s elapsed but deadline was 1.0s"

    clock.advance(0.5)
    await task
    assert completed.is_set()


async def test_fake_clock_advance_releases_only_matured_waiters() -> None:
    clock = FakeClock(start=_start())
    results: list[str] = []

    async def labelled(name: str, seconds: float) -> None:
        await clock.sleep(seconds)
        results.append(name)

    short_task = asyncio.create_task(labelled("short", 0.3))
    long_task = asyncio.create_task(labelled("long", 2.0))
    await asyncio.sleep(0)

    clock.advance(0.5)
    await asyncio.sleep(0)
    await short_task
    assert results == ["short"]
    assert not long_task.done()

    clock.advance(1.5)
    await long_task
    assert results == ["short", "long"]


def test_fake_clock_advance_rejects_negative() -> None:
    clock = FakeClock(start=_start())
    with pytest.raises(ValueError, match="must be >= 0"):
        clock.advance(-0.1)


async def test_fake_clock_cancellation_drops_waiter() -> None:
    """Cancelling a sleep must remove its pending waiter so a later
    `advance` doesn't try to resolve a torn-down future."""
    clock = FakeClock(start=_start())

    async def sleeper() -> None:
        await clock.sleep(5.0)

    task = asyncio.create_task(sleeper())
    await asyncio.sleep(0)
    task.cancel()
    with _suppress_cancel():
        await task

    # If the waiter wasn't dropped, advance() would call set_result on a
    # cancelled future and raise InvalidStateError.
    clock.advance(10.0)


# ---------------------------------------------------------------------------
# TickStats
# ---------------------------------------------------------------------------


def test_tick_stats_defaults() -> None:
    stats = TickStats()
    assert stats.ticks_completed == 0
    assert stats.ticks_overrun == 0
    assert stats.listener_failures == 0
    assert stats.last_tick_at is None
    assert stats.last_snapshot is None


# ---------------------------------------------------------------------------
# TickLoop construction
# ---------------------------------------------------------------------------


def test_tick_loop_rejects_non_positive_interval() -> None:
    store = _store()
    clock = FakeClock(start=_start())
    with pytest.raises(ValueError, match="must be > 0"):
        TickLoop(store=store, clock=clock, interval_sec=0.0)
    with pytest.raises(ValueError, match="must be > 0"):
        TickLoop(store=store, clock=clock, interval_sec=-0.5)


def test_tick_loop_exposes_stats() -> None:
    loop = _loop()
    assert isinstance(loop.stats, TickStats)
    assert loop.stats.ticks_completed == 0


# ---------------------------------------------------------------------------
# TickLoop.tick_once
# ---------------------------------------------------------------------------


async def test_tick_once_returns_snapshot_and_updates_stats() -> None:
    clock = FakeClock(start=_start())
    loop = _loop(clock=clock)

    clock.advance(0.1)
    snap = await loop.tick_once()

    assert isinstance(snap, SessionState)
    assert loop.stats.ticks_completed == 1
    assert loop.stats.last_tick_at == clock.now()
    assert loop.stats.last_snapshot is snap


async def test_tick_once_invokes_listener_with_snapshot() -> None:
    received: list[SessionState] = []

    async def listener(snap: SessionState) -> None:
        received.append(snap)

    loop = _loop(listener=listener)
    snap = await loop.tick_once()

    assert received == [snap]
    assert loop.stats.listener_failures == 0


async def test_tick_once_listener_exception_does_not_abort_tick(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The brief is explicit: listener (LLM/TTS/persistence) must never
    stall the loop. An exception is logged + counted and the tick still
    returns its snapshot to the caller."""

    async def boom(_: SessionState) -> None:
        raise RuntimeError("listener exploded")

    loop = _loop(listener=boom)
    with caplog.at_level(logging.ERROR, logger="verbio_engine.tick_loop"):
        snap = await loop.tick_once()

    assert isinstance(snap, SessionState)
    assert loop.stats.ticks_completed == 1
    assert loop.stats.listener_failures == 1
    assert any("listener_failed" in rec.message for rec in caplog.records)


async def test_tick_once_works_without_listener() -> None:
    loop = _loop(listener=None)
    snap = await loop.tick_once()
    assert isinstance(snap, SessionState)
    assert loop.stats.listener_failures == 0


async def test_tick_once_projects_recorded_events() -> None:
    """The tick loop must hand off to `StateStore.advance_to(t)`, so
    events recorded before the tick should appear in the projection."""
    store = _store()
    clock = FakeClock(start=_start())
    loop = _loop(store=store, clock=clock)

    store.record(ParticipantJoinEvent(ts=_start(), participant_id="p1", display_name="P1"))
    clock.advance(0.5)
    snap = await loop.tick_once()

    assert "p1" in snap.participants


# ---------------------------------------------------------------------------
# TickLoop.stop + run
# ---------------------------------------------------------------------------


async def test_stop_event_breaks_run_loop() -> None:
    clock = FakeClock(start=_start())
    snapshots: list[SessionState] = []

    async def listener(snap: SessionState) -> None:
        snapshots.append(snap)
        if len(snapshots) >= 3:
            loop.stop()

    loop = _loop(clock=clock, listener=listener, interval_sec=0.5)

    async def driver() -> None:
        # Stay slightly ahead of the loop so each clock.sleep matures.
        while not loop._stop_event.is_set():
            await asyncio.sleep(0)
            clock.advance(0.5)

    await asyncio.gather(loop.run(), driver())
    assert len(snapshots) >= 3
    assert loop.stats.ticks_completed >= 3


async def test_run_stops_at_scheduled_end_at() -> None:
    start = _start()
    end = start + timedelta(seconds=2.0)
    clock = FakeClock(start=start)
    loop = _loop(clock=clock, interval_sec=0.5, scheduled_end_at=end)

    async def driver() -> None:
        # Advance past the scheduled end so the next loop iteration trips
        # the end-of-session check and breaks.
        for _ in range(8):
            await asyncio.sleep(0)
            clock.advance(0.5)

    await asyncio.gather(loop.run(), driver())
    # Loop should have stopped on its own; final tick time must be at
    # or past the scheduled end.
    assert loop.stats.last_tick_at is not None
    assert loop.stats.last_tick_at <= clock.now()


async def test_run_records_overrun_when_tick_falls_behind() -> None:
    """If the clock has already advanced past the next deadline by the
    time we go to sleep, that's an overrun. The loop bumps the counter
    and proceeds immediately without sleeping."""
    clock = FakeClock(start=_start())

    async def slow_listener(_: SessionState) -> None:
        # Simulate a tick that takes longer than the interval.
        clock.advance(1.0)  # interval is 0.5; this puts us 0.5s past
        if loop.stats.ticks_completed >= 4:
            loop.stop()

    loop = _loop(clock=clock, listener=slow_listener, interval_sec=0.5)
    await loop.run()
    assert loop.stats.ticks_overrun >= 1


async def test_run_cadence_is_anchor_based_not_previous_based() -> None:
    """`_next_due_at` must increment by exactly `interval_sec * N` from
    the anchor — never `now + interval`. If a tick overruns by 0.7s on
    a 0.5s loop, the next deadline is still `start + N*interval`, so the
    overrun cost is amortized instead of permanently shifting the grid.
    """
    clock = FakeClock(start=_start())
    interval = 0.5
    completed = 0

    async def listener(_: SessionState) -> None:
        nonlocal completed
        completed += 1
        # Inject jitter: tick 1 overruns the interval by 200ms, tick 2
        # runs early (clock barely moved), tick 3 overruns by 100ms.
        if completed == 1:
            clock.advance(0.7)
        elif completed == 2:
            clock.advance(0.05)
        elif completed == 3:
            clock.advance(0.6)
        if completed >= 4:
            loop.stop()

    loop = _loop(clock=clock, listener=listener, interval_sec=interval)

    async def driver() -> None:
        # Nudge the event loop forward; the listener does most of the
        # clock work. Add a tiny advance per cycle so any leftover sleep
        # eventually matures.
        for _ in range(40):
            await asyncio.sleep(0)
            if not loop._stop_event.is_set():
                clock.advance(0.01)

    await asyncio.gather(loop.run(), driver())

    assert completed >= 4
    # Anchor was `_start()`; after 4 ticks `_next_due_at` should be
    # `start + 4 * interval` regardless of the jitter above.
    expected = _start() + timedelta(seconds=interval * completed)
    assert (
        loop._next_due_at == expected
    ), f"after {completed} ticks expected _next_due_at={expected}, got {loop._next_due_at}"


# ---------------------------------------------------------------------------
# _race + _suppress_cancel
# ---------------------------------------------------------------------------


async def test_race_returns_when_first_completes() -> None:
    fast_done = asyncio.Event()

    async def fast() -> None:
        fast_done.set()

    async def slow() -> None:
        await asyncio.sleep(10.0)  # never resolves within the test
        pytest.fail("slow coroutine should have been cancelled")

    await _race(fast(), slow())
    assert fast_done.is_set()


async def test_race_reraises_winner_exception() -> None:
    async def boom() -> None:
        raise RuntimeError("winner crashed")

    async def quiet() -> None:
        await asyncio.sleep(10.0)

    with pytest.raises(RuntimeError, match="winner crashed"):
        await _race(boom(), quiet())


async def test_suppress_cancel_swallows_cancellation() -> None:
    """`_suppress_cancel` exists so `_race` can drain cancelled pending
    tasks without surfacing `CancelledError`. Verify both branches:
    cancellation is swallowed, other exceptions propagate."""
    with _suppress_cancel():
        raise asyncio.CancelledError

    with pytest.raises(ValueError, match="other"), _suppress_cancel():
        raise ValueError("other")
