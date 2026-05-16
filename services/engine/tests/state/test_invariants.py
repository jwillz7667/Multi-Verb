"""Property-based invariants on `StateStore` projections (brief §14 Phase 2).

The brief calls out, verbatim:
    "invariants like sum of speaking_time across participants
    <= session elapsed time."

Three invariants are checked here with Hypothesis:

  * **Speaking-time additivity:** the sum of every participant's total
    speaking time, clamped to `[started_at, t]`, cannot exceed
    `elapsed_sec * n_participants` (each participant can speak the
    whole session, but cannot speak *more* than the session has run).
  * **Fair share:** `fair_share_pct ≈ 100 / n` and the sum of fair
    shares equals 100% (within float tolerance).
  * **Actual share bounds:** every projected `actual_share` is inside
    `[0, 100]` and the sum across participants is either 0 (nobody
    spoke in the window) or 100 (within float tolerance).

The strategies generate random but well-formed event streams so the
store always has consistent input.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hypothesis import HealthCheck, given, settings, strategies as st

from verbio_engine.state import (
    ParticipantJoinEvent,
    StateStore,
    UtteranceFinalEvent,
)

_START = datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)

_FLOAT_TOLERANCE = 1e-6


def _segments_strategy(
    participant_id: str, *, max_duration_sec: float
) -> st.SearchStrategy[list[UtteranceFinalEvent]]:
    """Generate a list of non-overlapping finals for one participant."""

    @st.composite
    def build(draw: st.DrawFn) -> list[UtteranceFinalEvent]:
        n = draw(st.integers(min_value=0, max_value=6))
        out: list[UtteranceFinalEvent] = []
        cursor_sec = float(draw(st.floats(min_value=0.0, max_value=2.0)))
        for i in range(n):
            duration = float(draw(st.floats(min_value=0.5, max_value=15.0)))
            if cursor_sec + duration > max_duration_sec:
                break
            start = _START + timedelta(seconds=cursor_sec)
            end = _START + timedelta(seconds=cursor_sec + duration)
            out.append(
                UtteranceFinalEvent(
                    ts=end,
                    utterance_id=f"u-{participant_id}-{i}",
                    participant_id=participant_id,
                    text=f"segment-{i}",
                    start_ts=start,
                    end_ts=end,
                    confidence=0.9,
                    is_backchannel=False,
                )
            )
            gap = float(draw(st.floats(min_value=0.1, max_value=4.0)))
            cursor_sec += duration + gap
        return out

    return build()


@st.composite
def _stream(draw: st.DrawFn) -> tuple[list[str], float, list[UtteranceFinalEvent]]:
    """Pick N participants, a session duration, and per-participant finals."""
    n = draw(st.integers(min_value=1, max_value=4))
    duration_sec = float(draw(st.floats(min_value=20.0, max_value=600.0)))
    participants = [f"p{i}" for i in range(n)]
    finals: list[UtteranceFinalEvent] = []
    for pid in participants:
        finals.extend(draw(_segments_strategy(pid, max_duration_sec=duration_sec - 1.0)))
    return participants, duration_sec, finals


def _make_store(participants: list[str], finals: list[UtteranceFinalEvent]) -> StateStore:
    store = StateStore(session_id=uuid4(), started_at=_START)
    for pid in participants:
        store.record(ParticipantJoinEvent(ts=_START, participant_id=pid, display_name=pid))
    for ev in finals:
        store.record(ev)
    return store


# ---------------------------------------------------------------------------
# Invariant: speaking_time accounting
# ---------------------------------------------------------------------------


@given(_stream())
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_total_speaking_time_bounded_by_elapsed_per_participant(
    stream: tuple[list[str], float, list[UtteranceFinalEvent]],
) -> None:
    participants, duration_sec, finals = stream
    store = _make_store(participants, finals)
    snap = store.advance_to(_START + timedelta(seconds=duration_sec))

    elapsed = snap.elapsed_sec
    assert elapsed >= 0.0
    # No single participant can have spoken more than the elapsed window.
    for p in snap.participants.values():
        assert p.speaking_time_total_sec <= elapsed + _FLOAT_TOLERANCE


@given(_stream())
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_per_participant_total_geq_per_window_aggregates(
    stream: tuple[list[str], float, list[UtteranceFinalEvent]],
) -> None:
    participants, duration_sec, finals = stream
    store = _make_store(participants, finals)
    snap = store.advance_to(_START + timedelta(seconds=duration_sec))

    for p in snap.participants.values():
        assert p.speaking_time_last_5min_sec <= p.speaking_time_total_sec + _FLOAT_TOLERANCE
        assert p.speaking_time_last_60sec <= p.speaking_time_total_sec + _FLOAT_TOLERANCE
        assert p.speaking_time_last_60sec <= p.speaking_time_last_5min_sec + _FLOAT_TOLERANCE


# ---------------------------------------------------------------------------
# Invariant: fair share
# ---------------------------------------------------------------------------


@given(_stream())
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_fair_share_pct_is_100_over_n(
    stream: tuple[list[str], float, list[UtteranceFinalEvent]],
) -> None:
    participants, duration_sec, finals = stream
    store = _make_store(participants, finals)
    snap = store.advance_to(_START + timedelta(seconds=duration_sec))

    n = len(snap.participants)
    if n == 0:
        return  # vacuously true; nothing to assert
    expected = 100.0 / n
    for p in snap.participants.values():
        assert abs(p.fair_share_pct - expected) < _FLOAT_TOLERANCE

    total_fair = sum(p.fair_share_pct for p in snap.participants.values())
    assert abs(total_fair - 100.0) < _FLOAT_TOLERANCE


# ---------------------------------------------------------------------------
# Invariant: actual share
# ---------------------------------------------------------------------------


@given(_stream())
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_actual_share_in_bounds_and_sums_to_zero_or_hundred(
    stream: tuple[list[str], float, list[UtteranceFinalEvent]],
) -> None:
    participants, duration_sec, finals = stream
    store = _make_store(participants, finals)
    snap = store.advance_to(_START + timedelta(seconds=duration_sec))

    for p in snap.participants.values():
        assert 0.0 <= p.actual_share_last_5min_pct <= 100.0

    total_share = sum(p.actual_share_last_5min_pct for p in snap.participants.values())
    if total_share > 0.0:
        assert abs(total_share - 100.0) < 1e-3


# ---------------------------------------------------------------------------
# Invariant: tick monotonicity
# ---------------------------------------------------------------------------


@given(st.integers(min_value=1, max_value=20))
@settings(max_examples=50, deadline=None)
def test_tick_ids_strictly_monotonic(n_ticks: int) -> None:
    store = StateStore(session_id=uuid4(), started_at=_START)
    snaps = [store.advance_to(_START + timedelta(seconds=i)) for i in range(n_ticks)]
    assert [s.tick_id for s in snaps] == list(range(n_ticks))


# ---------------------------------------------------------------------------
# Invariant: snapshot is a pure function of the event stream
# ---------------------------------------------------------------------------


@given(_stream())
@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_two_stores_with_same_events_project_equal_snapshots(
    stream: tuple[list[str], float, list[UtteranceFinalEvent]],
) -> None:
    participants, duration_sec, finals = stream

    def make() -> StateStore:
        s = StateStore(session_id=uuid4(), started_at=_START)
        for pid in participants:
            s.record(ParticipantJoinEvent(ts=_START, participant_id=pid, display_name=pid))
        for ev in finals:
            s.record(ev)
        return s

    a = make().advance_to(_START + timedelta(seconds=duration_sec))
    b = make().advance_to(_START + timedelta(seconds=duration_sec))

    # session_id differs (random uuid4), so dump and ignore it.
    a_dump = a.model_dump(mode="json", exclude={"session_id"})
    b_dump = b.model_dump(mode="json", exclude={"session_id"})
    assert a_dump == b_dump
