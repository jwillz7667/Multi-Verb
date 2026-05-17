"""Integration tests for `SessionRuntime` — real Postgres, no LiveKit.

Uses the same testcontainers fixture as the persistence tests. Validates
that the runtime's transitions write the right rows for the agent's
canonical lifecycle: dispatch arrives → mark live → participant joins
→ participant leaves → room ends → mark ended.

The LiveKit-facing glue (`agent.worker`) is not exercised here; that's
covered by the Playwright E2E in L6 which spins up real fake-participant
audio.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.agent import ParticipantSnapshot, SessionRuntime
from verbio_engine.agent.runtime import UnknownSessionError
from verbio_engine.persistence import (
    Participant,
    Session,
    StateSnapshot,
    Utterance,
    UtteranceInsert,
    create_session_factory,
)
from verbio_engine.realtime import (
    StateSnapshotEventEnvelope,
    TranscriptEvent,
    UtteranceEventEnvelope,
)
from verbio_engine.recordings import (
    EgressHandle,
    composite_audio_key,
    participant_audio_key,
)
from verbio_engine.tick_loop import FakeClock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

RuntimeFactory = Callable[[str], SessionRuntime]


class RecordingPublisher:
    """In-memory EventPublisher double used by runtime integration tests."""

    def __init__(self) -> None:
        self.events: list[TranscriptEvent] = []
        self.closed = False

    async def publish(self, event: TranscriptEvent) -> int:
        self.events.append(event)
        return 1

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
async def runtime_factory(engine: AsyncEngine) -> RuntimeFactory:
    """Build a SessionRuntime bound to the migrated test DB.

    A factory rather than a fixture-of-the-runtime so tests can choose
    their own room_name (and we don't accidentally share state).
    """
    factory = create_session_factory(engine)

    def _make(room_name: str) -> SessionRuntime:
        return SessionRuntime(session_factory=factory, room_name=room_name)

    return _make


@pytest.mark.integration
async def test_unknown_room_raises_before_session_id_resolves(
    engine: AsyncEngine,
    runtime_factory: RuntimeFactory,
) -> None:
    runtime = runtime_factory(f"never-created-{uuid.uuid4()}")

    with pytest.raises(UnknownSessionError):
        await runtime.on_room_connected()


@pytest.mark.integration
async def test_full_lifecycle_writes_expected_rows(
    engine: AsyncEngine,
    runtime_factory: RuntimeFactory,
) -> None:
    # Web has already created the session row.
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    runtime = runtime_factory(room_name)

    # Agent dispatched → room connected.
    seeded = await runtime.on_room_connected()
    assert seeded.status == "live"
    assert seeded.actual_start is not None

    # Two participants join, one re-joins after a disconnect.
    await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-maya",
            display_name="Maya",
            role="participant",
        )
    )
    await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-devon",
            display_name="Devon",
            role="researcher",
        )
    )
    await runtime.on_participant_left("id-maya")
    rejoined = await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-maya",
            display_name="Maya",
            role="participant",
        )
    )

    # Room teardown.
    await runtime.on_room_disconnected()

    # Verify the resulting DB state.
    async with factory() as db:
        sess = (
            await db.execute(select(Session).where(Session.livekit_room_name == room_name))
        ).scalar_one()
        parts = (
            (await db.execute(select(Participant).where(Participant.session_id == sess.id)))
            .scalars()
            .all()
        )

    assert sess.status == "ended"
    assert sess.actual_end is not None

    by_identity = {p.livekit_identity: p for p in parts}
    assert set(by_identity) == {"id-maya", "id-devon"}
    assert by_identity["id-maya"].id == rejoined.id  # re-join reused the row
    assert by_identity["id-maya"].left_at is None  # re-join cleared left_at
    assert by_identity["id-maya"].role == "participant"
    assert by_identity["id-devon"].role == "researcher"


@pytest.mark.integration
async def test_on_room_disconnected_is_noop_before_connect(
    runtime_factory: RuntimeFactory,
) -> None:
    runtime = runtime_factory(f"room-{uuid.uuid4()}")

    # No exception: a worker that crashed before `on_room_connected`
    # must be safe to shut down.
    await runtime.on_room_disconnected()


@pytest.mark.integration
async def test_session_id_property_raises_before_connect(
    runtime_factory: RuntimeFactory,
) -> None:
    runtime = runtime_factory(f"room-{uuid.uuid4()}")

    with pytest.raises(RuntimeError, match="before on_room_connected"):
        _ = runtime.session_id


@pytest.mark.integration
async def test_on_participant_joined_populates_identity_cache(
    engine: AsyncEngine,
    runtime_factory: RuntimeFactory,
) -> None:
    """Track callbacks resolve participant_id via this cache.

    Without it the transcriber would have to round-trip to Postgres on
    every track_subscribed event just to discover its own foreign key.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    runtime = runtime_factory(room_name)
    await runtime.on_room_connected()

    assert runtime.participant_id_for("id-alice") is None

    row = await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-alice",
            display_name="Alice",
            role="participant",
        )
    )

    assert runtime.participant_id_for("id-alice") == row.id
    # await_participant_id returns immediately when already cached.
    awaited = await runtime.await_participant_id("id-alice", timeout=0.001)
    assert awaited == row.id


@pytest.mark.integration
async def test_persist_utterance_writes_row(
    engine: AsyncEngine,
    runtime_factory: RuntimeFactory,
) -> None:
    """End-to-end: STT pipeline → runtime.persist_utterance → DB row."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    runtime = runtime_factory(room_name)
    await runtime.on_room_connected()
    participant = await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-utt",
            display_name="Utt",
            role="participant",
        )
    )

    start = datetime.now(UTC)
    end = start
    await runtime.persist_utterance(
        UtteranceInsert(
            session_id=runtime.session_id,
            participant_id=participant.id,
            start_ts=start,
            end_ts=end,
            text="testing one two",
            confidence=0.88,
            is_final=True,
        )
    )

    async with factory() as db:
        rows = (
            (await db.execute(select(Utterance).where(Utterance.session_id == runtime.session_id)))
            .scalars()
            .all()
        )

    assert [r.text for r in rows] == ["testing one two"]
    assert rows[0].participant_id == participant.id
    assert rows[0].is_final is True
    assert rows[0].confidence == pytest.approx(0.88)


@pytest.mark.integration
async def test_persist_utterance_publishes_event_with_speaker_metadata(
    engine: AsyncEngine,
) -> None:
    """Persist-then-publish: the SSE envelope carries identity + display name.

    The dashboard renders without re-joining to `participants`, so the
    runtime is responsible for enriching each event with the speaker's
    LiveKit identity and display name from the in-memory snapshot cache.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    await runtime.on_room_connected()
    participant = await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-pub",
            display_name="Publisher Test",
            role="participant",
        )
    )

    now = datetime.now(UTC)
    await runtime.persist_utterance(
        UtteranceInsert(
            session_id=runtime.session_id,
            participant_id=participant.id,
            start_ts=now,
            end_ts=now,
            text="event payload",
            confidence=0.77,
            is_final=True,
        )
    )

    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.type == "utterance"
    assert event.session_id == runtime.session_id
    assert event.payload.participant_identity == "id-pub"
    assert event.payload.participant_display_name == "Publisher Test"
    assert event.payload.text == "event payload"
    assert event.payload.is_final is True
    # The SSE event id is the utterance UUID (resolvable for backfill).
    assert event.id == str(event.payload.utterance_id)


@pytest.mark.integration
async def test_persist_utterance_skips_publish_without_cached_snapshot(
    engine: AsyncEngine,
) -> None:
    """Worker restart mid-session loses the snapshot cache; publish silently skips.

    The audit-trail row is still written. The SSE backfill on the next
    reconnect picks up the utterance, so the dashboard recovers without
    a publish in this rare edge case.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        sess = Session(livekit_room_name=room_name, status="scheduled")
        db.add(sess)
    # Pre-seed a participant row directly so the FK is satisfied but the
    # in-memory snapshot cache stays empty (simulates worker restart).
    async with factory() as db, db.begin():
        sess_row = (
            await db.execute(select(Session).where(Session.livekit_room_name == room_name))
        ).scalar_one()
        orphan = Participant(
            session_id=sess_row.id,
            display_name="Ghost",
            role="participant",
            livekit_identity="id-ghost",
        )
        db.add(orphan)

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    await runtime.on_room_connected()

    async with factory() as db:
        orphan_row = (
            await db.execute(
                select(Participant).where(Participant.livekit_identity == "id-ghost"),
            )
        ).scalar_one()

    now = datetime.now(UTC)
    await runtime.persist_utterance(
        UtteranceInsert(
            session_id=runtime.session_id,
            participant_id=orphan_row.id,
            start_ts=now,
            end_ts=now,
            text="orphan utterance",
            confidence=None,
            is_final=True,
        )
    )

    assert recorder.events == []
    # Row still written.
    async with factory() as db:
        utterances = (
            (await db.execute(select(Utterance).where(Utterance.session_id == runtime.session_id)))
            .scalars()
            .all()
        )
    assert [u.text for u in utterances] == ["orphan utterance"]


# ---------------------------------------------------------------------------
# Tick loop wiring — state events flow from runtime callbacks into the
# store, the tick projects, and snapshots land in Postgres + on the wire.
# ---------------------------------------------------------------------------


async def _seed_session(factory: object, room_name: str) -> None:
    """Create the `sessions` row the agent expects to find on join."""
    async with factory() as db, db.begin():  # type: ignore[operator]
        db.add(Session(livekit_room_name=room_name, status="scheduled"))


@pytest.mark.integration
async def test_participant_join_flows_into_state_store_and_snapshot(
    engine: AsyncEngine,
) -> None:
    """Brief §10.1: a participant joining must appear in the next tick's
    `SessionState.participants`. This proves the runtime's `_record_state_event`
    hook reaches the live store the tick loop drives."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    session_row = await runtime.on_room_connected()

    started = session_row.actual_start
    assert started is not None
    clock = FakeClock(start=started)
    runtime.start_tick_loop(
        started_at=started,
        clock=clock,
        interval_sec=0.5,
    )

    try:
        await runtime.on_participant_joined(
            ParticipantSnapshot(
                identity="id-tick-a",
                display_name="Tick Alice",
                role="participant",
            )
        )
        # Drive one tick at exactly `started` — the join event timestamp
        # is wall-clock `now()`, which is >= `started`, so under a real
        # clock the next advance picks it up. The FakeClock here only
        # controls when the tick fires; the join event's ts is real.
        # We need to advance the virtual clock past the join's wall time
        # so the event drains into the store on this tick.
        clock.advance(60.0)
        # Yield to let the tick task complete its iteration.
        await asyncio.sleep(0.05)
    finally:
        await runtime.stop_tick_loop()

    snapshot_events = [e for e in recorder.events if isinstance(e, StateSnapshotEventEnvelope)]
    assert snapshot_events, "tick loop produced no snapshots"

    # The first tick may fire before the participant_join event has
    # propagated through the async upsert + `_record_state_event`. Find
    # the first snapshot that actually contains the joined participant.
    seen = next(
        (
            ev
            for ev in snapshot_events
            if any(p.display_name == "Tick Alice" for p in ev.payload.state.participants.values())
        ),
        None,
    )
    assert seen is not None, "no snapshot contained the joined participant"

    # The corresponding `state_snapshots` row is on disk before the
    # envelope reached the publisher (persist-before-publish).
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(StateSnapshot).where(StateSnapshot.session_id == runtime.session_id),
                )
            )
            .scalars()
            .all()
        )
    assert any(row.id == seen.payload.snapshot_id for row in rows)


@pytest.mark.integration
async def test_moderator_join_does_not_appear_in_state_snapshot(
    engine: AsyncEngine,
) -> None:
    """The moderator is the agent itself — not a session member from the
    rules' POV. Joining as `role="moderator"` must not show up in the
    `participants` projection or the rule engine will count us against
    quietness budgets / fair-share thresholds."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None
    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        await runtime.on_participant_joined(
            ParticipantSnapshot(
                identity="id-mod",
                display_name="Verbio",
                role="moderator",
            )
        )
        clock.advance(60.0)
        await asyncio.sleep(0.05)
    finally:
        await runtime.stop_tick_loop()

    snapshot_events = [e for e in recorder.events if isinstance(e, StateSnapshotEventEnvelope)]
    assert snapshot_events, "tick loop produced no snapshots"
    for ev in snapshot_events:
        for p in ev.payload.state.participants.values():
            assert (
                p.display_name != "Verbio"
            ), "moderator must not appear in SessionState.participants"


@pytest.mark.integration
async def test_participant_leave_removes_from_state_snapshot(
    engine: AsyncEngine,
) -> None:
    """A `ParticipantLeaveEvent` must drop the participant from the
    next tick's projection — the dashboard tile vanishes immediately,
    matching the LiveKit room view."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None
    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        await runtime.on_participant_joined(
            ParticipantSnapshot(
                identity="id-leaver",
                display_name="Leaver",
                role="participant",
            )
        )
        clock.advance(60.0)
        await asyncio.sleep(0.05)

        await runtime.on_participant_left("id-leaver")
        clock.advance(60.0)
        await asyncio.sleep(0.05)
    finally:
        await runtime.stop_tick_loop()

    snapshot_events = [e for e in recorder.events if isinstance(e, StateSnapshotEventEnvelope)]
    # Find the last snapshot; the leaver should be absent by then.
    final = snapshot_events[-1]
    names = {p.display_name for p in final.payload.state.participants.values()}
    assert "Leaver" not in names


@pytest.mark.integration
async def test_final_utterance_feeds_state_store_speaking_time(
    engine: AsyncEngine,
) -> None:
    """An `UtteranceFinalEvent` must accumulate into the participant's
    speaking-time totals — that's the data the dashboard tile reads to
    show share-of-airtime. Interim STT results do NOT feed the store
    (the listener filters by `is_final=True`)."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None
    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        joined = await runtime.on_participant_joined(
            ParticipantSnapshot(
                identity="id-speaker",
                display_name="Speaker",
                role="participant",
            )
        )

        now = datetime.now(UTC)
        # Interim — must NOT increment speaking time.
        await runtime.persist_utterance(
            UtteranceInsert(
                session_id=runtime.session_id,
                participant_id=joined.id,
                start_ts=now,
                end_ts=now,
                text="interim",
                confidence=0.5,
                is_final=False,
            )
        )
        # Final — duration ~ 2 seconds.
        final_start = now
        final_end = now + timedelta(seconds=2)
        await runtime.persist_utterance(
            UtteranceInsert(
                session_id=runtime.session_id,
                participant_id=joined.id,
                start_ts=final_start,
                end_ts=final_end,
                text="final hello",
                confidence=0.9,
                is_final=True,
            )
        )

        clock.advance(60.0)
        await asyncio.sleep(0.05)
    finally:
        await runtime.stop_tick_loop()

    snapshot_events = [e for e in recorder.events if isinstance(e, StateSnapshotEventEnvelope)]
    assert snapshot_events, "tick loop produced no snapshots"

    # The participant in the final snapshot should have ~2s of speaking
    # time (final segment) and turn_count == 1 (interim filtered out).
    final = snapshot_events[-1]
    speakers = [p for p in final.payload.state.participants.values() if p.display_name == "Speaker"]
    assert len(speakers) == 1
    speaker = speakers[0]
    assert speaker.turn_count == 1
    assert speaker.speaking_time_total_sec == pytest.approx(2.0, rel=0.01)

    # The corresponding utterance envelope was also published (interim
    # + final = 2 utterance events on the wire).
    utterance_events = [e for e in recorder.events if isinstance(e, UtteranceEventEnvelope)]
    assert len(utterance_events) == 2
    assert {e.payload.is_final for e in utterance_events} == {True, False}


# ---------------------------------------------------------------------------
# Embedding pipeline wiring (Phase 3 L7).
# ---------------------------------------------------------------------------


class _RecordingProvider:
    """EmbeddingProvider double that records prompt + transcript calls."""

    def __init__(self) -> None:
        self.dim = 4
        self.model_name = "rec-embed-v1"
        self.calls: list[str] = []

    async def embed_one(self, text: str) -> list[float]:
        self.calls.append(text)
        # Use a position-dependent vector so different inputs produce
        # different outputs — lets us assert which call landed in cache.
        seed = float(len(self.calls))
        return [seed, 0.0, 0.0, 0.0]

    async def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed_one(t) for t in texts]


@pytest.mark.integration
async def test_persist_utterance_triggers_embed_pipeline(
    engine: AsyncEngine,
) -> None:
    """Phase 3 L7 contract:

    1. `seed_study_prompt` (before tick loop) buffers, then drains as the
       loop starts.
    2. Each `is_final=True` `persist_utterance` request triggers the
       coordinator's single-flight re-embed of the rolling transcript.
    3. The resulting vectors thread through to `SessionState` snapshots.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    provider = _RecordingProvider()
    recorder = RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=recorder,
        embedding_provider=provider,
    )

    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None
    clock = FakeClock(start=started)

    # Seed prompt BEFORE start_tick_loop to exercise the buffer-drain path.
    runtime.seed_study_prompt("how do you use the music app daily?")
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        joined = await runtime.on_participant_joined(
            ParticipantSnapshot(
                identity="id-speaker",
                display_name="Speaker",
                role="participant",
            )
        )

        # Use the fake-clock anchor for the event timestamps so the
        # store's `advance_to(t)` sees the event as already-occurred and
        # folds it into `_global_transcript` (the coordinator then reads
        # that for the rolling-transcript embed). Mixing wall-clock and
        # fake-clock would leave the event stranded in `_pending`.
        utt_start = started + timedelta(seconds=0.1)
        utt_end = utt_start + timedelta(seconds=1.0)
        await runtime.persist_utterance(
            UtteranceInsert(
                session_id=runtime.session_id,
                participant_id=joined.id,
                start_ts=utt_start,
                end_ts=utt_end,
                text="i mostly use it on the train",
                confidence=0.95,
                is_final=True,
            )
        )

        # Tick past the utterance so the store folds it; sleep gives the
        # listener + coordinator a chance to publish + re-embed.
        clock.advance(2.0)
        await asyncio.sleep(0.05)
    finally:
        await runtime.stop_tick_loop()

    # Provider was called for both the prompt and the rolling transcript.
    assert provider.calls[0] == "how do you use the music app daily?"
    rolling_calls = [c for c in provider.calls if "train" in c]
    assert rolling_calls, "rolling transcript was never embedded"

    # Final snapshot carries both embeddings + the model name.
    snapshot_events = [e for e in recorder.events if isinstance(e, StateSnapshotEventEnvelope)]
    assert snapshot_events, "tick loop produced no snapshots"
    final_state = snapshot_events[-1].payload.state
    assert final_state.study_prompt == "how do you use the music app daily?"
    assert final_state.study_prompt_embedding is not None
    assert final_state.rolling_transcript_30s_embedding is not None
    assert final_state.embedding_model_name == "rec-embed-v1"


# ---------------------------------------------------------------------------
# Phase 6 L2: egress dispatcher wiring
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordingDispatcher:
    """In-memory `EgressDispatcher` double used by the wiring tests."""

    composite_calls: list[tuple[uuid.UUID, str]] = field(default_factory=list)
    participant_calls: list[tuple[uuid.UUID, str, str]] = field(default_factory=list)
    closed: bool = False

    async def start_room_composite(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
    ) -> EgressHandle | None:
        self.composite_calls.append((session_id, room_name))
        return EgressHandle(
            egress_id=f"EG_C_{len(self.composite_calls):04d}",
            r2_key=composite_audio_key(session_id),
        )

    async def start_participant_audio(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
        identity: str,
    ) -> EgressHandle | None:
        self.participant_calls.append((session_id, room_name, identity))
        return EgressHandle(
            egress_id=f"EG_P_{len(self.participant_calls):04d}",
            r2_key=participant_audio_key(session_id, identity),
        )

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.integration
async def test_on_room_connected_starts_composite_egress(
    engine: AsyncEngine,
) -> None:
    """`on_room_connected` must fire composite egress after marking live."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    dispatcher = _RecordingDispatcher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        egress_dispatcher=dispatcher,
    )

    session_row = await runtime.on_room_connected()

    assert dispatcher.composite_calls == [(session_row.id, room_name)]
    assert dispatcher.participant_calls == []


@pytest.mark.integration
async def test_on_participant_joined_fires_egress_for_real_participants_only(
    engine: AsyncEngine,
) -> None:
    """Participants + researchers record; moderator never does.

    Moderator audio is captured in the room composite — a dedicated
    moderator track would be redundant and would clutter the per-track
    folder. The state event itself is also suppressed for the
    moderator (the rules POV treats them as not-a-participant), so the
    egress branch must match that gate.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    dispatcher = _RecordingDispatcher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        egress_dispatcher=dispatcher,
    )
    await runtime.on_room_connected()

    await runtime.on_participant_joined(
        ParticipantSnapshot(identity="id-p1", display_name="P1", role="participant"),
    )
    await runtime.on_participant_joined(
        ParticipantSnapshot(identity="id-r1", display_name="R1", role="researcher"),
    )
    await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-moderator",
            display_name="Mod",
            role="moderator",
        ),
    )

    identities = [identity for _, _, identity in dispatcher.participant_calls]
    assert identities == ["id-p1", "id-r1"]
    assert all(sid == runtime.session_id for sid, _, _ in dispatcher.participant_calls)


@pytest.mark.integration
async def test_runtime_without_dispatcher_skips_egress_calls(
    engine: AsyncEngine,
) -> None:
    """Default (no dispatcher) wiring keeps the lifecycle working without recording."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    runtime = SessionRuntime(session_factory=factory, room_name=room_name)

    # No egress dispatcher → no AttributeError, just a silent skip.
    await runtime.on_room_connected()
    await runtime.on_participant_joined(
        ParticipantSnapshot(identity="id-no-rec", display_name="X", role="participant"),
    )
