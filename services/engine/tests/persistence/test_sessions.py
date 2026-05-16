"""Integration tests for the session repository.

Covers the lifecycle transitions the LiveKit agent worker drives:
look-up by room name, start, end. Both transitions must be idempotent
so a worker crash-restart can't clobber the original timestamps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from verbio_engine.persistence import Session, SessionRepo

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
async def test_get_by_room_name_returns_seeded_row(session: AsyncSession) -> None:
    room = f"room-{uuid.uuid4()}"
    session.add(Session(livekit_room_name=room, status="scheduled"))
    await session.flush()

    repo = SessionRepo(session)
    found = await repo.get_by_room_name(room)

    assert found is not None
    assert found.livekit_room_name == room
    assert found.status == "scheduled"


@pytest.mark.integration
async def test_get_by_room_name_returns_none_for_unknown(session: AsyncSession) -> None:
    repo = SessionRepo(session)
    assert await repo.get_by_room_name("does-not-exist") is None


@pytest.mark.integration
async def test_mark_started_transitions_scheduled_to_live(session: AsyncSession) -> None:
    row = Session(livekit_room_name=f"room-{uuid.uuid4()}", status="scheduled")
    session.add(row)
    await session.flush()

    repo = SessionRepo(session)
    started_at = datetime.now(UTC)
    out = await repo.mark_started(row, at=started_at)

    assert out.status == "live"
    assert out.actual_start == started_at


@pytest.mark.integration
async def test_mark_started_is_idempotent_on_live_row(session: AsyncSession) -> None:
    original_start = datetime.now(UTC)
    row = Session(
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
        actual_start=original_start,
    )
    session.add(row)
    await session.flush()

    repo = SessionRepo(session)
    out = await repo.mark_started(row, at=datetime.now(UTC))

    # Original start preserved — restart must not rewrite history.
    assert out.actual_start == original_start


@pytest.mark.integration
async def test_mark_ended_transitions_live_to_ended(session: AsyncSession) -> None:
    row = Session(
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
        actual_start=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()

    repo = SessionRepo(session)
    ended_at = datetime.now(UTC)
    out = await repo.mark_ended(row, at=ended_at)

    assert out.status == "ended"
    assert out.actual_end == ended_at
