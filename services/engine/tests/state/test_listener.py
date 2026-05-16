"""Unit tests for `PersistAndPublishListener` (Phase 2 L3).

Exercises the listener's contract without a real DB or Redis:

  * persist-before-publish ordering (brief §6 invariant 1)
  * publish failures do not crash the listener (publisher swallows)
  * persist failures DO propagate (so TickLoop counts them and the
    audit-trail invariant isn't violated by silent drops)
  * snapshot's `model_dump(mode="json")` is what lands in JSONB
  * the published envelope matches the persisted row id
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from verbio_engine.domain import ParticipantState, QuietnessBudget, SessionState
from verbio_engine.persistence import StateSnapshot
from verbio_engine.realtime import (
    StateSnapshotEventEnvelope,
    UtteranceEventEnvelope,
)
from verbio_engine.state.listener import PersistAndPublishListener

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordingAction:
    """One observable event during a listener invocation."""

    kind: str  # "persist" | "publish"
    payload: list[StateSnapshot] | UtteranceEventEnvelope | StateSnapshotEventEnvelope


@dataclass(slots=True)
class _FakeAsyncSession:
    """Stand-in for SQLAlchemy AsyncSession that records `add` + `flush`.

    The listener's `session_factory()` must yield this; `begin()` is a
    context manager that commits on exit (no-op here).
    """

    actions: list[_RecordingAction]
    fail_on_flush: bool = False
    added: list[StateSnapshot] = field(default_factory=list)
    _exited: bool = False

    def add(self, instance: StateSnapshot) -> None:
        self.added.append(instance)

    async def flush(self, _instances: list[StateSnapshot] | None = None) -> None:
        if self.fail_on_flush:
            msg = "simulated DB outage"
            raise RuntimeError(msg)
        # Assign an id eagerly, mimicking the server-side default UUID gen
        # the real Postgres column has — the listener reads `row.id`
        # immediately after flush.
        for row in self.added:
            row.id = uuid.uuid4()
        self.actions.append(_RecordingAction("persist", list(self.added)))

    def begin(self) -> object:
        outer = self

        class _Tx:
            async def __aenter__(self) -> _FakeAsyncSession:
                return outer

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Tx()


@dataclass(slots=True)
class _FakeSessionFactory:
    """Async-context-manager session factory the listener calls.

    `factory()` returns an object whose `__aenter__` yields a fresh
    fake session. The recorded action log is shared across sessions so
    assertions can see the persist/publish interleaving across the
    listener's full call.
    """

    actions: list[_RecordingAction]
    fail_on_flush: bool = False
    spawned: list[_FakeAsyncSession] = field(default_factory=list)

    def __call__(self) -> object:
        sess = _FakeAsyncSession(actions=self.actions, fail_on_flush=self.fail_on_flush)
        self.spawned.append(sess)

        @asynccontextmanager
        async def _cm() -> AsyncIterator[_FakeAsyncSession]:
            try:
                yield sess
            finally:
                sess._exited = True  # fake's own bookkeeping

        return _cm()


@dataclass(slots=True)
class _FakePublisher:
    actions: list[_RecordingAction]
    fail: bool = False
    last_event: UtteranceEventEnvelope | StateSnapshotEventEnvelope | None = None

    async def publish(
        self,
        event: UtteranceEventEnvelope | StateSnapshotEventEnvelope,
    ) -> int:
        if self.fail:
            # Mirrors RedisEventPublisher.publish, which catches+logs.
            return 0
        self.last_event = event
        self.actions.append(_RecordingAction("publish", event))
        return 1

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(*, session_id: uuid.UUID | None = None, tick_id: int = 0) -> SessionState:
    sid = session_id or uuid.uuid4()
    started = datetime.now(UTC)
    return SessionState(
        session_id=sid,
        tick_id=tick_id,
        t=started,
        started_at=started,
        elapsed_sec=float(tick_id) * 0.5,
        participants={
            "p1": ParticipantState(  # type: ignore[call-arg]
                participant_id="p1",
                display_name="Maya",
                joined_at=started,
            ),
        },
        currently_speaking_count=1,
        silence_run_sec=0.0,
        quietness_budget=QuietnessBudget(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_listener_persists_then_publishes_in_that_order() -> None:
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions)
    publisher = _FakePublisher(actions=actions)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    await listener(_state(tick_id=3))

    assert [a.kind for a in actions] == ["persist", "publish"]


async def test_listener_serialises_state_to_json_mode_dump() -> None:
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions)
    publisher = _FakePublisher(actions=actions)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    state = _state(tick_id=11)
    await listener(state)

    persisted_rows = [a.payload for a in actions if a.kind == "persist"]
    assert persisted_rows, "listener never persisted"
    first = persisted_rows[0]
    assert isinstance(first, list)
    row = first[0]
    assert isinstance(row, StateSnapshot)

    # JSON-mode dump: UUIDs as strings, datetimes as ISO-8601 — never
    # raw Python objects, so Postgres JSONB stores the canonical wire
    # form (matches the SSE payload byte-for-byte).
    expected = state.model_dump(mode="json")
    assert row.state == expected
    assert row.state["session_id"] == str(state.session_id)
    assert row.state["tick_id"] == 11


async def test_published_envelope_mirrors_persisted_row_id() -> None:
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions)
    publisher = _FakePublisher(actions=actions)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    state = _state(tick_id=7)
    await listener(state)

    persist_payload = next(a.payload for a in actions if a.kind == "persist")
    assert isinstance(persist_payload, list)
    persisted_row = persist_payload[0]
    published_event = next(a.payload for a in actions if a.kind == "publish")

    assert isinstance(published_event, StateSnapshotEventEnvelope)
    assert published_event.id == str(persisted_row.id)
    assert published_event.payload.snapshot_id == persisted_row.id
    assert published_event.session_id == state.session_id
    assert published_event.ts == state.t


async def test_persist_failure_propagates_so_tick_loop_can_count_it() -> None:
    """Brief invariant: no silent snapshot drops. The TickLoop's
    listener_failures counter exists *because* this path raises."""
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions, fail_on_flush=True)
    publisher = _FakePublisher(actions=actions)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    with pytest.raises(RuntimeError, match="simulated DB outage"):
        await listener(_state())

    # And critically: no publish happened. Persist-before-publish is
    # the audit invariant — a failed persist must not leak a ghost
    # event onto the wire.
    assert all(a.kind != "publish" for a in actions)


async def test_publish_failure_is_swallowed_listener_returns_normally() -> None:
    """Publisher swallows its own errors (see RedisEventPublisher);
    the listener mirrors that, so a Redis blip does not abort the
    tick loop."""
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions)
    publisher = _FakePublisher(actions=actions, fail=True)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    await listener(_state())

    # Persist happened, publish was attempted but returned 0 — no raise.
    assert any(a.kind == "persist" for a in actions)


async def test_constructs_one_session_per_invocation() -> None:
    """Each tick opens its own transaction; the listener must not
    re-use connections across ticks (the engine pool would explode)."""
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions)
    publisher = _FakePublisher(actions=actions)
    listener = PersistAndPublishListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
    )

    for tick in range(3):
        await listener(_state(tick_id=tick))

    assert len(factory.spawned) == 3
    # All sessions ran through their context-manager exit cleanly.
    assert all(s._exited for s in factory.spawned)
