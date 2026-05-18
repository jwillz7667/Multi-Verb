"""Concurrent-session load test (P7 L6, brief §14).

Drives 10 in-process moderator sessions simultaneously to prove the
state-store/tick-loop core stays stable under the production fanout the
brief calls out: ten rooms, five participants each, sixty minutes.
Provider I/O (LiveKit, Deepgram, mouth LLM, TTS) is replaced with
realistic synthetic event streams — what we are measuring here is the
deterministic Python core, not third-party latency.

Why in-process and not subprocesses
-----------------------------------

In production each session is its own engine process (one per LiveKit
room). Running them all in one event loop here is *harder* than
production: every coroutine contends for the same asyncio scheduler.
If the per-tick math holds up under that contention, it will hold up
when each session has its own CPU + memory pool.

Why a `load` marker
-------------------

Default `pytest` runs exclude `-m load` via `addopts` in `pyproject.toml`.
Run explicitly with `uv run pytest -m load` to exercise this file. CI
runs the short 30-second variant on every PR (catches regressions);
release validation flips `VERBIO_LOAD_DURATION_SEC=3600` for the full
60-minute soak the brief demands.

Pass criteria
-------------

- Zero listener failures across all sessions.
- Tick-overrun rate < 2 % of total ticks (5 % of ticks may slip the
  500 ms cadence under contention; anything beyond suggests the math is
  starving the event loop).
- Worst-session p95 per-tick latency < 100 ms.
- RSS growth across the run < 250 MB (10 sessions x ~25 MB headroom
  each; anything bigger points to an event-log leak in `StateStore`).
- All sessions completed (no coroutine hung).
"""

from __future__ import annotations

import asyncio
import os
import platform
import random
import resource
import statistics
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from verbio_engine.domain import SessionState
from verbio_engine.state import (
    ParticipantJoinEvent,
    StateStore,
    UtteranceFinalEvent,
    VadOffEvent,
    VadOnEvent,
)
from verbio_engine.tick_loop import TickLoop, WallClock

# ---------------------------------------------------------------------------
# Brief-spec dimensions (§14 Phase 7)
# ---------------------------------------------------------------------------

_SESSIONS = 10
_PARTICIPANTS_PER_SESSION = 5
_TICK_INTERVAL_SEC = 0.5  # 2 Hz, the brief's default cadence.

# Default duration short enough to fit a PR CI run. Release-validation
# overrides via env var to hit the brief's 60-minute target.
_DEFAULT_DURATION_SEC = 30
_DURATION_ENV = "VERBIO_LOAD_DURATION_SEC"

# Per-participant event cadence. A focus-group participant typically
# speaks once every 3-10 seconds; we sample uniformly in that range so
# the load is realistic, not metronomic.
_MIN_INTER_UTTERANCE_SEC = 3.0
_MAX_INTER_UTTERANCE_SEC = 10.0

# How long each synthetic utterance is. STT finals on focus-group audio
# average ~2-6 s of speech; this drives speaking-time accounting and
# `silence_run_sec` resets in the state store.
_MIN_UTTERANCE_SEC = 2.0
_MAX_UTTERANCE_SEC = 6.0

# Pass-criteria thresholds, documented at module top.
_MAX_OVERRUN_FRACTION = 0.02
_MAX_P95_TICK_LATENCY_MS = 100.0
_MAX_RSS_GROWTH_BYTES = 250 * 1024 * 1024
_MAX_LISTENER_FAILURES = 0


# ---------------------------------------------------------------------------
# Per-session result the orchestrator collects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _SessionResult:
    session_id: uuid.UUID
    ticks: int
    overruns: int
    listener_failures: int
    tick_latencies_ms: list[float]
    snapshots_seen: int

    @property
    def p95_tick_latency_ms(self) -> float:
        if not self.tick_latencies_ms:
            return 0.0
        sorted_ms = sorted(self.tick_latencies_ms)
        # statistics.quantiles requires n>=2; for tiny runs fall back.
        if len(sorted_ms) < 20:
            return sorted_ms[-1]
        return statistics.quantiles(sorted_ms, n=20)[18]  # 95th percentile


@dataclass(slots=True)
class _LoadResult:
    duration_sec: float
    sessions: list[_SessionResult] = field(default_factory=list)
    rss_growth_bytes: int = 0

    @property
    def total_ticks(self) -> int:
        return sum(s.ticks for s in self.sessions)

    @property
    def total_overruns(self) -> int:
        return sum(s.overruns for s in self.sessions)

    @property
    def total_listener_failures(self) -> int:
        return sum(s.listener_failures for s in self.sessions)

    @property
    def worst_p95_latency_ms(self) -> float:
        return max((s.p95_tick_latency_ms for s in self.sessions), default=0.0)


# ---------------------------------------------------------------------------
# RSS measurement (POSIX only; the engine runs on Linux in prod)
# ---------------------------------------------------------------------------


def _peak_rss_bytes() -> int:
    """Process RSS peak in bytes. Wraps `getrusage` unit handling.

    `ru_maxrss` is kilobytes on Linux and bytes on macOS (the BSD
    heritage). We normalise to bytes so the caller does not have to
    care which CI runner is executing the test.
    """
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if platform.system() == "Darwin":
        return rss
    return rss * 1024


# ---------------------------------------------------------------------------
# Per-session synthetic load
# ---------------------------------------------------------------------------


def _build_store(session_id: uuid.UUID, started_at: datetime) -> StateStore:
    return StateStore(session_id=session_id, started_at=started_at)


def _seed_participants(store: StateStore, now: datetime, count: int) -> list[str]:
    """Inject N participant_join events and return their ids.

    Real production flow has participants joining over a few seconds at
    session start; for a load test the join order does not matter, only
    that the StateStore sees all five before the first utterance.
    """
    ids: list[str] = []
    for i in range(count):
        pid = f"p{i}"
        store.record(
            ParticipantJoinEvent(
                ts=now,
                participant_id=pid,
                display_name=f"Participant {i}",
            )
        )
        ids.append(pid)
    return ids


async def _drive_participants(
    store: StateStore,
    participant_ids: list[str],
    stop_event: asyncio.Event,
    rng: random.Random,
) -> None:
    """Emit a steady stream of VAD + utterance events per participant.

    Runs until `stop_event` is set. Each participant runs as its own
    coroutine — they overlap naturally because focus-group cross-talk
    is exactly what the engine has to cope with.
    """

    async def one(pid: str) -> None:
        # Stagger the first utterance so all five do not fire in lockstep.
        await asyncio.sleep(rng.uniform(0.0, _MAX_INTER_UTTERANCE_SEC))
        utterance_seq = 0
        while not stop_event.is_set():
            start_wall = datetime.now(UTC)
            duration = rng.uniform(_MIN_UTTERANCE_SEC, _MAX_UTTERANCE_SEC)
            end_wall = start_wall + timedelta(seconds=duration)

            store.record(VadOnEvent(ts=start_wall, participant_id=pid))
            # In real life the STT final lags VAD-off by ~200-600 ms.
            # Emit VAD-off first, then the final; the store sorts on `ts`
            # so the projection is identical regardless of arrival order.
            store.record(VadOffEvent(ts=end_wall, participant_id=pid))
            store.record(
                UtteranceFinalEvent(
                    ts=end_wall + timedelta(milliseconds=300),
                    utterance_id=f"{pid}-u{utterance_seq}",
                    participant_id=pid,
                    text=f"synthetic utterance {utterance_seq} from {pid}",
                    start_ts=start_wall,
                    end_ts=end_wall,
                    confidence=0.95,
                )
            )
            utterance_seq += 1

            # Sleep through the rest of this utterance plus the
            # inter-utterance gap. Bounded by the stop event so the
            # coroutine never outlives the load run by more than one
            # short gap.
            gap = rng.uniform(_MIN_INTER_UTTERANCE_SEC, _MAX_INTER_UTTERANCE_SEC)
            await _sleep_or_stop(duration + gap, stop_event)

    await asyncio.gather(*(one(pid) for pid in participant_ids))


async def _sleep_or_stop(seconds: float, stop_event: asyncio.Event) -> None:
    """Wait up to `seconds`, returning early if `stop_event` is set."""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return


async def _run_one_session(
    session_index: int,
    *,
    duration_sec: float,
    rng: random.Random,
) -> _SessionResult:
    session_id = uuid.uuid4()
    started_at = datetime.now(UTC)
    store = _build_store(session_id, started_at)

    # Latency timer state lives in a list captured by the listener
    # closure. The listener runs after every successful tick.
    tick_latencies_ms: list[float] = []
    snapshots_seen = [0]  # mutable count visible to closure
    tick_started_at_ns = [time.monotonic_ns()]

    async def listener(_: SessionState) -> None:
        elapsed_ns = time.monotonic_ns() - tick_started_at_ns[0]
        tick_latencies_ms.append(elapsed_ns / 1_000_000)
        snapshots_seen[0] += 1

    participant_ids = _seed_participants(store, started_at, _PARTICIPANTS_PER_SESSION)

    loop = TickLoop(
        store=store,
        clock=WallClock(),
        interval_sec=_TICK_INTERVAL_SEC,
        listener=listener,
        # `scheduled_end_at` gives a hard upper bound — if the stop
        # signal is somehow missed, the loop self-terminates.
        scheduled_end_at=started_at + timedelta(seconds=duration_sec + 5),
    )

    stop_event = asyncio.Event()

    # Hook to capture tick start time. The TickLoop does not expose a
    # pre-tick hook directly; we approximate by resetting the timer in
    # a wrapper around `tick_once`. This adds one ns-resolution monotonic
    # read per tick — well below the measurement floor.
    original_tick_once = loop.tick_once

    async def timed_tick_once() -> SessionState:
        tick_started_at_ns[0] = time.monotonic_ns()
        return await original_tick_once()

    loop.tick_once = timed_tick_once  # type: ignore[method-assign]

    participants_task = asyncio.create_task(
        _drive_participants(store, participant_ids, stop_event, rng),
        name=f"load.session.{session_index}.participants",
    )

    run_task = asyncio.create_task(loop.run(), name=f"load.session.{session_index}.tick")

    # Run for the configured wall-clock duration, then signal stop.
    await asyncio.sleep(duration_sec)
    stop_event.set()
    loop.stop()

    # Tick loop drains on next iteration (one interval at most). The
    # participants task observes `stop_event` and unwinds. Wait for
    # both; surface failures as test failures via `result()` re-raise.
    await asyncio.wait_for(run_task, timeout=_TICK_INTERVAL_SEC * 4)
    await asyncio.wait_for(participants_task, timeout=_MAX_INTER_UTTERANCE_SEC * 2)

    return _SessionResult(
        session_id=session_id,
        ticks=loop.stats.ticks_completed,
        overruns=loop.stats.ticks_overrun,
        listener_failures=loop.stats.listener_failures,
        tick_latencies_ms=tick_latencies_ms,
        snapshots_seen=snapshots_seen[0],
    )


# ---------------------------------------------------------------------------
# Top-level test
# ---------------------------------------------------------------------------


@pytest.mark.load
async def test_ten_concurrent_sessions_stay_within_budget() -> None:
    duration_sec = float(os.environ.get(_DURATION_ENV, _DEFAULT_DURATION_SEC))
    # Seed the per-session RNG deterministically from the run id so
    # rerunning the test after a regression reproduces the same load
    # shape.
    base_seed = int(os.environ.get("VERBIO_LOAD_SEED", "20260517"))

    rss_before = _peak_rss_bytes()
    started_at = time.monotonic()

    runners = [
        _run_one_session(
            i,
            duration_sec=duration_sec,
            # Reproducible load shape per session, not cryptographic.
            rng=random.Random(base_seed + i),  # noqa: S311
        )
        for i in range(_SESSIONS)
    ]
    sessions = await asyncio.gather(*runners)

    elapsed_sec = time.monotonic() - started_at
    rss_after = _peak_rss_bytes()
    rss_growth_bytes = max(0, rss_after - rss_before)

    result = _LoadResult(
        duration_sec=elapsed_sec,
        sessions=list(sessions),
        rss_growth_bytes=rss_growth_bytes,
    )

    # Surface a one-line summary in pytest output so a soak run that
    # passes still leaves a record of the numbers it hit.
    print(
        "\nload summary: "
        f"sessions={len(result.sessions)}, "
        f"duration_sec={result.duration_sec:.1f}, "
        f"total_ticks={result.total_ticks}, "
        f"overruns={result.total_overruns}, "
        f"listener_failures={result.total_listener_failures}, "
        f"worst_p95_tick_ms={result.worst_p95_latency_ms:.2f}, "
        f"rss_growth_mb={result.rss_growth_bytes / 1024 / 1024:.1f}"
    )

    # ---------------------- Brief-mandated assertions -----------------
    assert (
        result.total_listener_failures <= _MAX_LISTENER_FAILURES
    ), f"listener failures: {result.total_listener_failures} > {_MAX_LISTENER_FAILURES}"

    assert result.total_ticks > 0, "no ticks were executed across any session"

    overrun_fraction = result.total_overruns / result.total_ticks
    assert overrun_fraction < _MAX_OVERRUN_FRACTION, (
        f"tick overrun rate {overrun_fraction:.3f} exceeds budget {_MAX_OVERRUN_FRACTION:.3f} "
        f"(overruns={result.total_overruns}, ticks={result.total_ticks})"
    )

    assert result.worst_p95_latency_ms < _MAX_P95_TICK_LATENCY_MS, (
        f"worst-session p95 tick latency {result.worst_p95_latency_ms:.2f} ms exceeds "
        f"budget {_MAX_P95_TICK_LATENCY_MS} ms"
    )

    assert result.rss_growth_bytes < _MAX_RSS_GROWTH_BYTES, (
        f"RSS growth {result.rss_growth_bytes / 1024 / 1024:.1f} MB exceeds budget "
        f"{_MAX_RSS_GROWTH_BYTES / 1024 / 1024:.0f} MB — suspected event-log leak in StateStore"
    )

    # All sessions should have produced some snapshots — a session
    # whose listener saw zero snapshots is a hung tick loop.
    for s in result.sessions:
        assert (
            s.snapshots_seen > 0
        ), f"session {s.session_id} produced no snapshots — tick loop hung"
