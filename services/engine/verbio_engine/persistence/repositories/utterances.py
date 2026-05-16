"""Utterance repository — STT writes + transcript reads.

The STT pipeline produces interim rows (low confidence, partial text)
followed by a final row per segment. We persist both: interim for live
UI rendering, final for downstream rules and exports.

API:
  - `insert(...)`     : single insert; returns the persisted row.
  - `insert_many(...)`: batched insert for replay/import paths.
  - `recent(...)`     : last N utterances for a session, ordered by start.

Anything more elaborate (full-text search, time-range scans, joins) is
deferred to the phase that needs it. Repositories must not grow methods
on speculation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from verbio_engine.persistence.models import Utterance

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class UtteranceInsert:
    """Input record for `UtteranceRepo.insert`.

    A frozen dataclass rather than the ORM model so call-sites don't
    need to think about SQLAlchemy identity-map semantics. The repo
    maps these into ORM rows internally.
    """

    session_id: uuid.UUID
    participant_id: uuid.UUID
    start_ts: datetime
    end_ts: datetime
    text: str
    is_final: bool
    confidence: float | None = None


class UtteranceRepo:
    """Async repository for the `utterances` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: UtteranceInsert) -> Utterance:
        """Persist a single utterance and return the row with its id."""
        row = Utterance(
            session_id=record.session_id,
            participant_id=record.participant_id,
            start_ts=record.start_ts,
            end_ts=record.end_ts,
            text=record.text,
            confidence=record.confidence,
            is_final=record.is_final,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row

    async def insert_many(self, records: Iterable[UtteranceInsert]) -> list[Utterance]:
        """Persist a batch; returns rows in input order."""
        rows = [
            Utterance(
                session_id=r.session_id,
                participant_id=r.participant_id,
                start_ts=r.start_ts,
                end_ts=r.end_ts,
                text=r.text,
                confidence=r.confidence,
                is_final=r.is_final,
            )
            for r in records
        ]
        self._session.add_all(rows)
        await self._session.flush(rows)
        return rows

    async def recent(
        self,
        session_id: uuid.UUID,
        *,
        limit: int = 50,
        finals_only: bool = False,
    ) -> Sequence[Utterance]:
        """Most recent utterances for a session, oldest-first within the page.

        Args:
            session_id: scope.
            limit: hard cap on rows returned.
            finals_only: when True, skip interim rows. Rules use this;
                live UI uses False so partial text streams.
        """
        stmt = select(Utterance).where(Utterance.session_id == session_id)
        if finals_only:
            stmt = stmt.where(Utterance.is_final.is_(True))
        stmt = stmt.order_by(Utterance.start_ts.desc()).limit(limit)
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows
