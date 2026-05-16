"""Integration tests for the utterance repository.

These tests boot a real Postgres container (testcontainers) and exercise
the full path: Alembic migrations → ORM models → repository writes →
read-back. We deliberately do not mock the DB — the brief mandates
testcontainers / disposable schema for integration coverage so
mock/prod divergence can't mask a broken migration.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from verbio_engine.persistence import (
    Participant,
    Session,
    UtteranceInsert,
    UtteranceRepo,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.integration
async def test_insert_persists_utterance_and_returns_row(
    session: AsyncSession,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> None:
    repo = UtteranceRepo(session)
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    part_row = Participant(
        id=participant_id,
        session_id=session_id,
        display_name="Maya",
        role="participant",
        livekit_identity=f"id-{uuid.uuid4()}",
    )
    session.add_all([sess_row, part_row])
    await session.flush()

    start = datetime.now(UTC)
    end = start + timedelta(seconds=2)
    row = await repo.insert(
        UtteranceInsert(
            session_id=session_id,
            participant_id=participant_id,
            start_ts=start,
            end_ts=end,
            text="so the tracking thing feels invasive",
            is_final=True,
            confidence=0.92,
        )
    )

    assert row.id is not None
    assert row.text == "so the tracking thing feels invasive"
    assert row.is_final is True


@pytest.mark.integration
async def test_recent_orders_oldest_first_within_limit(
    session: AsyncSession,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> None:
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    part_row = Participant(
        id=participant_id,
        session_id=session_id,
        display_name="Devon",
        role="participant",
        livekit_identity=f"id-{uuid.uuid4()}",
    )
    session.add_all([sess_row, part_row])
    await session.flush()

    repo = UtteranceRepo(session)
    base = datetime.now(UTC)
    for i in range(5):
        await repo.insert(
            UtteranceInsert(
                session_id=session_id,
                participant_id=participant_id,
                start_ts=base + timedelta(seconds=i),
                end_ts=base + timedelta(seconds=i, milliseconds=800),
                text=f"chunk {i}",
                is_final=i % 2 == 0,
            )
        )

    rows = await repo.recent(session_id, limit=3)
    assert [r.text for r in rows] == ["chunk 2", "chunk 3", "chunk 4"]


@pytest.mark.integration
async def test_recent_finals_only_filters_interim(
    session: AsyncSession,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> None:
    sess_row = Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    part_row = Participant(
        id=participant_id,
        session_id=session_id,
        display_name="Priya",
        role="participant",
        livekit_identity=f"id-{uuid.uuid4()}",
    )
    session.add_all([sess_row, part_row])
    await session.flush()

    repo = UtteranceRepo(session)
    base = datetime.now(UTC)
    await repo.insert_many(
        [
            UtteranceInsert(
                session_id=session_id,
                participant_id=participant_id,
                start_ts=base,
                end_ts=base + timedelta(milliseconds=200),
                text="so",
                is_final=False,
            ),
            UtteranceInsert(
                session_id=session_id,
                participant_id=participant_id,
                start_ts=base,
                end_ts=base + timedelta(milliseconds=900),
                text="so I think",
                is_final=False,
            ),
            UtteranceInsert(
                session_id=session_id,
                participant_id=participant_id,
                start_ts=base,
                end_ts=base + timedelta(milliseconds=1700),
                text="so I think we should pivot.",
                is_final=True,
                confidence=0.97,
            ),
        ]
    )

    finals = await repo.recent(session_id, finals_only=True)
    assert [r.text for r in finals] == ["so I think we should pivot."]
    assert finals[0].confidence == pytest.approx(0.97)
