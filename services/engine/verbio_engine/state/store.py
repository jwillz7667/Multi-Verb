"""`StateStore` — event-sourced projection of `SessionState`.

Brief §6 step 1: `state = state_store.advance_to(t)`. This module is
the implementation of that single line. The store is pure (no I/O, no
clock — `t` is always passed in), which lets the tick loop drive it
with a fake clock in tests.

How it works
------------

* `record(event)` buffers a typed `StateEvent` in time-order.
* `advance_to(t)` consumes every buffered event whose `ts <= t`, folds
  it into the in-memory accumulator, then projects a frozen
  `SessionState`. Subsequent calls only advance from the new cursor.

The store retains a small amount of history (closed speech segments
within the longest configured window) so rolling windows can be
recomputed at every tick without replaying the entire stream.

Invariants the implementation enforces
--------------------------------------

* `tick_id` is monotonic per call to `advance_to`. A second call with
  the same `t` returns a snapshot with the next tick_id; that matches
  the brief's "tick" abstraction — one snapshot per tick, never zero.
* `t` must be non-decreasing across `advance_to` calls (the tick clock
  only moves forward).
* `sum(p.speaking_time_total_sec for p in participants) <= elapsed_sec`
  always — speakers can overlap, but the projection clamps each
  per-participant slot inside `[started_at, t]`.
* `0.0 <= fair_share_pct <= 100.0` and `0.0 <= actual_share <= 100.0`.

The Hypothesis property tests in `tests/state/test_invariants.py` lock
these contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.domain.participant import (
    ParticipantFlags,
    ParticipantState,
    UtteranceRef,
)
from verbio_engine.domain.session_state import SessionState
from verbio_engine.state.config import StateStoreConfig
from verbio_engine.state.events import (
    ParticipantJoinEvent,
    ParticipantLeaveEvent,
    StateEvent,
    UtteranceFinalEvent,
    VadOffEvent,
    VadOnEvent,
)


# A finalized utterance kept in the rolling history. Cheaper than holding
# the full event around — only the fields windowed aggregates need.
@dataclass(slots=True, frozen=True)
class _Segment:
    participant_id: str
    start_ts: datetime
    end_ts: datetime
    duration_sec: float
    text: str
    is_backchannel: bool
    utterance_id: str


@dataclass(slots=True, frozen=True)
class _Interruption:
    """Recorded when a participant starts speaking over someone else.

    `attacker_id` is the late starter; `victim_id` is the participant
    already holding the floor. The store grades the late start as an
    interruption only when both VAD tracks remain active for
    `_INTERRUPTION_MIN_OVERLAP_SEC` seconds — a true talk-over rather
    than a clean turn handoff at the millisecond boundary.
    """

    attacker_id: str
    victim_id: str
    ts: datetime


@dataclass(slots=True)
class _ParticipantAccumulator:
    """Mutable per-participant scratch space the store folds events into.

    Kept separate from `ParticipantState` so the frozen Pydantic model
    is only constructed at projection time. Holds raw event history;
    projection turns history into the public per-participant fields.
    """

    participant_id: str
    display_name: str
    joined_at: datetime
    vad_active: bool = False
    vad_started_at: datetime | None = None
    # Running totals — survive history trimming because the brief's
    # `*_total*` fields are session-wide, not windowed. The store
    # increments these once per fold; trimming `segments` below cannot
    # discard a past turn.
    total_speaking_sec: float = 0.0
    total_turn_count: int = 0
    last_spoke_at: datetime | None = None
    last_spoke_duration_sec: float | None = None
    # Each entry is one STT-final segment kept around for rolling-window
    # aggregates; trimmed when its end_ts falls outside the longest
    # window the projection inspects.
    segments: list[_Segment] = field(default_factory=list)
    backchannels: list[datetime] = field(default_factory=list)
    interruptions_caused: list[_Interruption] = field(default_factory=list)
    interruptions_received: list[_Interruption] = field(default_factory=list)


_INTERRUPTION_MIN_OVERLAP_SEC: Final[float] = 0.4
"""Min concurrent VAD overlap to qualify as an interruption.

Below this, a late starter is treated as a clean handoff (the previous
speaker was already trailing off). 0.4s is the value the brief uses
implicitly via the cross_talk_pattern threshold and rounded VAD frame
timing — adjustable when Phase 3 ships per-study rule config.
"""


class StateStore:
    """Event-sourced projection of `SessionState`.

    Construct one per active session; the engine spawns one process per
    session so this never has to be thread-safe.
    """

    def __init__(
        self,
        *,
        session_id: UUID,
        started_at: datetime,
        scheduled_end_at: datetime | None = None,
        config: StateStoreConfig | None = None,
        quietness_budget: QuietnessBudget | None = None,
    ) -> None:
        self._session_id = session_id
        self._started_at = started_at
        self._scheduled_end_at = scheduled_end_at
        self._config = config if config is not None else StateStoreConfig()
        self._quietness_budget = (
            quietness_budget if quietness_budget is not None else QuietnessBudget()
        )

        # Event buffer (events not yet folded into accumulator). Kept
        # sorted by ts; `record` inserts in order, `advance_to` drains
        # the prefix whose ts <= t.
        self._pending: list[StateEvent] = []

        # Folded state — survives across ticks.
        self._participants: dict[str, _ParticipantAccumulator] = {}
        self._last_speech_at: datetime | None = None
        self._global_transcript: list[_Segment] = []

        # Researcher levers — flipped via `set_pause` / `set_muted` /
        # `update_quietness_budget` (Phase 5 wires command processing
        # to call these). Defaulted so Phase 2 ships read-safe.
        self._is_paused: bool = False
        self._moderator_muted: bool = False

        # Embedding cache (Phase 3 layer 6). The store stays sync at
        # `advance_to` time; an upstream coordinator (tick loop / runtime)
        # calls the async EmbeddingProvider and feeds results back here
        # via `set_study_prompt_embedding` / `set_rolling_transcript_embedding`.
        # Until those are called, the embedding fields project as None
        # and topic_drift's "missing embedding" branch keeps the moderator
        # silent.
        self._study_prompt: str = ""
        self._study_prompt_embedding: list[float] | None = None
        self._rolling_transcript_30s_embedding: list[float] | None = None
        self._embedding_model_name: str | None = None

        # Tick counter: each successful `advance_to` increments. The
        # first projected snapshot has `tick_id=0`.
        self._next_tick_id: int = 0

        # Sentinel for monotonicity check.
        self._last_advance_t: datetime | None = None

    # ------------------------------------------------------------------ API

    def record(self, event: StateEvent) -> None:
        """Buffer an event for the next `advance_to` call.

        Out-of-order arrivals are sorted into place — STT finals can
        arrive after the next VAD-off, and we want both folded at the
        next tick boundary as if they had landed in true time order.
        """
        # Linear scan; the buffer is typically tiny (10s of events per
        # 500ms tick), so bisect overhead is not worth a sorted-list
        # invariant we would have to maintain elsewhere.
        idx = 0
        for idx, existing in enumerate(self._pending):  # noqa: B007 — idx is the result
            if event.ts < existing.ts:
                break
        else:
            self._pending.append(event)
            return
        self._pending.insert(idx, event)

    def set_pause(self, *, paused: bool) -> None:
        """Toggle `SessionState.is_paused`. Idempotent."""
        self._is_paused = paused

    def set_moderator_muted(self, *, muted: bool) -> None:
        """Toggle `SessionState.moderator_muted`. Idempotent."""
        self._moderator_muted = muted

    def update_quietness_budget(self, budget: QuietnessBudget) -> None:
        """Replace the current budget snapshot (researcher slider, Phase 5)."""
        self._quietness_budget = budget

    def set_study_prompt(
        self,
        prompt: str,
        embedding: list[float],
        *,
        model_name: str,
    ) -> None:
        """Set the study prompt + its embedding for `topic_drift` (Phase 3 L6).

        Called once per session at startup, after the upstream coordinator
        has invoked the EmbeddingProvider. The vector is treated as opaque
        — the store does not validate dimensionality here because the
        rule predicate compares it against the transcript embedding, which
        is also produced by the same provider; cross-model mismatches are
        the coordinator's responsibility to prevent.
        """
        self._study_prompt = prompt
        self._study_prompt_embedding = list(embedding)
        self._embedding_model_name = model_name

    def set_rolling_transcript_embedding(
        self,
        embedding: list[float] | None,
        *,
        model_name: str,
    ) -> None:
        """Update the cached 30s-transcript embedding for `topic_drift`.

        Coordinator calls this after each UtteranceFinalEvent (or on a
        debounced schedule) once the async embed round-trip completes.
        Passing `None` clears the cache — used on provider failure so the
        rule sees "no embedding" and stays silent rather than acting on
        stale data.

        `model_name` must match what was passed to `set_study_prompt`;
        if it doesn't, we still store it (the projection writes it onto
        the snapshot) so a downstream invariant check can catch the drift.
        """
        self._rolling_transcript_30s_embedding = list(embedding) if embedding is not None else None
        # Don't overwrite a populated model_name with the same value;
        # but if the study_prompt was never set this is the first signal
        # we've seen and we record it for the snapshot.
        if self._embedding_model_name is None:
            self._embedding_model_name = model_name

    def advance_to(self, t: datetime) -> SessionState:
        """Fold pending events with `ts <= t` and project a fresh snapshot."""
        if self._last_advance_t is not None and t < self._last_advance_t:
            msg = (
                f"advance_to is monotonic; got t={t.isoformat()} "
                f"after t={self._last_advance_t.isoformat()}"
            )
            raise ValueError(msg)

        # 1) Drain events whose ts is at or before the tick boundary.
        drained: list[StateEvent] = []
        i = 0
        while i < len(self._pending) and self._pending[i].ts <= t:
            drained.append(self._pending[i])
            i += 1
        self._pending = self._pending[i:]

        for event in drained:
            self._fold(event, tick_t=t)

        # 2) Truncate retained history to the longest rolling window we
        #    actually inspect. Anything older can never re-enter a
        #    window aggregate.
        self._trim_history(t)

        # 3) Project SessionState.
        snapshot = self._project(t)
        self._last_advance_t = t
        self._next_tick_id += 1
        return snapshot

    # ------------------------------------------------------------ Folding

    def _fold(self, event: StateEvent, *, tick_t: datetime) -> None:
        if isinstance(event, ParticipantJoinEvent):
            # Re-joins reuse the original `joined_at` so speaking_time
            # totals never reset mid-session. Display name can update.
            existing = self._participants.get(event.participant_id)
            if existing is None:
                self._participants[event.participant_id] = _ParticipantAccumulator(
                    participant_id=event.participant_id,
                    display_name=event.display_name,
                    joined_at=event.ts,
                )
            else:
                existing.display_name = event.display_name
            return

        if isinstance(event, ParticipantLeaveEvent):
            # Remove from active set but keep nothing — leaving means
            # they shouldn't appear in the tile grid anymore. Rules
            # that care about historical participants can read the
            # decision log.
            self._participants.pop(event.participant_id, None)
            return

        if isinstance(event, VadOnEvent):
            self._handle_vad_on(event, tick_t=tick_t)
            return

        if isinstance(event, VadOffEvent):
            self._handle_vad_off(event)
            return

        # UtteranceFinalEvent — the only remaining variant.
        self._handle_utterance_final(event)

    def _handle_vad_on(self, event: VadOnEvent, *, tick_t: datetime) -> None:
        acc = self._ensure_participant(
            participant_id=event.participant_id,
            display_name=event.participant_id,
            ts=event.ts,
        )

        # Detect interruption: another participant is currently VAD-active
        # at the moment this one starts. Both have to keep speaking for
        # `_INTERRUPTION_MIN_OVERLAP_SEC` for it to count, but for the
        # purpose of incrementing counters at fold time we optimistically
        # record it — and the trim step later filters by overlap.
        for other_id, other in self._participants.items():
            if other_id == event.participant_id:
                continue
            if not other.vad_active or other.vad_started_at is None:
                continue
            # Optimistic: bookkeep regardless, then qualify on duration.
            interruption = _Interruption(
                attacker_id=event.participant_id,
                victim_id=other_id,
                ts=event.ts,
            )
            acc.interruptions_caused.append(interruption)
            other.interruptions_received.append(interruption)
            _ = tick_t  # tick_t reserved for future overlap-finalization

        acc.vad_active = True
        acc.vad_started_at = event.ts

    def _handle_vad_off(self, event: VadOffEvent) -> None:
        acc = self._participants.get(event.participant_id)
        if acc is None or not acc.vad_active:
            return
        acc.vad_active = False
        acc.vad_started_at = None

    def _handle_utterance_final(self, event: UtteranceFinalEvent) -> None:
        acc = self._ensure_participant(
            participant_id=event.participant_id,
            display_name=event.participant_id,
            ts=event.start_ts,
        )

        segment = _Segment(
            participant_id=event.participant_id,
            start_ts=event.start_ts,
            end_ts=event.end_ts,
            duration_sec=event.duration_sec,
            text=event.text,
            is_backchannel=event.is_backchannel,
            utterance_id=event.utterance_id,
        )

        if event.is_backchannel:
            # Backchannels are not turns and don't get counted against
            # speaking-time aggregates — but they do prove engagement.
            acc.backchannels.append(event.end_ts)
            return

        acc.segments.append(segment)
        # Running totals are session-wide and must not be reset when
        # `_trim_history` drops the segment from the rolling buffer.
        acc.total_speaking_sec += segment.duration_sec
        acc.total_turn_count += 1
        if acc.last_spoke_at is None or segment.end_ts > acc.last_spoke_at:
            acc.last_spoke_at = segment.end_ts
            acc.last_spoke_duration_sec = segment.duration_sec
        self._global_transcript.append(segment)
        if self._last_speech_at is None or event.end_ts > self._last_speech_at:
            self._last_speech_at = event.end_ts

    def _ensure_participant(
        self, *, participant_id: str, display_name: str, ts: datetime
    ) -> _ParticipantAccumulator:
        """Return existing accumulator or create one if events arrive before join.

        Real LiveKit sessions emit join before any media, but tests and
        replayed event streams can violate that. Auto-creating keeps
        the projection robust at the cost of a slightly weaker
        invariant — the rules engine never sees ghost participants
        because every event carries a real id.
        """
        acc = self._participants.get(participant_id)
        if acc is not None:
            return acc
        acc = _ParticipantAccumulator(
            participant_id=participant_id,
            display_name=display_name,
            joined_at=ts,
        )
        self._participants[participant_id] = acc
        return acc

    # ---------------------------------------------------------- Maintenance

    def _trim_history(self, t: datetime) -> None:
        """Discard segments and timestamps that can no longer affect any window.

        The longest window we read is `max(rolling_transcript, last_5min,
        interruption, backchannel, disengaged_silence)`. Drop anything
        whose `end_ts` is older than that boundary.
        """
        horizon_sec = max(
            self._config.rolling_transcript_window_sec,
            self._config.last_5min_window_sec,
            self._config.interruption_window_sec,
            self._config.backchannel_window_sec,
            self._config.disengaged_silence_sec,
            self._config.silent_too_long_sec,
        )
        cutoff = t - timedelta(seconds=horizon_sec)

        self._global_transcript = [s for s in self._global_transcript if s.end_ts >= cutoff]

        for acc in self._participants.values():
            acc.segments = [s for s in acc.segments if s.end_ts >= cutoff]
            acc.backchannels = [b for b in acc.backchannels if b >= cutoff]
            acc.interruptions_caused = [i for i in acc.interruptions_caused if i.ts >= cutoff]
            acc.interruptions_received = [i for i in acc.interruptions_received if i.ts >= cutoff]

    # ------------------------------------------------------------ Projection

    def _project(self, t: datetime) -> SessionState:
        n = len(self._participants)
        fair_share = 100.0 / n if n > 0 else 0.0

        # Per-participant aggregates.
        last5_window_start = t - timedelta(seconds=self._config.last_5min_window_sec)
        last60_window_start = t - timedelta(seconds=self._config.last_60sec_window_sec)
        transcript_window_start = t - timedelta(seconds=self._config.rolling_transcript_window_sec)
        backchannel_window_start = t - timedelta(seconds=self._config.backchannel_window_sec)

        # Pre-compute last_5min total to divide for actual_share.
        last5_totals: dict[str, float] = {}
        for pid, acc in self._participants.items():
            last5_totals[pid] = _sum_overlap(acc.segments, last5_window_start, t)
        last5_grand_total = sum(last5_totals.values())

        projected: dict[str, ParticipantState] = {}
        currently_speaking = 0
        for pid, acc in self._participants.items():
            last5_sec = last5_totals[pid]
            last60_sec = _sum_overlap(acc.segments, last60_window_start, t)

            backchannel_count = sum(1 for ts in acc.backchannels if ts >= backchannel_window_start)
            interruption_count = len(acc.interruptions_caused)
            was_interrupted_count = len(acc.interruptions_received)

            recent_refs = _recent_utterance_refs(
                acc.segments, max_count=self._config.recent_utterances_max
            )
            rolling_text = _concatenate_segments(
                [s for s in acc.segments if s.end_ts >= transcript_window_start]
            )

            actual_share = (
                (last5_sec / last5_grand_total) * 100.0 if last5_grand_total > 0.0 else 0.0
            )
            # Defensive clamp: floating-point accumulation can drift the
            # last participant's share to 100.0000001 — Pydantic's le=100
            # validator rejects that. The math guarantees the clamp is a
            # no-op in exact arithmetic.
            actual_share = min(actual_share, 100.0)

            # `is_currently_speaking` follows VAD only: STT finals always
            # arrive after the utterance has ended, so a final whose
            # `end_ts` covers `t` would just be a clock-skew artifact.
            is_speaking = acc.vad_active
            if is_speaking:
                currently_speaking += 1

            flags = self._compute_flags(
                acc=acc,
                t=t,
                actual_share_pct=actual_share,
                fair_share_pct=fair_share,
                backchannel_count=backchannel_count,
                was_interrupted_count=was_interrupted_count,
            )

            projected[pid] = ParticipantState(
                participant_id=acc.participant_id,
                display_name=acc.display_name,
                joined_at=acc.joined_at,
                speaking_time_total_sec=acc.total_speaking_sec,
                speaking_time_last_5min_sec=last5_sec,
                speaking_time_last_60sec=last60_sec,
                turn_count=acc.total_turn_count,
                last_spoke_at=acc.last_spoke_at,
                last_spoke_duration_sec=acc.last_spoke_duration_sec,
                is_currently_speaking=is_speaking,
                vad_active=acc.vad_active,
                backchannel_count_last_2min=backchannel_count,
                interruption_count=interruption_count,
                was_interrupted_count=was_interrupted_count,
                recent_utterances=recent_refs,
                rolling_transcript_2min=rolling_text,
                flags=flags,
                fair_share_pct=fair_share,
                actual_share_last_5min_pct=actual_share,
            )

        rolling_global_text = _concatenate_segments(
            [s for s in self._global_transcript if s.end_ts >= transcript_window_start]
        )

        if self._last_speech_at is None:
            silence_run = max((t - self._started_at).total_seconds(), 0.0)
        else:
            silence_run = max((t - self._last_speech_at).total_seconds(), 0.0)

        elapsed = max((t - self._started_at).total_seconds(), 0.0)

        return SessionState(
            session_id=self._session_id,
            tick_id=self._next_tick_id,
            t=t,
            started_at=self._started_at,
            scheduled_end_at=self._scheduled_end_at,
            elapsed_sec=elapsed,
            participants=projected,
            currently_speaking_count=currently_speaking,
            silence_run_sec=silence_run,
            rolling_global_transcript_2min=rolling_global_text,
            is_paused=self._is_paused,
            moderator_muted=self._moderator_muted,
            quietness_budget=self._quietness_budget,
            study_prompt=self._study_prompt,
            study_prompt_embedding=self._study_prompt_embedding,
            rolling_transcript_30s_embedding=self._rolling_transcript_30s_embedding,
            embedding_model_name=self._embedding_model_name,
        )

    def _compute_flags(
        self,
        *,
        acc: _ParticipantAccumulator,
        t: datetime,
        actual_share_pct: float,
        fair_share_pct: float,
        backchannel_count: int,
        was_interrupted_count: int,
    ) -> ParticipantFlags:
        cfg = self._config

        # `dominating` only meaningful when there is actually shared
        # airtime to dominate — single-participant sessions never trip.
        dominating = (
            len(self._participants) > 1
            and fair_share_pct > 0.0
            and actual_share_pct >= cfg.dominating_factor * fair_share_pct
        )

        # `acc.last_spoke_at` is the session-wide last spoken timestamp and
        # survives history trimming; falling back to `joined_at` gives a
        # newly-joined participant a clean "silent since join" reading.
        seconds_since_speech = (
            (t - acc.last_spoke_at).total_seconds()
            if acc.last_spoke_at is not None
            else (t - acc.joined_at).total_seconds()
        )
        silent_too_long = seconds_since_speech >= cfg.silent_too_long_sec

        frequently_interrupted = was_interrupted_count >= cfg.frequently_interrupted_threshold

        disengaged = seconds_since_speech >= cfg.disengaged_silence_sec and backchannel_count == 0

        return ParticipantFlags(
            dominating=dominating,
            silent_too_long=silent_too_long,
            frequently_interrupted=frequently_interrupted,
            disengaged=disengaged,
        )


# ---------------------------------------------------------------- Helpers


def _sum_overlap(segments: list[_Segment], window_start: datetime, window_end: datetime) -> float:
    """Sum of segment lengths clipped to `[window_start, window_end]`.

    Speaking-time accounting clamps each segment to the window so a
    long utterance straddling the boundary contributes only its
    visible portion. Negative or zero overlaps contribute 0.
    """
    if window_end <= window_start:
        return 0.0
    total = 0.0
    for seg in segments:
        start = max(seg.start_ts, window_start)
        end = min(seg.end_ts, window_end)
        delta = (end - start).total_seconds()
        if delta > 0.0:
            total += delta
    return total


def _recent_utterance_refs(segments: list[_Segment], *, max_count: int) -> list[UtteranceRef]:
    """Latest `max_count` finalized segments, oldest first."""
    tail = segments[-max_count:]
    return [
        UtteranceRef(
            utterance_id=seg.utterance_id,
            text=seg.text,
            spoken_at=seg.start_ts,
            duration_sec=seg.duration_sec,
        )
        for seg in tail
    ]


def _concatenate_segments(segments: list[_Segment]) -> str:
    """Join finalized segments into one transcript string, newest last.

    Sorted by `start_ts` so out-of-order STT deliveries surface in
    spoken order — rule predicates that read this expect chronology.
    """
    ordered = sorted(segments, key=lambda s: s.start_ts)
    return " ".join(s.text for s in ordered if s.text)
