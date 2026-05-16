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

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.agent import ParticipantSnapshot, SessionRuntime
from verbio_engine.agent.runtime import UnknownSessionError
from verbio_engine.persistence import (
    Participant,
    Session,
    Utterance,
    UtteranceInsert,
    create_session_factory,
)
from verbio_engine.realtime import TranscriptEvent

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
