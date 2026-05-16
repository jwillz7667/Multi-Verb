"""Field-by-field coverage of `StateStore.advance_to` per brief §5.1.

Every `ParticipantState` field has at least one test that proves the
projection produces the expected value from a curated event stream.
Brief §14 Phase 2 done-when calls for one unit test per state field —
that list is enumerated below.

The store is event-sourced, so each test reads:
  1. Build a store with a known start time.
  2. Record a sequence of events.
  3. Advance to a target tick time.
  4. Assert on the projected snapshot.

No fixtures shared across tests on purpose — each test is a complete
narrative and survives refactors without ripple.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from verbio_engine.domain import (
    ParticipantFlags,
    ParticipantState,
    QuietnessBudget,
    SessionState,
)
from verbio_engine.state import (
    ParticipantJoinEvent,
    ParticipantLeaveEvent,
    StateStore,
    StateStoreConfig,
    UtteranceFinalEvent,
    VadOffEvent,
    VadOnEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start() -> datetime:
    """Fixed UTC anchor — deterministic for every test in this module."""
    return datetime(2026, 5, 16, 12, 0, 0, tzinfo=UTC)


def _store(
    *,
    started_at: datetime | None = None,
    config: StateStoreConfig | None = None,
    quietness_budget: QuietnessBudget | None = None,
    scheduled_end_at: datetime | None = None,
) -> StateStore:
    return StateStore(
        session_id=uuid4(),
        started_at=started_at if started_at is not None else _start(),
        scheduled_end_at=scheduled_end_at,
        config=config,
        quietness_budget=quietness_budget,
    )


def _join(pid: str, *, at: datetime, name: str | None = None) -> ParticipantJoinEvent:
    return ParticipantJoinEvent(
        ts=at, participant_id=pid, display_name=name if name is not None else pid
    )


def _final(
    pid: str,
    *,
    start: datetime,
    duration_sec: float,
    text: str = "hi",
    is_backchannel: bool = False,
    confidence: float | None = 0.95,
    utterance_id: str | None = None,
) -> UtteranceFinalEvent:
    end = start + timedelta(seconds=duration_sec)
    return UtteranceFinalEvent(
        ts=end,
        utterance_id=utterance_id if utterance_id is not None else f"u-{pid}-{start.isoformat()}",
        participant_id=pid,
        text=text,
        start_ts=start,
        end_ts=end,
        confidence=confidence,
        is_backchannel=is_backchannel,
    )


# ---------------------------------------------------------------------------
# Tick mechanics
# ---------------------------------------------------------------------------


def test_first_tick_has_tick_id_zero_and_empty_participants() -> None:
    store = _store()
    snap = store.advance_to(_start())

    assert isinstance(snap, SessionState)
    assert snap.tick_id == 0
    assert snap.participants == {}
    assert snap.elapsed_sec == 0.0
    assert snap.currently_speaking_count == 0


def test_tick_id_monotonically_increments() -> None:
    store = _store()
    base = _start()
    snaps = [store.advance_to(base + timedelta(seconds=i)) for i in range(5)]

    assert [s.tick_id for s in snaps] == [0, 1, 2, 3, 4]
    assert all(snaps[i + 1].t > snaps[i].t for i in range(len(snaps) - 1))


def test_backwards_advance_rejected() -> None:
    store = _store()
    base = _start()
    store.advance_to(base + timedelta(seconds=5))

    with pytest.raises(ValueError, match="monotonic"):
        store.advance_to(base + timedelta(seconds=4))


def test_elapsed_sec_tracks_wall_clock() -> None:
    store = _store()
    snap = store.advance_to(_start() + timedelta(seconds=42))
    assert snap.elapsed_sec == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# Participant join / leave
# ---------------------------------------------------------------------------


def test_join_event_adds_participant_with_joined_at() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base + timedelta(seconds=1), name="Maya"))

    snap = store.advance_to(base + timedelta(seconds=2))
    assert "p1" in snap.participants
    p = snap.participants["p1"]
    assert p.display_name == "Maya"
    assert p.joined_at == base + timedelta(seconds=1)


def test_late_arriving_join_event_buffers_in_order() -> None:
    # Events recorded out of order should fold at the next tick whose
    # boundary covers them — proving the buffer is time-sorted.
    store = _store()
    base = _start()
    store.record(_join("p2", at=base + timedelta(seconds=3)))
    store.record(_join("p1", at=base + timedelta(seconds=1)))

    snap = store.advance_to(base + timedelta(seconds=5))
    p1 = snap.participants["p1"]
    p2 = snap.participants["p2"]
    assert p1.joined_at < p2.joined_at


def test_leave_event_removes_participant() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base + timedelta(seconds=1)))
    store.record(ParticipantLeaveEvent(ts=base + timedelta(seconds=10), participant_id="p1"))

    snap = store.advance_to(base + timedelta(seconds=11))
    assert "p1" not in snap.participants


def test_rejoin_preserves_display_name_update() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base + timedelta(seconds=1), name="Maya"))
    store.record(_join("p1", at=base + timedelta(seconds=2), name="Maya R."))

    snap = store.advance_to(base + timedelta(seconds=3))
    assert snap.participants["p1"].display_name == "Maya R."


# ---------------------------------------------------------------------------
# Speaking-time aggregates
# ---------------------------------------------------------------------------


def test_speaking_time_total_sums_segments() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=3.0))
    store.record(_final("p1", start=base + timedelta(seconds=10), duration_sec=2.5))

    snap = store.advance_to(base + timedelta(seconds=15))
    assert snap.participants["p1"].speaking_time_total_sec == pytest.approx(5.5)


def test_speaking_time_last_5min_window_excludes_old_segments() -> None:
    cfg = StateStoreConfig(last_5min_window_sec=300.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    # First final 400s ago — outside the 5-min window at t=base+500.
    store.record(_final("p1", start=base + timedelta(seconds=10), duration_sec=4.0))
    # Recent final at t=base+450 — inside the window.
    store.record(_final("p1", start=base + timedelta(seconds=450), duration_sec=6.0))

    snap = store.advance_to(base + timedelta(seconds=500))
    p = snap.participants["p1"]
    assert p.speaking_time_total_sec == pytest.approx(10.0)
    assert p.speaking_time_last_5min_sec == pytest.approx(6.0)


def test_speaking_time_last_60sec_clamps_to_window_boundary() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    # 10s utterance straddling the 60-s boundary at t=base+120:
    # spans [110, 120] and beyond, but window starts at t-60 = 60.
    store.record(_final("p1", start=base + timedelta(seconds=55), duration_sec=10.0))
    # Window: [60, 120]. Segment: [55, 65]. Overlap: [60, 65] = 5s.

    snap = store.advance_to(base + timedelta(seconds=120))
    assert snap.participants["p1"].speaking_time_last_60sec == pytest.approx(5.0)


def test_turn_count_increments_per_final() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    for i in range(4):
        store.record(_final("p1", start=base + timedelta(seconds=i * 3 + 1), duration_sec=1.0))

    snap = store.advance_to(base + timedelta(seconds=20))
    assert snap.participants["p1"].turn_count == 4


def test_backchannels_do_not_increment_turn_count() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=1.0))
    store.record(
        _final(
            "p1",
            start=base + timedelta(seconds=5),
            duration_sec=0.4,
            text="mm-hm",
            is_backchannel=True,
        )
    )

    snap = store.advance_to(base + timedelta(seconds=10))
    p = snap.participants["p1"]
    assert p.turn_count == 1
    assert p.speaking_time_total_sec == pytest.approx(1.0)
    assert p.backchannel_count_last_2min == 1


def test_last_spoke_at_and_duration_track_most_recent_final() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=2), duration_sec=1.0))
    store.record(_final("p1", start=base + timedelta(seconds=10), duration_sec=2.5))

    snap = store.advance_to(base + timedelta(seconds=15))
    p = snap.participants["p1"]
    assert p.last_spoke_at == base + timedelta(seconds=12, milliseconds=500)
    assert p.last_spoke_duration_sec == pytest.approx(2.5)


def test_no_finals_leaves_last_spoke_at_none() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))

    snap = store.advance_to(base + timedelta(seconds=5))
    p = snap.participants["p1"]
    assert p.last_spoke_at is None
    assert p.last_spoke_duration_sec is None


# ---------------------------------------------------------------------------
# Engagement signals (VAD, currently_speaking, backchannels)
# ---------------------------------------------------------------------------


def test_vad_on_then_off_toggles_vad_active() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(VadOnEvent(ts=base + timedelta(seconds=1), participant_id="p1"))

    snap_active = store.advance_to(base + timedelta(seconds=2))
    assert snap_active.participants["p1"].vad_active is True
    assert snap_active.participants["p1"].is_currently_speaking is True
    assert snap_active.currently_speaking_count == 1

    store.record(VadOffEvent(ts=base + timedelta(seconds=3), participant_id="p1"))
    snap_silent = store.advance_to(base + timedelta(seconds=4))
    assert snap_silent.participants["p1"].vad_active is False
    assert snap_silent.participants["p1"].is_currently_speaking is False
    assert snap_silent.currently_speaking_count == 0


def test_is_currently_speaking_follows_vad_not_late_finals() -> None:
    # STT finals always arrive after the utterance ends, so they cannot
    # claim "still speaking" — only VAD does. A stale final landing in
    # the next tick must not flip is_currently_speaking back on.
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(VadOnEvent(ts=base + timedelta(seconds=1), participant_id="p1"))
    store.record(VadOffEvent(ts=base + timedelta(seconds=3), participant_id="p1"))
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=2.0))

    snap = store.advance_to(base + timedelta(seconds=5))
    assert snap.participants["p1"].is_currently_speaking is False
    assert snap.participants["p1"].vad_active is False


def test_backchannel_count_decays_outside_window() -> None:
    cfg = StateStoreConfig(backchannel_window_sec=120.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    # Old backchannel (200s before t=300) → outside 2-min window.
    store.record(
        _final(
            "p1",
            start=base + timedelta(seconds=100),
            duration_sec=0.3,
            text="yeah",
            is_backchannel=True,
        )
    )
    # Fresh backchannel (50s before t=300) → inside.
    store.record(
        _final(
            "p1",
            start=base + timedelta(seconds=250),
            duration_sec=0.3,
            text="mm",
            is_backchannel=True,
        )
    )

    snap = store.advance_to(base + timedelta(seconds=300))
    assert snap.participants["p1"].backchannel_count_last_2min == 1


# ---------------------------------------------------------------------------
# Interruptions
# ---------------------------------------------------------------------------


def test_interruption_increments_attacker_and_victim_counters() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    # p1 holds the floor.
    store.record(VadOnEvent(ts=base + timedelta(seconds=1), participant_id="p1"))
    # p2 cuts in while p1 is still active.
    store.record(VadOnEvent(ts=base + timedelta(seconds=2), participant_id="p2"))

    snap = store.advance_to(base + timedelta(seconds=3))
    p1 = snap.participants["p1"]
    p2 = snap.participants["p2"]
    assert p2.interruption_count == 1
    assert p2.was_interrupted_count == 0
    assert p1.was_interrupted_count == 1
    assert p1.interruption_count == 0


def test_consecutive_vad_on_no_interruption_when_other_already_silent() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    store.record(VadOnEvent(ts=base + timedelta(seconds=1), participant_id="p1"))
    store.record(VadOffEvent(ts=base + timedelta(seconds=2), participant_id="p1"))
    store.record(VadOnEvent(ts=base + timedelta(seconds=3), participant_id="p2"))

    snap = store.advance_to(base + timedelta(seconds=4))
    assert snap.participants["p2"].interruption_count == 0
    assert snap.participants["p1"].was_interrupted_count == 0


# ---------------------------------------------------------------------------
# Content signals: recent_utterances, rolling transcript
# ---------------------------------------------------------------------------


def test_recent_utterances_caps_at_five() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    for i in range(7):
        store.record(
            _final(
                "p1",
                start=base + timedelta(seconds=i * 2 + 1),
                duration_sec=0.5,
                text=f"chunk-{i}",
                utterance_id=f"u-{i}",
            )
        )

    snap = store.advance_to(base + timedelta(seconds=20))
    refs = snap.participants["p1"].recent_utterances
    assert len(refs) == 5
    # Newest five — oldest first inside the buffer.
    assert [r.utterance_id for r in refs] == [f"u-{i}" for i in range(2, 7)]


def test_rolling_transcript_2min_concatenates_in_chronological_order() -> None:
    cfg = StateStoreConfig(rolling_transcript_window_sec=120.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    # Old final outside window → excluded.
    store.record(
        _final(
            "p1",
            start=base + timedelta(seconds=10),
            duration_sec=1.0,
            text="ancient history",
        )
    )
    # Inside window.
    store.record(_final("p1", start=base + timedelta(seconds=200), duration_sec=1.0, text="first"))
    store.record(_final("p1", start=base + timedelta(seconds=210), duration_sec=1.0, text="second"))

    snap = store.advance_to(base + timedelta(seconds=240))
    assert snap.participants["p1"].rolling_transcript_2min == "first second"


def test_rolling_global_transcript_includes_all_speakers() -> None:
    cfg = StateStoreConfig(rolling_transcript_window_sec=120.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=5), duration_sec=1.0, text="alpha"))
    store.record(_final("p2", start=base + timedelta(seconds=8), duration_sec=1.0, text="beta"))

    snap = store.advance_to(base + timedelta(seconds=20))
    assert snap.rolling_global_transcript_2min == "alpha beta"


# ---------------------------------------------------------------------------
# Fairness math: fair_share_pct, actual_share_last_5min_pct
# ---------------------------------------------------------------------------


def test_fair_share_pct_equals_100_over_n() -> None:
    store = _store()
    base = _start()
    for i in range(4):
        store.record(_join(f"p{i}", at=base))

    snap = store.advance_to(base + timedelta(seconds=5))
    for p in snap.participants.values():
        assert p.fair_share_pct == pytest.approx(25.0)


def test_single_participant_actual_share_is_100_when_speaking() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=5.0))

    snap = store.advance_to(base + timedelta(seconds=10))
    p = snap.participants["p1"]
    assert p.actual_share_last_5min_pct == pytest.approx(100.0)


def test_actual_share_is_zero_when_no_speech_in_window() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))

    snap = store.advance_to(base + timedelta(seconds=5))
    for p in snap.participants.values():
        assert p.actual_share_last_5min_pct == 0.0


def test_actual_share_proportional_to_speaking_time() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    # p1: 30s, p2: 10s → 75% / 25%.
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=30.0))
    store.record(_final("p2", start=base + timedelta(seconds=40), duration_sec=10.0))

    snap = store.advance_to(base + timedelta(seconds=60))
    assert snap.participants["p1"].actual_share_last_5min_pct == pytest.approx(75.0)
    assert snap.participants["p2"].actual_share_last_5min_pct == pytest.approx(25.0)


# ---------------------------------------------------------------------------
# Derived flags
# ---------------------------------------------------------------------------


def test_dominating_flag_fires_when_share_exceeds_factor_times_fair() -> None:
    cfg = StateStoreConfig(dominating_factor=2.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    store.record(_join("p3", at=base))
    # Fair share = 33.3%. Dominating threshold = 66.7%. p1 takes 80%.
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=80.0))
    store.record(_final("p2", start=base + timedelta(seconds=85), duration_sec=10.0))
    store.record(_final("p3", start=base + timedelta(seconds=100), duration_sec=10.0))

    snap = store.advance_to(base + timedelta(seconds=120))
    assert snap.participants["p1"].flags.dominating is True
    assert snap.participants["p2"].flags.dominating is False
    assert snap.participants["p3"].flags.dominating is False


def test_dominating_flag_never_fires_for_single_participant() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=1), duration_sec=100.0))

    snap = store.advance_to(base + timedelta(seconds=120))
    assert snap.participants["p1"].flags.dominating is False


def test_silent_too_long_flag_fires_after_threshold_with_no_finals() -> None:
    cfg = StateStoreConfig(silent_too_long_sec=60.0, disengaged_silence_sec=30.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))

    snap = store.advance_to(base + timedelta(seconds=90))
    assert snap.participants["p1"].flags.silent_too_long is True


def test_silent_too_long_flag_clears_after_speaking() -> None:
    cfg = StateStoreConfig(silent_too_long_sec=60.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=70), duration_sec=2.0))

    snap = store.advance_to(base + timedelta(seconds=75))
    assert snap.participants["p1"].flags.silent_too_long is False


def test_frequently_interrupted_flag_at_threshold() -> None:
    cfg = StateStoreConfig(frequently_interrupted_threshold=2)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    # p1 holds floor; p2 cuts in three times.
    for i in range(3):
        ts = base + timedelta(seconds=10 + i * 30)
        store.record(VadOnEvent(ts=ts, participant_id="p1"))
        store.record(VadOnEvent(ts=ts + timedelta(seconds=1), participant_id="p2"))
        store.record(VadOffEvent(ts=ts + timedelta(seconds=3), participant_id="p2"))
        store.record(VadOffEvent(ts=ts + timedelta(seconds=4), participant_id="p1"))

    snap = store.advance_to(base + timedelta(seconds=120))
    assert snap.participants["p1"].flags.frequently_interrupted is True
    assert snap.participants["p2"].flags.frequently_interrupted is False


def test_disengaged_flag_requires_no_speech_and_no_backchannels() -> None:
    cfg = StateStoreConfig(disengaged_silence_sec=60.0, silent_too_long_sec=300.0)
    store = _store(config=cfg)
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    # p2 occasionally backchannels.
    store.record(
        _final(
            "p2",
            start=base + timedelta(seconds=70),
            duration_sec=0.3,
            text="mm",
            is_backchannel=True,
        )
    )

    snap = store.advance_to(base + timedelta(seconds=100))
    assert snap.participants["p1"].flags.disengaged is True
    assert snap.participants["p2"].flags.disengaged is False


# ---------------------------------------------------------------------------
# Session-level fields
# ---------------------------------------------------------------------------


def test_silence_run_sec_from_session_start_when_no_speech_ever() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))

    snap = store.advance_to(base + timedelta(seconds=33))
    assert snap.silence_run_sec == pytest.approx(33.0)


def test_silence_run_sec_resets_on_final() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_final("p1", start=base + timedelta(seconds=10), duration_sec=2.0))

    snap = store.advance_to(base + timedelta(seconds=20))
    # final ends at base+12 → silence run = 8s
    assert snap.silence_run_sec == pytest.approx(8.0)


def test_currently_speaking_count_aggregates_across_participants() -> None:
    store = _store()
    base = _start()
    store.record(_join("p1", at=base))
    store.record(_join("p2", at=base))
    store.record(_join("p3", at=base))
    store.record(VadOnEvent(ts=base + timedelta(seconds=1), participant_id="p1"))
    store.record(VadOnEvent(ts=base + timedelta(seconds=2), participant_id="p2"))

    snap = store.advance_to(base + timedelta(seconds=3))
    assert snap.currently_speaking_count == 2


def test_quietness_budget_replaced_by_update_call() -> None:
    store = _store()
    snap_default = store.advance_to(_start())
    assert snap_default.quietness_budget.max_utterances_per_10min == 3

    custom = QuietnessBudget(max_utterances_per_10min=1, min_seconds_between_utterances=120.0)
    store.update_quietness_budget(custom)
    snap_updated = store.advance_to(_start() + timedelta(seconds=1))
    assert snap_updated.quietness_budget.max_utterances_per_10min == 1
    assert snap_updated.quietness_budget.min_seconds_between_utterances == 120.0


def test_pause_and_mute_toggles_surface_on_snapshot() -> None:
    store = _store()
    store.set_pause(paused=True)
    store.set_moderator_muted(muted=True)
    snap = store.advance_to(_start())
    assert snap.is_paused is True
    assert snap.moderator_muted is True


def test_scheduled_end_at_threaded_through() -> None:
    end = _start() + timedelta(hours=1)
    store = _store(scheduled_end_at=end)
    snap = store.advance_to(_start())
    assert snap.scheduled_end_at == end


# ---------------------------------------------------------------------------
# Pydantic-level shape guarantees
# ---------------------------------------------------------------------------


def test_projected_snapshot_is_frozen() -> None:
    store = _store()
    snap = store.advance_to(_start())
    # Pydantic frozen models raise ValidationError on assignment.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        snap.tick_id = 99  # type: ignore[misc]


def test_projected_participant_state_is_a_real_participant_state() -> None:
    store = _store()
    store.record(_join("p1", at=_start()))
    snap = store.advance_to(_start() + timedelta(seconds=1))
    p = snap.participants["p1"]
    assert isinstance(p, ParticipantState)
    assert isinstance(p.flags, ParticipantFlags)
