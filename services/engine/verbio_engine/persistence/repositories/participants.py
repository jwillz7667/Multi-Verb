"""Participant repository — upsert by (session_id, livekit_identity).

The LiveKit Agents framework fires `participant_connected` /
`participant_disconnected` events per LiveKit identity. The DB's
`Participant` rows are keyed by their own UUID but are uniquely
identified within a session by `(session_id, livekit_identity)` (brief
§10.1; enforced by `uq_participants_session_id_livekit_identity`).

A reconnect must reuse the existing row (so utterances stay glued to one
participant_id across drops); this is what `upsert_on_join` does. It
inserts on first sight, updates `joined_at` on the *first* connect only,
and is a no-op for joined_at on subsequent reconnects.

`mark_left` stamps `left_at` at disconnect; if the participant rejoins
later, we clear `left_at` so the column always reflects the *current*
disconnected state, not the most recent historical one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from verbio_engine.persistence.models import Participant

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ParticipantJoin:
    """Input record for `ParticipantRepo.upsert_on_join`."""

    session_id: uuid.UUID
    livekit_identity: str
    display_name: str
    role: str
    joined_at: datetime | None = None


class ParticipantRepo:
    """Async repository for the `participants` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_on_join(self, record: ParticipantJoin) -> Participant:
        """Insert the participant if new, or refresh re-join state.

        Identity is `(session_id, livekit_identity)`. On reconnect we
        clear `left_at` so the column reflects current presence; we
        intentionally do NOT overwrite `joined_at` so analyses keep the
        original session-entry timestamp.
        """
        existing = await self._fetch(record.session_id, record.livekit_identity)
        if existing is not None:
            existing.left_at = None
            await self._session.flush([existing])
            return existing

        row = Participant(
            session_id=record.session_id,
            livekit_identity=record.livekit_identity,
            display_name=record.display_name,
            role=record.role,
            joined_at=record.joined_at or datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush([row])
        return row

    async def mark_left(
        self,
        session_id: uuid.UUID,
        livekit_identity: str,
        *,
        at: datetime | None = None,
    ) -> Participant | None:
        """Stamp `left_at`. Returns the row, or None if not found (defensive)."""
        existing = await self._fetch(session_id, livekit_identity)
        if existing is None:
            return None
        existing.left_at = at or datetime.now(UTC)
        await self._session.flush([existing])
        return existing

    async def _fetch(
        self,
        session_id: uuid.UUID,
        livekit_identity: str,
    ) -> Participant | None:
        stmt = select(Participant).where(
            Participant.session_id == session_id,
            Participant.livekit_identity == livekit_identity,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
