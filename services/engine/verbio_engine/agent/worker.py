"""LiveKit Agents worker entrypoint — Phase 1 audio plumbing.

This module is the thin SDK adapter between LiveKit's job framework and
`SessionRuntime`. The split keeps the runtime testable without spinning
up `livekit.rtc`; everything that touches the SDK lives here.

Dispatch model (brief §5.2): the web app calls LiveKit's Agent Dispatch
API with `agent_name="verbio-moderator"` and a JSON metadata blob
containing the `session_id`. Because `agent_name` is set on
`WorkerOptions`, the worker opts out of auto-dispatch — it only spawns
jobs that web explicitly requests. The job's `ctx.room.name` is the
LiveKit room name that the web side persisted in
`sessions.livekit_room_name`, which is how the runtime joins job →
session row.

Permissions are clamped to `can_publish=False`: Phase 1 is observation
only, and we want the LiveKit token to enforce that at the wire so a
bug in our code can never cause audio to leak from the agent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from verbio_engine.agent.runtime import (
    ParticipantSnapshot,
    SessionRuntime,
    UnknownSessionError,
)
from verbio_engine.config import load_settings
from verbio_engine.logging import configure_logging, get_logger
from verbio_engine.persistence import create_engine, create_session_factory

if TYPE_CHECKING:
    from verbio_engine.config import Settings

log = get_logger(__name__)


def _require(value: str | None, key: str) -> str:
    if value is None or value == "":
        msg = f"{key} must be set for the LiveKit moderator agent worker to start"
        raise RuntimeError(msg)
    return value


async def _entrypoint_with_runtime(
    *,
    ctx: object,
    settings: Settings,
) -> None:
    """Real entrypoint, separated from the SDK-typed wrapper for testing.

    `ctx` is a `livekit.agents.JobContext` at runtime; we accept `object`
    to keep this module importable in environments that don't have the
    LiveKit SDK installed (e.g., unit-test runs that mock the worker
    boundary).
    """
    # Local imports so module collection works without livekit-agents
    # installed — the import only happens when the worker actually runs.
    from livekit import rtc

    db_url = _require(
        settings.database_url_engine.get_secret_value() if settings.database_url_engine else None,
        "DATABASE_URL_ENGINE",
    )
    engine = create_engine(db_url, pool_size=4, max_overflow=2)
    factory = create_session_factory(engine)

    room: rtc.Room = ctx.room  # type: ignore[attr-defined]
    runtime = SessionRuntime(session_factory=factory, room_name=room.name)

    # Hold strong references to in-flight tasks so they don't get
    # garbage-collected mid-await; otherwise asyncio cancels them
    # silently (RUF006 / asyncio docs).
    pending: set[asyncio.Task[None]] = set()

    def _spawn(coro: object) -> None:
        task = asyncio.create_task(_safe(coro))
        pending.add(task)
        task.add_done_callback(pending.discard)

    # Register handlers BEFORE awaiting connect; otherwise we miss the
    # initial burst of participant_connected / track_subscribed events
    # for participants who joined before the agent did.
    def _on_participant_connected(participant: rtc.RemoteParticipant) -> None:
        snapshot = ParticipantSnapshot.from_livekit(
            identity=participant.identity,
            display_name=participant.name,
            metadata=participant.metadata,
        )
        _spawn(runtime.on_participant_joined(snapshot))

    def _on_participant_disconnected(participant: rtc.RemoteParticipant) -> None:
        _spawn(runtime.on_participant_left(participant.identity))

    def _on_track_subscribed(
        track: rtc.Track,
        _publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        # L3 will pipe audio frames into VAD + Deepgram here. Phase 1
        # subscribes only — observation without ingestion.
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            log.info(
                "agent.track_subscribed",
                participant_identity=participant.identity,
                track_sid=track.sid,
            )

    room.on("participant_connected", _on_participant_connected)
    room.on("participant_disconnected", _on_participant_disconnected)
    room.on("track_subscribed", _on_track_subscribed)

    # Flush session-end + dispose pool when LiveKit tears the job down.
    async def _shutdown() -> None:
        await _safe(runtime.on_room_disconnected())
        await engine.dispose()

    ctx.add_shutdown_callback(_shutdown)  # type: ignore[attr-defined]

    # `AUDIO_ONLY` subscribes to remote audio tracks and ignores video.
    # Publishing is independently clamped via WorkerPermissions below.
    from livekit.agents import AutoSubscribe

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)  # type: ignore[attr-defined]

    try:
        await runtime.on_room_connected()
    except UnknownSessionError:
        log.error(
            "agent.unknown_session",
            room_name=room.name,
            hint="web must create the sessions row before dispatching the agent",
        )
        await ctx.shutdown()  # type: ignore[attr-defined]
        return

    # The LiveKit framework keeps the task alive until the room ends or
    # the worker is signalled. There's nothing more for Phase 1 to do
    # synchronously here — Phase 2 wires the tick loop and Phase 3
    # introduces decision orchestration.
    log.info("agent.ready", room_name=room.name, session_id=str(runtime.session_id))


async def _safe(coro: object) -> None:
    """Awaitable wrapper that logs (and swallows) exceptions.

    LiveKit's event callbacks are sync; we schedule async work via
    `asyncio.create_task`. An unhandled exception in a fire-and-forget
    task only surfaces as a warning at the event loop level — wrap them
    so the audit log captures failures.
    """
    try:
        await coro  # type: ignore[misc]
    except Exception as exc:
        # Log-and-swallow at the fire-and-forget task boundary. Letting
        # the exception escape only surfaces as a generic asyncio
        # warning; capturing it preserves the audit trail.
        log.exception("agent.callback_failed", error=str(exc))


def run_worker() -> None:
    """CLI entrypoint — `verbio-engine-agent`.

    Boot a long-lived LiveKit Agents worker that registers under
    `agent_name="verbio-moderator"`. The worker stays idle until web
    explicitly dispatches a job to a room.
    """
    settings = load_settings()
    configure_logging(level=settings.log_level, json_format=settings.is_production)

    # Validate LiveKit credentials at boot — the SDK's own error if
    # missing is a generic AssertionError deep in the worker, which is
    # painful to diagnose.
    _require(settings.livekit_url, "LIVEKIT_URL")
    _require(
        settings.livekit_api_key.get_secret_value() if settings.livekit_api_key else None,
        "LIVEKIT_API_KEY",
    )
    _require(
        settings.livekit_api_secret.get_secret_value() if settings.livekit_api_secret else None,
        "LIVEKIT_API_SECRET",
    )

    # Imports here, not at module top-level, so `verbio_engine.agent.run_worker`
    # only requires `livekit-agents` to be installed when the worker
    # actually starts. Unit tests can import this module to validate
    # `_entrypoint_with_runtime` shape without the SDK present.
    from livekit.agents import (
        JobContext,
        WorkerOptions,
        WorkerPermissions,
        cli,
    )

    async def entrypoint(ctx: JobContext) -> None:
        await _entrypoint_with_runtime(ctx=ctx, settings=settings)

    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            # Setting agent_name opts out of LiveKit's auto-dispatch.
            # The worker only spawns jobs that web's AgentDispatch call
            # explicitly requests for this name.
            agent_name="verbio-moderator",
            # Belt-and-braces: even if our code tries to publish audio
            # by accident, the token won't allow it. Phase 4 flips this
            # back to True (or mints a separate token) when the
            # moderator speaks.
            permissions=WorkerPermissions(
                can_publish=False,
                can_subscribe=True,
                can_publish_data=True,
                hidden=False,
            ),
        )
    )
