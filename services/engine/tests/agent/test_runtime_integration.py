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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

RuntimeFactory = Callable[[str], SessionRuntime]


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
