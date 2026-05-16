"""Unit tests for `agent.runtime` — no DB, no LiveKit.

These exercise the pure-Python pieces of `SessionRuntime`: snapshot
parsing, role inference, error surface, and the tick-loop wiring
(`start_tick_loop` / `stop_tick_loop` / `_record_state_event`). The
DB-touching transitions are covered by `tests/agent/test_runtime_integration.py`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from verbio_engine.agent.runtime import (
    ParticipantSnapshot,
    SessionRuntime,
    _parse_role,
)
from verbio_engine.state import ParticipantJoinEvent
from verbio_engine.tick_loop import FakeClock


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "participant"),
        ("", "participant"),
        ('{"role": "researcher"}', "researcher"),
        ('{"role": "moderator"}', "moderator"),
        ('{"role": "participant"}', "participant"),
        # Default-deny when role is missing — never silently grant
        # researcher privileges.
        ('{"display_name": "Maya"}', "participant"),
        # Malformed JSON falls back to participant.
        ("not-json", "participant"),
        # Unknown role string falls back to participant.
        ('{"role": "admin"}', "participant"),
        # Non-object JSON (e.g. an array) falls back to participant.
        ('["researcher"]', "participant"),
    ],
)
def test_parse_role_default_denies_privileged_roles(metadata: str | None, expected: str) -> None:
    assert _parse_role(metadata) == expected


def test_from_livekit_uses_identity_when_display_name_blank() -> None:
    snap = ParticipantSnapshot.from_livekit(
        identity="id-abc",
        display_name="",
        metadata=None,
    )

    assert snap.identity == "id-abc"
    assert snap.display_name == "id-abc"
    assert snap.role == "participant"


def test_from_livekit_preserves_metadata_raw() -> None:
    raw = '{"role": "researcher", "org_id": "o-123"}'
    snap = ParticipantSnapshot.from_livekit(
        identity="id-r1",
        display_name="Researcher 1",
        metadata=raw,
    )

    assert snap.role == "researcher"
    assert snap.metadata_raw == raw


# ---------------------------------------------------------------------------
# participant_id cache + await_participant_id — pure asyncio, no DB.
# ---------------------------------------------------------------------------


def test_participant_id_for_returns_none_when_unknown() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    assert rt.participant_id_for("ghost") is None


async def test_await_participant_id_resolves_when_event_fires() -> None:
    """`track_subscribed` may race ahead of `participant_connected`.

    The transcriber awaits the per-identity Event; once the join
    handler populates the cache + sets the event, the wait returns.
    """
    import uuid

    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    pid = uuid.uuid4()

    async def _resolve_later() -> None:
        # Let the awaiter park on the Event first.
        await asyncio.sleep(0)
        rt._participant_ids["id-a"] = pid
        rt._participant_events.setdefault("id-a", asyncio.Event()).set()

    waker = asyncio.create_task(_resolve_later())
    try:
        got = await rt.await_participant_id("id-a", timeout=1.0)
    finally:
        await waker
    assert got == pid


async def test_await_participant_id_returns_immediately_when_already_cached() -> None:
    import uuid

    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    pid = uuid.uuid4()
    rt._participant_ids["id-cached"] = pid

    # Should not raise TimeoutError even with a tiny timeout.
    got = await rt.await_participant_id("id-cached", timeout=0.001)
    assert got == pid


async def test_await_participant_id_times_out_when_never_joined() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    with pytest.raises(asyncio.TimeoutError):
        await rt.await_participant_id("never", timeout=0.05)


# ---------------------------------------------------------------------------
# Tick loop wiring — no DB, no listener I/O.
#
# `stop_tick_loop()` sets the loop's stop event synchronously *before*
# awaiting `wait_for`, so the tick task sees the event the very first
# time it evaluates the `while` guard and exits without ever calling the
# listener. That's how these tests get away with `session_factory=None`:
# the listener is constructed (cheap), never invoked (would crash).
# ---------------------------------------------------------------------------


def _utc(year: int = 2026, month: int = 5, day: int = 16) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=UTC)


async def test_start_tick_loop_is_idempotent() -> None:
    """Second call must return the same task — re-spawning would leak ticks."""
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    rt._session_id = uuid.uuid4()
    started = _utc()
    clock = FakeClock(start=started)

    task1 = rt.start_tick_loop(started_at=started, clock=clock, interval_sec=60.0)
    store1 = rt._state_store
    loop1 = rt._tick_loop
    task2 = rt.start_tick_loop(started_at=started, clock=clock, interval_sec=60.0)

    assert task1 is task2
    # The state machinery is the original — second call short-circuited.
    assert rt._state_store is store1
    assert rt._tick_loop is loop1

    await rt.stop_tick_loop()


async def test_start_tick_loop_constructs_store_and_loop() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    rt._session_id = uuid.uuid4()
    started = _utc()
    clock = FakeClock(start=started)

    rt.start_tick_loop(started_at=started, clock=clock, interval_sec=60.0)

    assert rt._state_store is not None
    assert rt._tick_loop is not None
    assert rt._tick_task is not None
    assert not rt._tick_task.done()

    await rt.stop_tick_loop()


async def test_stop_tick_loop_is_noop_before_start() -> None:
    """A worker that crashed before `start_tick_loop` must shut down cleanly."""
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    # No exception, no log, no state mutation.
    await rt.stop_tick_loop()
    assert rt._tick_loop is None
    assert rt._tick_task is None


async def test_stop_tick_loop_clears_task_and_loop_references() -> None:
    """After stop, the runtime is back in its pre-start shape so a
    second `start_tick_loop` would build fresh machinery."""
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    rt._session_id = uuid.uuid4()
    started = _utc()
    clock = FakeClock(start=started)

    task = rt.start_tick_loop(started_at=started, clock=clock, interval_sec=60.0)
    await rt.stop_tick_loop()

    assert rt._tick_loop is None
    assert rt._tick_task is None
    # The task itself finished cleanly (stop event short-circuited the
    # loop body before tick_once ran).
    assert task.done()
    assert not task.cancelled()


async def test_stop_tick_loop_cancels_when_listener_hangs() -> None:
    """If a misbehaving listener blocks forever, stop must bound
    shutdown via `timeout` and still clear the runtime's refs."""
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    rt._session_id = uuid.uuid4()
    started = _utc()
    clock = FakeClock(start=started)

    rt.start_tick_loop(started_at=started, clock=clock, interval_sec=60.0)

    hung = asyncio.Event()  # never set anywhere — the listener parks forever.

    async def _hang(_state: object) -> None:
        await hung.wait()

    # Replace before the tick task gets scheduled; first tick_once will
    # dispatch to `_hang` and block, leaving stop_event invisible. We
    # alias `rt._tick_loop` so mypy doesn't latch the non-None narrowing
    # onto the runtime attribute itself (we re-assert it as None below).
    tick_loop = rt._tick_loop
    assert tick_loop is not None
    tick_loop._listener = _hang

    # Yield so the task enters tick_once and parks inside the listener.
    await asyncio.sleep(0)

    await rt.stop_tick_loop(timeout=0.05)

    assert rt._tick_loop is None
    assert rt._tick_task is None


def test_record_state_event_is_noop_when_store_not_started() -> None:
    """The worker calls `_record_state_event` from participant callbacks,
    which can fire before `start_tick_loop` if the worker is racing the
    initial burst of LiveKit events. Must be a silent no-op, not a crash."""
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    rt._session_id = uuid.uuid4()

    rt._record_state_event(
        ParticipantJoinEvent(
            ts=_utc(),
            participant_id=str(uuid.uuid4()),
            display_name="P",
        )
    )

    assert rt._state_store is None


def test_clock_now_returns_aware_utc() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    now = rt._clock_now()
    assert now.tzinfo is UTC
