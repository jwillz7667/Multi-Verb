"""Transport-agnostic per-session orchestrator.

`SessionRuntime` owns the persistence transitions the moderator agent
drives when it joins a LiveKit room: session start/end, participant
join/leave. It is deliberately independent of `livekit.rtc` so it can
be unit-tested without spinning up a real room (`agent.worker` is the
LiveKit-facing glue that calls into this).

Why a separate object instead of inlining the logic in the SDK
callbacks: the brief mandates an audit trail of every state change. If
the persistence logic lives only inside `room.on("...")` lambdas, it
escapes test coverage and the room object becomes a god-object. The
runtime concentrates the audit-relevant transitions; the worker is then
a thin adapter.

Each `SessionRuntime` instance is bound to **one** session (one room).
The agent worker spawns one per dispatched job.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from verbio_engine.persistence import (
    ParticipantJoin,
    ParticipantRepo,
    SessionRepo,
    UtteranceRepo,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.persistence import Participant, Session, UtteranceInsert


# Mirrors `ParticipantRole` in persistence.models. We re-state the
# literal here so the runtime's public surface doesn't bleed SQLAlchemy
# types into call-sites.
RemoteRole = Literal["participant", "researcher", "moderator"]


@dataclass(frozen=True, slots=True)
class ParticipantSnapshot:
    """Minimal view of a LiveKit participant the runtime needs.

    Decouples the runtime from `livekit.rtc.RemoteParticipant` so unit
    tests can construct one trivially. `worker.py` builds this from the
    real SDK object.
    """

    identity: str
    display_name: str
    role: RemoteRole
    metadata_raw: str | None = None

    @classmethod
    def from_livekit(
        cls,
        *,
        identity: str,
        display_name: str,
        metadata: str | None,
    ) -> ParticipantSnapshot:
        """Construct from raw LiveKit fields, parsing role out of metadata.

        Web mints participant tokens with a JSON metadata payload
        containing `{"role": "participant" | "researcher"}`. Falls back
        to "participant" if metadata is missing or malformed — the more
        privileged "researcher" role should never be granted by default.
        """
        return cls(
            identity=identity,
            display_name=display_name or identity,
            role=_parse_role(metadata),
            metadata_raw=metadata,
        )


def _parse_role(metadata: str | None) -> RemoteRole:
    if not metadata:
        return "participant"
    try:
        parsed = json.loads(metadata)
    except json.JSONDecodeError:
        return "participant"
    role = parsed.get("role") if isinstance(parsed, dict) else None
    if role in ("participant", "researcher", "moderator"):
        return cast("RemoteRole", role)
    return "participant"


class UnknownSessionError(LookupError):
    """Raised when no `sessions` row matches the LiveKit room name.

    The web app must create the session row before dispatching the
    agent. If this fires, the agent is being dispatched without a
    matching session — disconnect and log; do not silently create.
    """


class SessionRuntime:
    """Per-session orchestrator. One instance per dispatched job."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        room_name: str,
    ) -> None:
        self._session_factory = session_factory
        self._room_name = room_name
        self._session_id: uuid.UUID | None = None
        # `livekit_identity → participant_id`. Populated by
        # `on_participant_joined`; the audio pipeline consults this to
        # tag each utterance with the correct DB participant row.
        self._participant_ids: dict[str, uuid.UUID] = {}
        # Per-identity asyncio.Event so a `track_subscribed` race that
        # fires before `participant_connected`'s upsert completes can
        # `await runtime.await_participant_id(identity)` instead of
        # busy-polling.
        self._participant_events: dict[str, asyncio.Event] = {}

    @property
    def session_id(self) -> uuid.UUID:
        """Session UUID resolved from the room name; valid after `on_room_connected`."""
        if self._session_id is None:
            msg = "SessionRuntime.session_id accessed before on_room_connected()"
            raise RuntimeError(msg)
        return self._session_id

    def participant_id_for(self, identity: str) -> uuid.UUID | None:
        """Sync lookup: identity → DB participant_id, or None if not yet joined."""
        return self._participant_ids.get(identity)

    async def await_participant_id(
        self,
        identity: str,
        *,
        timeout: float = 10.0,  # noqa: ASYNC109
    ) -> uuid.UUID:
        """Block until the participant row exists for `identity`.

        Resolves the `track_subscribed` vs `participant_connected` race
        — LiveKit can deliver both events back-to-back, and the join
        upsert is async, so the track callback might find an empty
        cache. Wait on the per-identity Event instead of polling.

        The `timeout` parameter is intentional API ergonomics (callers
        pass a number, not an `asyncio.timeout` context); ASYNC109 is
        suppressed because wrapping at every call-site would be noisier
        than the internal `asyncio.wait_for` we already use.

        Raises:
            asyncio.TimeoutError: participant didn't appear within `timeout`.
        """
        if (pid := self._participant_ids.get(identity)) is not None:
            return pid
        event = self._participant_events.setdefault(identity, asyncio.Event())
        await asyncio.wait_for(event.wait(), timeout=timeout)
        # Event fires only after _participant_ids is populated.
        return self._participant_ids[identity]

    async def on_room_connected(self) -> Session:
        """Resolve the Session row and mark it `live`.

        Raises:
            UnknownSessionError: no row matches `self._room_name`.
        """
        async with self._session_factory() as db, db.begin():
            sess_repo = SessionRepo(db)
            row = await sess_repo.get_by_room_name(self._room_name)
            if row is None:
                raise UnknownSessionError(self._room_name)
            await sess_repo.mark_started(row)
            self._session_id = row.id
            return row

    async def on_participant_joined(self, snapshot: ParticipantSnapshot) -> Participant:
        """Upsert the participant row for a connect/reconnect event."""
        async with self._session_factory() as db, db.begin():
            repo = ParticipantRepo(db)
            row = await repo.upsert_on_join(
                ParticipantJoin(
                    session_id=self.session_id,
                    livekit_identity=snapshot.identity,
                    display_name=snapshot.display_name,
                    role=snapshot.role,
                )
            )
        self._participant_ids[snapshot.identity] = row.id
        # Wake any track callback that's already awaiting this identity.
        self._participant_events.setdefault(snapshot.identity, asyncio.Event()).set()
        return row

    async def on_participant_left(self, identity: str) -> None:
        """Stamp `left_at` for a participant disconnect."""
        async with self._session_factory() as db, db.begin():
            repo = ParticipantRepo(db)
            await repo.mark_left(self.session_id, identity)

    async def persist_utterance(self, record: UtteranceInsert) -> None:
        """Write one STT-derived utterance row.

        Called from the per-track transcriber tasks. Each insert is its
        own short transaction so a slow write can't backpressure the
        STT stream — the pool absorbs the concurrency (4 connections
        per worker is the current ceiling; see worker.py).
        """
        async with self._session_factory() as db, db.begin():
            repo = UtteranceRepo(db)
            await repo.insert(record)

    async def on_room_disconnected(self) -> None:
        """Mark the session `ended` at room teardown."""
        if self._session_id is None:
            # Never connected (e.g. UnknownSessionError on join). Nothing
            # to mark ended.
            return
        async with self._session_factory() as db, db.begin():
            sess_repo = SessionRepo(db)
            row = await sess_repo.get_by_room_name(self._room_name)
            if row is None:
                return
            await sess_repo.mark_ended(row)
