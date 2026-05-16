"""Tick-snapshot listener — persist + publish (Phase 2 L3).

The `TickLoop` invokes its `SnapshotListener` callback after every
`StateStore.advance_to(t)` call. This module's
`PersistAndPublishListener` is the production wiring:

  1. Open a short transaction.
  2. Insert the SessionState into `state_snapshots`.
  3. Commit.
  4. Publish a `state_snapshot` envelope to Redis pub/sub for the
     dashboard SSE route.

Persist-before-publish is invariant (brief §6 invariant 1). The audit
trail must never have a state visible to subscribers that isn't
already on disk in Postgres — otherwise a reconnect/backfill could
diverge from the live stream. The two phases run serially inside the
listener so this ordering is guaranteed by control flow, not by
optimistic best-effort.

Error policy:
  - Persist failure: the exception propagates. `TickLoop.tick_once`
    catches it inside its `try/except`, increments
    `stats.listener_failures`, and continues to the next tick. We
    deliberately *don't* swallow inside the listener — losing snapshots
    silently would break the audit-trail invariant the brief depends
    on, so a noisy log + counter beats a quiet drop.
  - Publish failure: swallowed inside `RedisEventPublisher.publish`
    itself (already logged there). The SSE route's Postgres backfill
    on next reconnect recovers any lost-in-flight envelope.

Each tick uses its own session/transaction. The tick loop is
sequential per session, so the connection pool sees at most one
checkout per process per tick — minimal pressure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from verbio_engine.persistence import StateSnapshotInsert, StateSnapshotRepo
from verbio_engine.realtime import state_snapshot_event

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.domain.session_state import SessionState
    from verbio_engine.realtime import EventPublisher


class PersistAndPublishListener:
    """`SnapshotListener` impl: write `state_snapshots` row, then publish.

    Designed for direct use as `TickLoop(listener=PersistAndPublishListener(...))`.
    Instances are awaitable callables — pytest can also unit-test the
    `__call__` path without spinning up the tick loop.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher

    async def __call__(self, state: SessionState) -> None:
        # Serialise once. Both the DB JSONB column and the SSE payload
        # use the same canonical form, and Pydantic round-trips
        # `SessionState` losslessly through `model_dump(mode="json")`,
        # so a single dump keeps DB and wire perfectly aligned.
        state_dict = state.model_dump(mode="json")

        async with self._session_factory() as db, db.begin():
            repo = StateSnapshotRepo(db)
            row = await repo.insert(
                StateSnapshotInsert(
                    session_id=state.session_id,
                    tick_id=state.tick_id,
                    ts=state.t,
                    state=state_dict,
                )
            )

        # Persist-before-publish: only reach here after the transaction
        # has committed. The envelope's `id` is `str(row.id)` so the
        # SSE backfill can resolve `last-event-id` straight to the row.
        await self._publisher.publish(
            state_snapshot_event(
                snapshot_id=row.id,
                state=state,
            )
        )
