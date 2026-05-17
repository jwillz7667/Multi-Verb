"""Researcher actions repository — write API for the audit trail (P5 L2).

Every command drained from the bus is persisted here regardless of
whether the engine acts on it (mute/pause control-plane commands have
no resulting decision; force_* commands link to one via
`resulting_decision_id`). The audit row is the load-bearing record;
researchers must be able to answer "what did I tell the moderator?"
even when the moderator chose not to speak.

Idempotency
-----------

The repo uses the wire-shape `command_id` (a UUID minted by the web
producer) as the row primary key. A duplicate insert (replay on engine
restart, double-send from a flaky client) is detected with a select-
then-act check rather than a postgres-dialect `ON CONFLICT` — matches
the codebase's existing upsert pattern (see participants repo). The
duplicate is a no-op; the original row is preserved verbatim.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from verbio_engine.persistence.models import ResearcherAction

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ResearcherActionInsert:
    """Input record for `ResearcherActionRepo.insert`.

    `command_id` is the wire-shape UUID from `ResearcherCommand` and
    becomes the row PK so a re-drain after engine restart deduplicates
    to the original row. `resulting_decision_id` is null for control-
    plane commands (mute, pause, set_quietness_budget, flag_moment) and
    populated by P5 L3+ for force_* / whisper commands once the
    matching decision row exists.
    """

    command_id: uuid.UUID
    session_id: uuid.UUID
    researcher_id: uuid.UUID
    ts: datetime
    command_type: str
    payload: dict[str, object] | None = None
    resulting_decision_id: uuid.UUID | None = None


class ResearcherActionRepo:
    """Async repository for the `researcher_actions` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: ResearcherActionInsert) -> ResearcherAction:
        """Persist one researcher_action row; idempotent on command_id.

        Returns the row — either the freshly-inserted one or, on a
        duplicate command_id, the pre-existing one. Either way the
        caller sees a row that reflects the canonical state of the
        audit trail for this command.
        """
        existing = await self._fetch(record.command_id)
        if existing is not None:
            return existing

        row = ResearcherAction(
            id=record.command_id,
            session_id=record.session_id,
            researcher_id=record.researcher_id,
            ts=record.ts,
            command_type=record.command_type,
            # SQLAlchemy persists JSONB as-is; defensive copy keeps the
            # caller's dict immutable from the ORM's POV.
            payload=dict(record.payload) if record.payload is not None else None,
            resulting_decision_id=record.resulting_decision_id,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row

    async def set_resulting_decision_id(
        self,
        *,
        command_id: uuid.UUID,
        decision_id: uuid.UUID,
    ) -> int:
        """Stamp `resulting_decision_id` on an existing audit row (P5 L3).

        Returns the number of rows updated — 0 means the audit row wasn't
        found (likely because `_persist_commands` skipped it due to a
        malformed researcher_id), which the caller can ignore. Issued in
        the decision transaction so the FK lands atomically with the
        decision row it points at.
        """
        stmt = (
            update(ResearcherAction)
            .where(ResearcherAction.id == command_id)
            .values(resulting_decision_id=decision_id)
        )
        result = await self._session.execute(stmt)
        # AsyncSession.execute returns Result[Any]; DML statements
        # actually yield a CursorResult that exposes rowcount. The static
        # type isn't narrowed, so getattr keeps mypy strict-happy while
        # preserving the actual runtime semantics.
        return int(getattr(result, "rowcount", 0) or 0)

    async def _fetch(self, command_id: uuid.UUID) -> ResearcherAction | None:
        stmt = select(ResearcherAction).where(ResearcherAction.id == command_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()
