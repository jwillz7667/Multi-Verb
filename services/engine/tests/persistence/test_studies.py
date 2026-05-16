"""Integration tests for the study repository (Phase 3 L11).

The engine treats studies as read-only inputs in production — web
writes them via Prisma. We expose `insert` purely so integration
tests (this one + the runtime suite) can seed studies alongside
sessions.

Coverage:
  * `insert` round-trips all jsonb columns + the rules_version.
  * `get_by_id` returns None for a missing id (the runtime treats
    that as "study deleted between schedule and start" and falls
    back to defaults rather than crashing).
  * `sessions.study_id` FK rejects an unknown study id — proves the
    L11 migration installed the constraint correctly.
  * ON DELETE SET NULL on the FK: deleting the study leaves the
    session row intact with study_id NULL'd (the brief's rationale —
    historical sessions outlive purged studies).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from verbio_engine.persistence import (
    Session,
    Study,
    StudyInsert,
    StudyRepo,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _study_input(**overrides: object) -> StudyInsert:
    """Build a StudyInsert with all required defaults populated."""
    base: dict[str, object] = {
        "org_id": uuid.uuid4(),
        "name": "Coffee Shop Habits",
        "prompt": "Tell me about your morning coffee routine.",
        "rules_version": "v1.0",
        "created_by": uuid.uuid4(),
        "rules_config": {"silence_gap": {"threshold_sec": 12.0}},
        "moderator_persona": {"voice": "cartesia/sonic-en-female-1"},
        "retention_policy": {"recordings_ttl_days": 90},
    }
    base.update(overrides)
    return StudyInsert(**base)  # type: ignore[arg-type]


@pytest.mark.integration
async def test_insert_round_trips_all_columns(session: AsyncSession) -> None:
    repo = StudyRepo(session)
    record = _study_input()

    row = await repo.insert(record)

    assert row.id is not None
    assert row.name == "Coffee Shop Habits"
    assert row.prompt == "Tell me about your morning coffee routine."
    assert row.rules_version == "v1.0"
    assert row.rules_config == {"silence_gap": {"threshold_sec": 12.0}}
    assert row.moderator_persona == {"voice": "cartesia/sonic-en-female-1"}
    assert row.retention_policy == {"recordings_ttl_days": 90}
    # server_default=now() should populate created_at without us setting it.
    assert row.created_at is not None


@pytest.mark.integration
async def test_get_by_id_returns_none_for_missing(session: AsyncSession) -> None:
    repo = StudyRepo(session)
    assert await repo.get_by_id(uuid.uuid4()) is None


@pytest.mark.integration
async def test_insert_then_get_by_id_returns_same_row(session: AsyncSession) -> None:
    repo = StudyRepo(session)
    written = await repo.insert(_study_input(name="Pets"))

    fetched = await repo.get_by_id(written.id)

    assert fetched is not None
    assert fetched.id == written.id
    assert fetched.name == "Pets"


@pytest.mark.integration
async def test_sessions_study_id_fk_rejects_unknown_study(session: AsyncSession) -> None:
    """The FK added in migration 0006 must reject a dangling study reference."""
    bogus = Session(
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="scheduled",
        study_id=uuid.uuid4(),
    )
    session.add(bogus)

    with pytest.raises(IntegrityError):
        await session.flush()


@pytest.mark.integration
async def test_study_delete_nulls_session_study_id(session: AsyncSession) -> None:
    """`ON DELETE SET NULL`: purging a study leaves sessions intact."""
    study = await StudyRepo(session).insert(_study_input())
    sess = Session(
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="scheduled",
        study_id=study.id,
    )
    session.add(sess)
    await session.flush()
    sess_id = sess.id

    await session.execute(delete(Study).where(Study.id == study.id))
    await session.flush()
    # Discard the cached identity-mapped copy so the next select reads
    # the post-FK-action state from the database.
    session.expire_all()

    refreshed = (await session.execute(select(Session).where(Session.id == sess_id))).scalar_one()
    assert refreshed.study_id is None, "ON DELETE SET NULL should have NULL'd study_id"
