"""State-snapshot repository — one insert per tick (Phase 2 L3).

The tick loop hands a `SessionState` projection to the snapshot listener
every 500ms; the listener serialises it once and asks this repo to put
a row down. The repo has only the write path for now — replay reads
land in Phase 6 alongside the rest of the export tooling.

API:
  - `insert(record)` : persist one snapshot; returns the row with its id.

No `insert_many` here: writes arrive serially from the tick loop, so
batching is not on the critical path. If a future replay-import flow
needs bulk writes it can grow the method then.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from verbio_engine.persistence.models import StateSnapshot

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class StateSnapshotInsert:
    """Input record for `StateSnapshotRepo.insert`.

    `state` is the canonical `SessionState.model_dump(mode="json")` form
    — UUIDs as strings, datetimes as ISO-8601 — so the JSONB column
    holds exactly what the SSE consumer sees and replay parses with the
    shared schema.
    """

    session_id: uuid.UUID
    tick_id: int
    ts: datetime
    state: dict[str, object]


class StateSnapshotRepo:
    """Async repository for the `state_snapshots` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: StateSnapshotInsert) -> StateSnapshot:
        """Persist a single snapshot and return the row with its id."""
        row = StateSnapshot(
            session_id=record.session_id,
            tick_id=record.tick_id,
            ts=record.ts,
            state=record.state,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row
