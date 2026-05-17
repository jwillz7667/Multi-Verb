"""Session flags repository — replay-bookmark write API (P5 L7).

Per brief §5.4 / §10.1, `session_flags` records moments the dashboard
should foreground during replay. Two write paths land rows here:

  * **Researcher-issued** via the `flag_moment` command — the listener
    walks the drained batch and inserts one row per command. The audit
    row in `researcher_actions` is the load-bearing record of researcher
    intent; the matching `session_flags` row is the replay-surface
    projection of that intent.
  * **Engine-auto** (future) — the engine may auto-flag a stalled
    thread or a participant rejoining mid-incident. Those rows set
    `auto_generated=True` and leave `researcher_id` null.

Idempotency
-----------

`flag_moment` commands carry their own UUID `command_id`; the
listener uses that UUID as the `session_flags.id` so a re-drain after
an engine restart deduplicates to the original row. The repo uses the
same select-then-act pattern as `researcher_actions` for consistency.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from verbio_engine.persistence.models import SessionFlag

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class SessionFlagInsert:
    """Input record for `SessionFlagRepo.insert`.

    `id` is the source-of-truth UUID for the row; for researcher-issued
    flags this is the `command_id` of the originating `flag_moment` so
    re-drains deduplicate. For engine-auto flags this is a fresh
    `uuid4()`.

    `researcher_id` is `None` for `auto_generated=True` rows and the
    researcher's UUID otherwise.
    """

    id: uuid.UUID
    session_id: uuid.UUID
    ts: datetime
    researcher_id: uuid.UUID | None
    note: str | None
    auto_generated: bool


class SessionFlagRepo:
    """Async repository for the `session_flags` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: SessionFlagInsert) -> SessionFlag:
        """Persist one session_flag row; idempotent on `id`.

        Returns the row — either the freshly-inserted one or, on a
        duplicate `id`, the pre-existing one. The duplicate path is
        the audit-replay safety net: a researcher's flag_moment may be
        drained twice across an engine restart, and the bookmark must
        not double-count in the replay UI.
        """
        existing = await self._fetch(record.id)
        if existing is not None:
            return existing

        row = SessionFlag(
            id=record.id,
            session_id=record.session_id,
            ts=record.ts,
            researcher_id=record.researcher_id,
            note=record.note,
            auto_generated=record.auto_generated,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row

    async def _fetch(self, flag_id: uuid.UUID) -> SessionFlag | None:
        stmt = select(SessionFlag).where(SessionFlag.id == flag_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
