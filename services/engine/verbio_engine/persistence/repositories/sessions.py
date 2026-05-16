"""Session repository — lookup + lifecycle transitions.

Used by the LiveKit agent worker when it joins a room. The web app
creates the `Session` row up-front (status="scheduled", with a
livekit_room_name); the engine looks the row up by room name on join,
then transitions `status` and `actual_start` / `actual_end` as the room
opens and closes.

Phase 1 needs:
  - `get_by_room_name`: look up the pre-created session.
  - `mark_started`: idempotent transition to status="live" + actual_start.
  - `mark_ended`:   idempotent transition to status="ended" + actual_end.

Anything more (create, list, search) belongs in the web service —
the engine never creates sessions on its own.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from verbio_engine.persistence.models import Session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class SessionRepo:
    """Async repository for the `sessions` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_room_name(self, room_name: str) -> Session | None:
        """Look up the session pre-created by the web app for this room."""
        stmt = select(Session).where(Session.livekit_room_name == room_name)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def mark_started(self, session: Session, *, at: datetime | None = None) -> Session:
        """Transition `scheduled` → `live` and stamp `actual_start`.

        Idempotent: re-entry on a `live` row is a no-op so a worker
        crash-restart doesn't clobber the original start timestamp.
        """
        if session.status == "live":
            return session
        session.status = "live"
        session.actual_start = at or datetime.now(UTC)
        await self._session.flush([session])
        return session

    async def mark_ended(self, session: Session, *, at: datetime | None = None) -> Session:
        """Transition `live` → `ended` and stamp `actual_end`.

        Idempotent: re-entry on an `ended` row is a no-op.
        """
        if session.status == "ended":
            return session
        session.status = "ended"
        session.actual_end = at or datetime.now(UTC)
        await self._session.flush([session])
        return session
