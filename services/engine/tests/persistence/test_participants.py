"""Integration tests for the participant repository.

Covers the LiveKit-driven join / re-join / leave lifecycle. The key
invariants exercised:

- A re-join must reuse the existing row (so utterances stay glued to
  one `participant_id` across drops).
- `joined_at` is set once on first connect; reconnects don't overwrite
  it (analysis needs the original session-entry timestamp).
- `left_at` reflects the *current* disconnected state — cleared on
  reconnect, re-stamped on subsequent disconnect.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from verbio_engine.persistence import (
    ParticipantJoin,
    ParticipantRepo,
    Session,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_session(session: AsyncSession) -> uuid.UUID:
    row = Session(livekit_room_name=f"room-{uuid.uuid4()}", status="live")
    session.add(row)
    await session.flush()
    return row.id


@pytest.mark.integration
async def test_upsert_on_join_inserts_first_time(session: AsyncSession) -> None:
    session_id = await _seed_session(session)
    repo = ParticipantRepo(session)

    joined_at = datetime.now(UTC)
    row = await repo.upsert_on_join(
        ParticipantJoin(
            session_id=session_id,
            livekit_identity="id-alice",
            display_name="Alice",
            role="participant",
            joined_at=joined_at,
        )
    )

    assert row.id is not None
    assert row.display_name == "Alice"
    assert row.joined_at == joined_at
    assert row.left_at is None


@pytest.mark.integration
async def test_upsert_on_join_preserves_original_joined_at_on_reconnect(
    session: AsyncSession,
) -> None:
    session_id = await _seed_session(session)
    repo = ParticipantRepo(session)

    first_joined_at = datetime.now(UTC)
    first = await repo.upsert_on_join(
        ParticipantJoin(
            session_id=session_id,
            livekit_identity="id-bob",
            display_name="Bob",
            role="participant",
            joined_at=first_joined_at,
        )
    )

    second = await repo.upsert_on_join(
        ParticipantJoin(
            session_id=session_id,
            livekit_identity="id-bob",
            display_name="Bob (renamed mid-session — ignored)",
            role="participant",
            joined_at=datetime.now(UTC),
        )
    )

    assert second.id == first.id
    assert second.joined_at == first_joined_at
    assert second.left_at is None


@pytest.mark.integration
async def test_upsert_on_join_clears_left_at_on_reconnect(session: AsyncSession) -> None:
    session_id = await _seed_session(session)
    repo = ParticipantRepo(session)

    await repo.upsert_on_join(
        ParticipantJoin(
            session_id=session_id,
            livekit_identity="id-carol",
            display_name="Carol",
            role="participant",
        )
    )
    left = await repo.mark_left(session_id, "id-carol")
    assert left is not None
    assert left.left_at is not None

    rejoined = await repo.upsert_on_join(
        ParticipantJoin(
            session_id=session_id,
            livekit_identity="id-carol",
            display_name="Carol",
            role="participant",
        )
    )

    assert rejoined.left_at is None


@pytest.mark.integration
async def test_mark_left_returns_none_for_unknown_identity(
    session: AsyncSession,
) -> None:
    session_id = await _seed_session(session)
    repo = ParticipantRepo(session)

    assert await repo.mark_left(session_id, "ghost-identity") is None
