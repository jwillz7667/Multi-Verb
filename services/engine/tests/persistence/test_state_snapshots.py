"""Integration tests for the state-snapshot repository.

Boots a real Postgres container (testcontainers) and exercises the
full path: Alembic migrations → ORM model → repository insert →
read-back. Per the brief and project conventions, integration tests
must hit a real DB so a broken migration surfaces here, not in prod.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.persistence import (
    Session,
    StateSnapshot,
    StateSnapshotInsert,
    StateSnapshotRepo,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _state_dict(session_id: uuid.UUID, tick_id: int) -> dict[str, object]:
    """Minimal-but-realistic SessionState JSON dump for the JSONB column."""
    now = datetime.now(UTC).isoformat()
    return {
        "session_id": str(session_id),
        "tick_id": tick_id,
        "t": now,
        "started_at": now,
        "scheduled_end_at": None,
        "elapsed_sec": float(tick_id) * 0.5,
        "participants": {},
        "currently_speaking_count": 0,
        "silence_run_sec": 0.0,
        "rolling_global_transcript_2min": "",
        "is_paused": False,
        "moderator_muted": False,
        "quietness_budget": {
            "max_decisions_per_5min": 6,
            "min_seconds_between_decisions": 25,
        },
    }


@pytest.mark.integration
async def test_insert_persists_snapshot_and_returns_row(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    session.add(sess_row)
    await session.flush()

    repo = StateSnapshotRepo(session)
    ts = datetime.now(UTC)
    record = StateSnapshotInsert(
        session_id=session_id,
        tick_id=0,
        ts=ts,
        state=_state_dict(session_id, 0),
    )
    row = await repo.insert(record)

    assert row.id is not None
    assert row.tick_id == 0
    assert row.ts == ts
    assert row.state["tick_id"] == 0


@pytest.mark.integration
async def test_insert_preserves_full_state_jsonb_round_trip(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    """JSONB must round-trip through Postgres bit-for-bit identical."""
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    session.add(sess_row)
    await session.flush()

    repo = StateSnapshotRepo(session)
    state = _state_dict(session_id, 7)
    state["currently_speaking_count"] = 2
    state["silence_run_sec"] = 1.75
    state["rolling_global_transcript_2min"] = "alpha\nbeta\ngamma"

    inserted = await repo.insert(
        StateSnapshotInsert(
            session_id=session_id,
            tick_id=7,
            ts=datetime.now(UTC),
            state=state,
        )
    )
    await session.flush()

    reread = (
        await session.execute(
            select(StateSnapshot).where(StateSnapshot.id == inserted.id),
        )
    ).scalar_one()

    assert reread.state == state


@pytest.mark.integration
async def test_inserts_share_session_and_index_orders_by_tick_id(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    """Read-back via the (session_id, tick_id) index returns in tick order."""
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    session.add(sess_row)
    await session.flush()

    repo = StateSnapshotRepo(session)
    for tick in (3, 1, 0, 4, 2):
        await repo.insert(
            StateSnapshotInsert(
                session_id=session_id,
                tick_id=tick,
                ts=datetime.now(UTC),
                state=_state_dict(session_id, tick),
            )
        )

    rows = (
        (
            await session.execute(
                select(StateSnapshot)
                .where(StateSnapshot.session_id == session_id)
                .order_by(StateSnapshot.tick_id.asc()),
            )
        )
        .scalars()
        .all()
    )
    assert [r.tick_id for r in rows] == [0, 1, 2, 3, 4]


@pytest.mark.integration
async def test_session_cascade_deletes_snapshots(
    session: AsyncSession,
    session_id: uuid.UUID,
) -> None:
    """Brief §10.1: FK cascade so a session purge takes its snapshots with it."""
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    session.add(sess_row)
    await session.flush()

    repo = StateSnapshotRepo(session)
    await repo.insert(
        StateSnapshotInsert(
            session_id=session_id,
            tick_id=0,
            ts=datetime.now(UTC),
            state=_state_dict(session_id, 0),
        )
    )

    await session.delete(sess_row)
    await session.flush()

    remaining = (
        (
            await session.execute(
                select(StateSnapshot).where(StateSnapshot.session_id == session_id),
            )
        )
        .scalars()
        .all()
    )
    assert remaining == []
