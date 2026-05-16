"""Study repository — lookup by id + test-side insert.

The engine treats studies as read-only inputs: web creates them via
Prisma, the engine reads them when a session attached to a study comes
online. We expose `insert` because integration tests need to seed
study rows alongside session rows; in production the engine never
writes a study.

`get_by_id` returns `None` instead of raising on miss because the
runtime treats a missing study as "session has no study attached" —
the loader caller decides whether that's an error (a session with a
non-null study_id whose row is gone is the only real error case;
nullable study_id is the legitimate Phase 1-2 path).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from verbio_engine.persistence.models import Study

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class StudyInsert:
    """Input record for `StudyRepo.insert` — test-side seeding only.

    `prompt_embedding` is intentionally not exposed; embeddings live
    in-process today (see migration 0006 docstring). Callers that want
    to backfill the column once pgvector lands can do so via a follow-up
    repo method.
    """

    org_id: uuid.UUID
    name: str
    prompt: str
    rules_version: str
    created_by: uuid.UUID
    rules_config: dict[str, Any] = field(default_factory=dict)
    moderator_persona: dict[str, Any] = field(default_factory=dict)
    retention_policy: dict[str, Any] = field(default_factory=dict)
    study_id: uuid.UUID | None = None


class StudyRepo:
    """Async repository for the `studies` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, study_id: uuid.UUID) -> Study | None:
        """Look up a study by id. Returns `None` if no such row exists."""
        stmt = select(Study).where(Study.id == study_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def insert(self, record: StudyInsert) -> Study:
        """Insert a study row. Used by tests; production writes go via web."""
        row = Study(
            id=record.study_id or uuid.uuid4(),
            org_id=record.org_id,
            name=record.name,
            prompt=record.prompt,
            rules_config=dict(record.rules_config),
            rules_version=record.rules_version,
            moderator_persona=dict(record.moderator_persona),
            retention_policy=dict(record.retention_policy),
            created_by=record.created_by,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row
