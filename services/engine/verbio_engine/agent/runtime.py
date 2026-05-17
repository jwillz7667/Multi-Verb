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
from typing import TYPE_CHECKING, Any, Literal, cast

from verbio_engine.decisions import DecisionTickListener, ExecutionDispatcher
from verbio_engine.domain.rules_config import RulesConfig
from verbio_engine.domain.study import SessionConfigSnapshot
from verbio_engine.embeddings import EmbeddingCoordinator
from verbio_engine.logging import get_logger
from verbio_engine.persistence import (
    ParticipantJoin,
    ParticipantRepo,
    SessionRepo,
    StudyRepo,
    UtteranceRepo,
)
from verbio_engine.realtime import (
    EventPublisher,
    NullEventPublisher,
    utterance_event,
)
from verbio_engine.rules import V1_RULES_VERSION
from verbio_engine.state import (
    PersistAndPublishListener,
    StateStore,
    StateStoreConfig,
)
from verbio_engine.state.events import (
    ParticipantJoinEvent,
    ParticipantLeaveEvent,
    UtteranceFinalEvent,
)
from verbio_engine.tick_loop import Clock, TickLoop, WallClock

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.commands import CommandBus
    from verbio_engine.domain.session_state import SessionState
    from verbio_engine.embeddings import EmbeddingProvider
    from verbio_engine.persistence import Participant, Session, UtteranceInsert
    from verbio_engine.rules import RulesRegistry
    from verbio_engine.state.events import StateEvent

log = get_logger(__name__)


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
        publisher: EventPublisher | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        embedding_rolling_window_sec: float = 30.0,
        rules_registry: RulesRegistry | None = None,
        rules_registry_factory: Callable[[RulesConfig], RulesRegistry] | None = None,
        executor_dispatcher: ExecutionDispatcher | None = None,
        command_bus: CommandBus | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._room_name = room_name
        self._session_id: uuid.UUID | None = None
        # Phase 3 L10: when a registry is wired in the tick listener
        # runs the resolver and persists a decisions/rule_evaluations
        # batch each tick. Worker code injects the v1 registry; tests
        # leave it None when they're not exercising the decisions path.
        # Phase 3 L11: `rules_registry_factory` is the auto-build path —
        # when set AND no explicit registry was injected AND the session
        # has a study attached, the runtime builds a per-session registry
        # from the study's snapshotted RulesConfig at `on_room_connected`.
        # An explicit `rules_registry` always wins to keep test ergonomics.
        self._rules_registry: RulesRegistry | None = rules_registry
        self._rules_registry_factory: Callable[[RulesConfig], RulesRegistry] | None = (
            rules_registry_factory
        )
        # Phase 4 L8: optional mouth/TTS/publisher pipeline. When wired,
        # `DecisionTickListener` hands every non-silent auto decision to
        # the dispatcher, which spawns the executor as a background task
        # so the tick loop stays unblocked (brief §6 step 6). Worker code
        # wires this once the LiveKit room is up and the audio publisher
        # has joined the moderator track. Leaving it None keeps the
        # session in shadow mode — decisions persist but no audio plays.
        self._executor_dispatcher: ExecutionDispatcher | None = executor_dispatcher
        # P5 L2: optional Redis command bus. When wired the
        # `DecisionTickListener` drains it at the top of every tick and
        # persists each command to `researcher_actions`. Left None for
        # tests and for shadow-mode environments where the web service
        # isn't yet sending researcher commands.
        self._command_bus: CommandBus | None = command_bus
        # Optional embedding pipeline (Phase 3 L7). When `provider` is
        # None the runtime simply doesn't embed anything — topic_drift
        # will read `None` for both vectors and stay silent. The
        # coordinator is constructed lazily after `start_tick_loop`
        # spins up the state store (the coordinator needs both).
        self._embedding_provider: EmbeddingProvider | None = embedding_provider
        self._embedding_window_sec = embedding_rolling_window_sec
        self._embedding_coordinator: EmbeddingCoordinator | None = None
        # Buffered until `start_tick_loop`: a worker can seed the prompt
        # during `on_room_connected` (before the state store exists).
        self._pending_study_prompt: str | None = None
        # Holds references to fire-and-forget embed tasks so the event
        # loop's weak-ref tracking doesn't GC them mid-flight (RUF006).
        self._bg_embed_tasks: set[asyncio.Task[None]] = set()
        # `livekit_identity → participant_id`. Populated by
        # `on_participant_joined`; the audio pipeline consults this to
        # tag each utterance with the correct DB participant row.
        self._participant_ids: dict[str, uuid.UUID] = {}
        # Reverse lookup (participant_id → snapshot) so the persist
        # path can enrich the SSE event with the joiner's identity and
        # display name without an extra DB roundtrip per utterance.
        self._snapshot_by_pid: dict[uuid.UUID, ParticipantSnapshot] = {}
        # Per-identity asyncio.Event so a `track_subscribed` race that
        # fires before `participant_connected`'s upsert completes can
        # `await runtime.await_participant_id(identity)` instead of
        # busy-polling.
        self._participant_events: dict[str, asyncio.Event] = {}
        # Defaulting to NullEventPublisher keeps unit tests trivial —
        # they don't have to construct a Redis double when they don't
        # care about realtime fan-out. Worker code injects the real one.
        self._publisher: EventPublisher = publisher or NullEventPublisher()
        # Tick loop (Phase 2 L4): the worker calls `start_tick_loop`
        # after `on_room_connected` and `stop_tick_loop` during the
        # shutdown callback. The store is event-sourced — every join /
        # leave / utterance-final routes through `_record_state_event`
        # so the snapshot the dashboard sees matches DB reality.
        self._state_store: StateStore | None = None
        self._tick_loop: TickLoop | None = None
        self._tick_task: asyncio.Task[None] | None = None

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

    def _identity_to_db_uuid(self, identity: str) -> uuid.UUID | None:
        """Resolve a rule-emitted participant id → DB `participants.id` UUID.

        Used by `DecisionTickListener` to translate the string carried on
        `ModeratorDecision.target_participant_id` into a typed UUID. The
        rules engine receives whatever the state store puts into
        `state.participants` keys — today the runtime keys those by the
        stringified DB UUID, but a future refactor may switch to LiveKit
        identities. We accept either form for resilience:

          * Parses as UUID + matches a known participant → return typed.
          * Else look up as a LiveKit identity in the in-memory cache.

        Either path returning `None` means the runtime can't vouch for
        the id (purge race or rule emitted something unknown); the
        listener then writes `target_participant_id=NULL` and the audit
        trail keeps the moderator's intent without the FK link.
        """
        try:
            as_uuid = uuid.UUID(identity)
        except ValueError:
            as_uuid = None
        if as_uuid is not None and as_uuid in self._snapshot_by_pid:
            return as_uuid
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
        """Resolve the Session row, mark it `live`, and freeze its config.

        Phase 3 L11: this is where the study's `RulesConfig` is captured
        into `sessions.config_snapshot` (brief §7.5 — rule versioning is
        sacred). The snapshot is written exactly once per session — on
        the first start — so a worker restart preserves the original
        configuration even if the study has been edited in the meantime.

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
            await self._snapshot_session_config(db, row)
            return row

    async def _snapshot_session_config(self, db: AsyncSession, row: Session) -> None:
        """Freeze the study's config into `sessions.config_snapshot`.

        Idempotent: on a re-entry (worker restart) the existing snapshot
        is preserved verbatim and only the in-memory registry is
        rebuilt from it. The brief is explicit that historical sessions
        must keep their original configuration even if the study is
        later edited (§7.5).
        """
        existing = row.config_snapshot or {}
        if existing:
            self._maybe_build_registry_from_snapshot(existing)
            return

        snapshot = await self._build_config_snapshot(db, row)
        row.config_snapshot = snapshot.model_dump(mode="json")
        await db.flush([row])
        self._maybe_build_registry_from_snapshot(row.config_snapshot)
        log.info(
            "runtime.config_snapshot_frozen",
            session_id=str(row.id),
            study_id=str(row.study_id) if row.study_id else None,
            rules_version=snapshot.rules_config.rules_version,
        )

    async def _build_config_snapshot(
        self,
        db: AsyncSession,
        row: Session,
    ) -> SessionConfigSnapshot:
        """Construct the snapshot from the attached study, or v1 defaults."""
        if row.study_id is None:
            # Standalone session (Phase 1-2 path, plus tests that don't
            # seed a study). Snapshot the defaults so the audit trail
            # carries *something* — replay can still reconstruct a
            # default registry from this.
            return SessionConfigSnapshot(
                rules_config=RulesConfig(rules_version=V1_RULES_VERSION),
            )

        study_repo = StudyRepo(db)
        study = await study_repo.get_by_id(row.study_id)
        if study is None:
            # Dangling FK — study was deleted between session schedule
            # and start. Log loudly and fall back to defaults so the
            # session still runs in shadow mode rather than crashing.
            log.warning(
                "runtime.study_missing_for_session",
                session_id=str(row.id),
                study_id=str(row.study_id),
            )
            return SessionConfigSnapshot(
                rules_config=RulesConfig(rules_version=V1_RULES_VERSION),
            )

        # JSONB columns are typed as `dict[str, object]` on the ORM but
        # `RulesConfig.rules` requires `dict[str, dict[str, Any]]`. Cast
        # at the boundary; Pydantic re-validates the shape on the next line.
        rules_config = RulesConfig(
            rules_version=study.rules_version,
            rules=cast("dict[str, dict[str, Any]]", dict(study.rules_config)),
        )
        return SessionConfigSnapshot(
            rules_config=rules_config,
            moderator_persona=cast("dict[str, Any]", dict(study.moderator_persona)),
            retention_policy=cast("dict[str, Any]", dict(study.retention_policy)),
        )

    def _maybe_build_registry_from_snapshot(self, snapshot_data: dict[str, object]) -> None:
        """Build the per-session registry from a config snapshot dict.

        Skipped when an explicit `rules_registry` was injected (tests)
        or when no `rules_registry_factory` was wired (the runtime
        opts out of decisions entirely).
        """
        if self._rules_registry is not None or self._rules_registry_factory is None:
            return
        snapshot = SessionConfigSnapshot.model_validate(snapshot_data)
        self._rules_registry = self._rules_registry_factory(snapshot.rules_config)

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
        self._snapshot_by_pid[row.id] = snapshot
        # Wake any track callback that's already awaiting this identity.
        self._participant_events.setdefault(snapshot.identity, asyncio.Event()).set()
        # State store sees real participants only — the moderator is
        # the agent itself, not a session member from the rules' POV.
        if snapshot.role != "moderator":
            self._record_state_event(
                ParticipantJoinEvent(
                    ts=self._clock_now(),
                    participant_id=str(row.id),
                    display_name=snapshot.display_name,
                )
            )
        return row

    async def on_participant_left(self, identity: str) -> None:
        """Stamp `left_at` for a participant disconnect."""
        async with self._session_factory() as db, db.begin():
            repo = ParticipantRepo(db)
            await repo.mark_left(self.session_id, identity)
        pid = self._participant_ids.get(identity)
        if pid is not None:
            self._record_state_event(
                ParticipantLeaveEvent(ts=self._clock_now(), participant_id=str(pid)),
            )

    async def persist_utterance(self, record: UtteranceInsert) -> None:
        """Write one STT-derived utterance row, then fan out to Redis.

        Persistence-before-publish is intentional: the brief's audit
        invariant is that no spoken artifact may exist without a
        Postgres row, so the publish happens only after the row id is
        in hand. The publish is best-effort — failure is logged inside
        `EventPublisher.publish` and never propagates here.

        Each insert is its own short transaction so a slow write can't
        backpressure the STT stream — the pool absorbs the concurrency
        (4 connections per worker is the current ceiling; see worker.py).
        """
        async with self._session_factory() as db, db.begin():
            repo = UtteranceRepo(db)
            row = await repo.insert(record)

        snapshot = self._snapshot_by_pid.get(record.participant_id)
        if snapshot is None:
            # The participant row exists (the insert FK would have failed
            # otherwise) but the in-memory snapshot is missing — likely
            # a worker restart mid-session. Skip the publish; the SSE
            # backfill will surface the row on the next reconnect.
            log.warning(
                "runtime.publish_skipped_no_snapshot",
                participant_id=str(record.participant_id),
                utterance_id=str(row.id),
            )
            return

        await self._publisher.publish(
            utterance_event(
                utterance_id=row.id,
                session_id=record.session_id,
                participant_id=record.participant_id,
                participant_identity=snapshot.identity,
                participant_display_name=snapshot.display_name,
                text=record.text,
                is_final=record.is_final,
                confidence=record.confidence,
                start_ts=record.start_ts,
                end_ts=record.end_ts,
            )
        )

        # Interim STT results drive interactivity but not state math —
        # only finals feed the rolling speaking-time accounting that the
        # dashboard tiles read.
        if record.is_final:
            self._record_state_event(
                UtteranceFinalEvent(
                    ts=self._clock_now(),
                    utterance_id=str(row.id),
                    participant_id=str(record.participant_id),
                    text=record.text,
                    start_ts=record.start_ts,
                    end_ts=record.end_ts,
                    confidence=record.confidence,
                )
            )
            # Rolling transcript embedding lags the event stream by one
            # async round-trip; this request is single-flight inside the
            # coordinator so a burst of finals collapses into at most two
            # provider calls.
            if self._embedding_coordinator is not None:
                self._embedding_coordinator.request_rolling_re_embed()

    # ---------------------------------------------------------- Tick loop

    def start_tick_loop(
        self,
        *,
        started_at: datetime,
        clock: Clock | None = None,
        interval_sec: float = 0.5,
        scheduled_end_at: datetime | None = None,
        state_config: StateStoreConfig | None = None,
    ) -> asyncio.Task[None]:
        """Spawn the 2 Hz tick loop for this session.

        Constructs a `StateStore` rooted at `started_at` (the session
        row's `actual_start`), a `PersistAndPublishListener` that writes
        each snapshot to Postgres and publishes it on Redis, and a
        `TickLoop` that drives them.

        Must be called after `on_room_connected` so `self.session_id` is
        resolved. Idempotent: a second call returns the existing task.
        """
        if self._tick_task is not None:
            return self._tick_task
        self._state_store = StateStore(
            session_id=self.session_id,
            started_at=started_at,
            scheduled_end_at=scheduled_end_at,
            config=state_config,
        )
        snapshot_listener = PersistAndPublishListener(
            session_factory=self._session_factory,
            publisher=self._publisher,
        )
        decision_listener: DecisionTickListener | None = None
        if self._rules_registry is not None:
            decision_listener = DecisionTickListener(
                session_factory=self._session_factory,
                publisher=self._publisher,
                rules=self._rules_registry,
                identity_resolver=self._identity_to_db_uuid,
                command_bus=self._command_bus,
                executor_dispatcher=self._executor_dispatcher,
                # P5 L5: the runtime is the RuntimeControl provider —
                # it owns the state store (mute/pause flips) and the
                # tick loop (end_session stop). Pre-wired only when
                # the command bus is too; without a bus there's no
                # path for control commands to arrive, so registering
                # the control surface buys nothing.
                runtime_control=self if self._command_bus is not None else None,
            )
        listener = _compose_tick_listeners(snapshot_listener, decision_listener)
        self._tick_loop = TickLoop(
            store=self._state_store,
            clock=clock or WallClock(),
            interval_sec=interval_sec,
            listener=listener,
            scheduled_end_at=scheduled_end_at,
        )
        if self._embedding_provider is not None:
            self._embedding_coordinator = EmbeddingCoordinator(
                provider=self._embedding_provider,
                store=self._state_store,
                rolling_window_sec=self._embedding_window_sec,
            )
            # Drain any prompt the worker seeded before the store existed.
            if self._pending_study_prompt is not None:
                buffered = self._pending_study_prompt
                self._pending_study_prompt = None
                # Schedule on the running loop; embed_study_prompt awaits
                # the provider, which we don't want to do synchronously
                # inside start_tick_loop.
                self._spawn_bg_embed(self._embedding_coordinator.embed_study_prompt(buffered))
        self._tick_task = asyncio.create_task(self._tick_loop.run())
        log.info(
            "runtime.tick_loop_started",
            session_id=str(self.session_id),
            interval_sec=interval_sec,
        )
        return self._tick_task

    def seed_study_prompt(self, prompt: str) -> None:
        """Hand the study prompt to the embedding pipeline.

        Sync / non-blocking. Called once by the worker after the session
        row is loaded (the prompt comes from the study FK, which Phase 3
        L9 wires up). Behaviour:

        * No embedding provider configured → no-op (topic_drift stays
          silent, which is the intended degraded state).
        * Coordinator already built (called after `start_tick_loop`) →
          spawn the embed task immediately.
        * Coordinator not yet built (called before `start_tick_loop`) →
          buffer the prompt; `start_tick_loop` drains it.

        Re-seeding mid-session is supported (a researcher rewrite of the
        prompt would re-call this), though no UI exposes that today.
        """
        if self._embedding_provider is None:
            return
        if self._embedding_coordinator is None:
            self._pending_study_prompt = prompt
            return
        self._spawn_bg_embed(self._embedding_coordinator.embed_study_prompt(prompt))

    def _spawn_bg_embed(self, coro: Coroutine[object, object, None]) -> None:
        """Spawn a fire-and-forget embed task, holding a ref so it isn't GC'd."""
        task = asyncio.create_task(coro)
        self._bg_embed_tasks.add(task)
        task.add_done_callback(self._bg_embed_tasks.discard)

    async def stop_tick_loop(self, *, timeout: float = 5.0) -> None:  # noqa: ASYNC109
        """Request graceful shutdown of the tick loop and await it.

        Safe to call when the loop was never started — no-op in that
        case. Called from the worker's shutdown callback before the
        session is marked ended so the final snapshot still lands.
        """
        if self._tick_loop is None or self._tick_task is None:
            # Even when the tick loop never started, an embedding
            # coordinator could exist if `start_tick_loop` was called
            # then aborted; defensively close it if so.
            if self._embedding_coordinator is not None:
                await self._embedding_coordinator.aclose(timeout=timeout)
                self._embedding_coordinator = None
            return
        self._tick_loop.stop()
        try:
            await asyncio.wait_for(self._tick_task, timeout=timeout)
        except TimeoutError:
            log.warning(
                "runtime.tick_loop_shutdown_timeout",
                session_id=str(self.session_id),
                timeout=timeout,
            )
            self._tick_task.cancel()
        finally:
            stats = self._tick_loop.stats
            log.info(
                "runtime.tick_loop_stopped",
                session_id=str(self.session_id),
                ticks_completed=stats.ticks_completed,
                ticks_overrun=stats.ticks_overrun,
                listener_failures=stats.listener_failures,
            )
            self._tick_loop = None
            self._tick_task = None
            if self._embedding_coordinator is not None:
                await self._embedding_coordinator.aclose(timeout=timeout)
                self._embedding_coordinator = None
            # P4 L8: drain any in-flight executor tasks before returning.
            # The tick loop is already stopped, so no NEW dispatches will
            # land, but a slow mouth/TTS pipeline may still be writing to
            # the decisions row when the worker calls us — finish those
            # writes before the session_factory is torn down or we leak
            # `was_executed=False` rows for utterances that did play.
            if self._executor_dispatcher is not None:
                await self._executor_dispatcher.aclose()

    def _record_state_event(self, event: StateEvent) -> None:
        """Forward a state event to the store, if the tick loop is running."""
        if self._state_store is None:
            return
        self._state_store.record(event)

    def _clock_now(self) -> datetime:
        """Wall-clock now used for event stamping.

        Wraps `datetime.now(UTC)` so tests can monkeypatch in a fake.
        We deliberately don't reach into the tick loop's `Clock` — state
        events arrive on the wall, and a faked tick clock is for ticking
        cadence, not event timestamping.
        """
        from datetime import UTC, datetime  # local import keeps top-level lean

        return datetime.now(UTC)

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

    # ----- RuntimeControl surface (P5 L5) ------------------------------

    def set_muted(self, *, muted: bool) -> None:
        """Flip `SessionState.moderator_muted` via the state store.

        No-op when the tick loop has not been started yet (store is
        None) — `start_tick_loop` would not see the toggle anyway, and
        researchers cannot issue commands before the bus is wired. The
        no-op keeps the API safe to call from any lifecycle point.
        """
        if self._state_store is None:
            return
        self._state_store.set_moderator_muted(muted=muted)

    def set_pause(self, *, paused: bool) -> None:
        """Flip `SessionState.is_paused` via the state store.

        Same no-op-before-start guard as `set_muted`. The brief's
        "tick loop still runs but moderator is suppressed from
        speaking" semantics live entirely in the dispatch gate; the
        loop itself does not check this flag.
        """
        if self._state_store is None:
            return
        self._state_store.set_pause(paused=paused)

    async def request_end_session(self, *, reason: str | None) -> None:
        """Mark the session ended in Postgres and stop the tick loop.

        Called from the decision listener when an `end_session` command
        is processed. The DB write happens first so the audit trail
        records the end timestamp regardless of how the worker tears
        down. The tick loop stop is a synchronous flag flip; the loop's
        outer `while not stop_event` check exits on the next iteration
        (or sooner — `_race` listens for the stop event during sleep).

        `reason` is informational only — it's already persisted on the
        matching `researcher_actions` row by the listener's audit
        write, and the runtime has no field of its own to store it.
        Threaded through anyway so a future log line can carry it.

        Idempotent: re-entry after the session is already `ended` or
        after the tick loop has already been stopped is a no-op.
        """
        if self._session_id is not None:
            async with self._session_factory() as db, db.begin():
                sess_repo = SessionRepo(db)
                row = await sess_repo.get_by_room_name(self._room_name)
                if row is not None:
                    await sess_repo.mark_ended(row)
        if self._tick_loop is not None:
            self._tick_loop.stop()
        log.info(
            "runtime.end_session_requested",
            session_id=str(self._session_id) if self._session_id else None,
            reason=reason,
        )


def _compose_tick_listeners(
    snapshot_listener: PersistAndPublishListener,
    decision_listener: DecisionTickListener | None,
) -> Callable[[SessionState], Coroutine[object, object, None]]:
    """Build the single `SnapshotListener` the tick loop drives.

    Always runs the snapshot listener (state_snapshots row + SSE
    envelope). When a `DecisionTickListener` is wired, runs it
    afterwards in the same tick.

    Sequential execution is deliberate. The snapshot row is the
    upstream input to the decision audit log (researchers reading the
    log expect the matching snapshot to be queryable), so on a
    snapshot-persist failure we deliberately skip the decision write
    too — better to drop a whole tick consistently than to leave a
    decision dangling against a missing snapshot row. The tick loop's
    own try/except already counts the failure into `listener_failures`.
    """
    if decision_listener is None:
        return snapshot_listener.__call__

    async def composite(state: SessionState) -> None:
        await snapshot_listener(state)
        await decision_listener(state)

    return composite
