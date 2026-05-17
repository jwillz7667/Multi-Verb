"""LiveKit egress dispatcher — start composite + per-participant recordings.

This module is the engine's only caller of LiveKit's egress API. The
brief (§14 Phase 6, §6 recording egress) requires:

  1. One room-composite egress per session, audio+video mixed, written
     to R2 as `sessions/<uuid>/composite.mp4`.
  2. One per-participant audio egress per joined participant, written
     to R2 as `sessions/<uuid>/tracks/<identity>.ogg`.

LiveKit Cloud writes server-side directly to R2 — the engine doesn't
proxy bytes. We just hand the S3 credentials to LiveKit at start time
and trust the egress service to upload. Completion arrives at the web
service as a webhook (P6 L3); the engine sees only the synchronous
`EgressInfo` response that confirms the egress was accepted.

Design notes
------------

* **Protocol-based dependency.** `EgressDispatcher` is a structural type
  so the runtime can accept either the live LiveKit-backed impl or a
  `NullEgressDispatcher` no-op without branching on type. Matches
  `CommandBus` / `EventPublisher`.

* **Never raise into the runtime.** Egress is a recording concern, not
  a session-lifecycle blocker — a failed start logs and returns None.
  The session keeps running, decisions still persist, replay UI just
  shows "no recording available." A future hardening pass can add retry
  + alerting, but the brief's audit invariant is unrelated to whether
  the audio file lands.

* **Idempotency at the dispatcher boundary.** The runtime calls
  `start_room_composite` once at `on_room_connected`. A second call
  (worker restart mid-session) would start a duplicate egress and waste
  upload bandwidth; we de-dup in-process via a `started_sessions` set.
  LiveKit itself does not de-dup — that's our job.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from verbio_engine.logging import get_logger
from verbio_engine.recordings.keys import composite_audio_key, participant_audio_key

if TYPE_CHECKING:
    import uuid
    from collections.abc import Awaitable, Callable

    from livekit.api import (
        LiveKitAPI,
        ParticipantEgressRequest,
        RoomCompositeEgressRequest,
    )

    from verbio_engine.recordings.config import R2EgressConfig

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class EgressHandle:
    """Synchronous outcome of starting one egress.

    `egress_id` is LiveKit's identifier, surfaced in webhook payloads so
    the web side can correlate completion back to the start. `r2_key`
    is the bucket key the egress is uploading to — persisted on the
    session row so replay can mint signed URLs without re-asking
    LiveKit.
    """

    egress_id: str
    r2_key: str


@runtime_checkable
class EgressDispatcher(Protocol):
    """Structural type the runtime depends on."""

    async def start_room_composite(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
    ) -> EgressHandle | None: ...

    async def start_participant_audio(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
        identity: str,
    ) -> EgressHandle | None: ...

    async def aclose(self) -> None: ...


class LiveKitEgressDispatcher:
    """Concrete dispatcher backed by `livekit.api.LiveKitAPI`.

    Holds one `LiveKitAPI` instance for the lifetime of the worker
    (matches the pattern of one `RedisEventPublisher` per worker, not
    per session — the aiohttp session pools across calls). `aclose()`
    tears the API client down at worker shutdown.

    The optional `api_factory` indirection exists so tests can inject a
    fake that records arguments and returns canned `EgressInfo`
    instances; production wiring constructs the real `LiveKitAPI` from
    settings.
    """

    __slots__ = ("_api", "_api_factory", "_lock", "_r2", "_started_sessions")

    def __init__(
        self,
        *,
        r2: R2EgressConfig,
        api_factory: Callable[[], LiveKitAPI],
    ) -> None:
        self._r2 = r2
        self._api_factory = api_factory
        # Lazy: the API client opens its aiohttp session on first call,
        # so unconfigured environments don't pay the connection cost.
        self._api: LiveKitAPI | None = None
        self._started_sessions: set[uuid.UUID] = set()
        # Guards `_api` initialization against a concurrent first-call
        # race between composite + participant egress starts.
        self._lock = asyncio.Lock()

    async def start_room_composite(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
    ) -> EgressHandle | None:
        """Start a composite egress for the room, writing to R2.

        Idempotent per `session_id` — a second call for the same session
        is a no-op, returning None. The first call's handle is not
        cached; the runtime persists the egress_id itself on the
        sessions row.
        """
        if session_id in self._started_sessions:
            log.debug(
                "egress.composite_already_started",
                session_id=str(session_id),
                room_name=room_name,
            )
            return None
        self._started_sessions.add(session_id)

        key = composite_audio_key(session_id)
        request = _build_composite_request(
            room_name=room_name,
            r2=self._r2,
            filepath=key,
        )
        try:
            api = await self._ensure_api()
            info = await api.egress.start_room_composite_egress(request)
        except Exception as exc:
            # Roll back the dedup guard so a retry has a chance to land
            # (e.g., transient LiveKit-side network blip during startup).
            self._started_sessions.discard(session_id)
            log.warning(
                "egress.composite_start_failed",
                session_id=str(session_id),
                room_name=room_name,
                error=str(exc),
            )
            return None

        handle = EgressHandle(egress_id=info.egress_id, r2_key=key)
        log.info(
            "egress.composite_started",
            session_id=str(session_id),
            room_name=room_name,
            egress_id=handle.egress_id,
            r2_key=key,
        )
        return handle

    async def start_participant_audio(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
        identity: str,
    ) -> EgressHandle | None:
        """Start a per-participant audio egress, writing to R2.

        Called from `on_participant_joined` for every non-moderator
        participant. LiveKit's participant egress will wait for the
        identity to publish a track, so calling at join time (before
        any track is up) is safe — no race against `track_subscribed`.
        """
        key = participant_audio_key(session_id, identity)
        request = _build_participant_request(
            room_name=room_name,
            identity=identity,
            r2=self._r2,
            filepath=key,
        )
        try:
            api = await self._ensure_api()
            info = await api.egress.start_participant_egress(request)
        except Exception as exc:
            log.warning(
                "egress.participant_start_failed",
                session_id=str(session_id),
                room_name=room_name,
                identity=identity,
                error=str(exc),
            )
            return None

        handle = EgressHandle(egress_id=info.egress_id, r2_key=key)
        log.info(
            "egress.participant_started",
            session_id=str(session_id),
            room_name=room_name,
            identity=identity,
            egress_id=handle.egress_id,
            r2_key=key,
        )
        return handle

    async def aclose(self) -> None:
        """Tear down the LiveKit API client. Safe to call when never used."""
        if self._api is None:
            return
        try:
            close: Callable[[], Awaitable[None]] | None = getattr(self._api, "aclose", None)
            if close is not None:
                await close()
        except Exception as exc:
            log.debug("egress.close_warning", error=str(exc))
        finally:
            self._api = None

    async def _ensure_api(self) -> LiveKitAPI:
        if self._api is not None:
            return self._api
        async with self._lock:
            if self._api is None:
                self._api = self._api_factory()
            return self._api


class NullEgressDispatcher:
    """No-op dispatcher for tests / environments without R2 or LiveKit creds.

    Returns None from every start — the runtime treats that as "no
    recording happening this session" and proceeds. Same protocol shape
    so the runtime doesn't have to branch on dispatcher presence.
    """

    async def start_room_composite(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
    ) -> EgressHandle | None:
        _ = (session_id, room_name)
        return None

    async def start_participant_audio(
        self,
        *,
        session_id: uuid.UUID,
        room_name: str,
        identity: str,
    ) -> EgressHandle | None:
        _ = (session_id, room_name, identity)
        return None

    async def aclose(self) -> None:
        return None


def _build_composite_request(
    *,
    room_name: str,
    r2: R2EgressConfig,
    filepath: str,
) -> RoomCompositeEgressRequest:
    """Construct `RoomCompositeEgressRequest` with an R2 S3 destination.

    Imports the LiveKit proto modules lazily so this file (and its
    tests) stay importable in environments that don't have
    `livekit-api` installed — the same pattern used in `agent/worker.py`
    for the rtc SDK.
    """
    from livekit.api import (
        EncodedFileOutput,
        EncodedFileType,
        RoomCompositeEgressRequest,
        S3Upload,
    )

    s3 = S3Upload(
        access_key=r2.access_key_id,
        secret=r2.secret_access_key,
        region="auto",
        endpoint=r2.endpoint,
        bucket=r2.bucket,
        force_path_style=True,
    )
    file_output = EncodedFileOutput(
        file_type=EncodedFileType.MP4,
        filepath=filepath,
        s3=s3,
    )
    return RoomCompositeEgressRequest(
        room_name=room_name,
        # `audio_only=False` here — the composite captures both audio
        # and video (the brief is silent on per-recording media types,
        # so we default to the richer artifact and let the audio-only
        # per-participant tracks cover transcript-aligned playback).
        audio_only=False,
        file_outputs=[file_output],
    )


def _build_participant_request(
    *,
    room_name: str,
    identity: str,
    r2: R2EgressConfig,
    filepath: str,
) -> ParticipantEgressRequest:
    """Construct `ParticipantEgressRequest` with an R2 S3 destination.

    Per-participant egress records every track the identity publishes
    for the duration of the session into one OGG/Opus file. That's
    enough for transcript-aligned playback in the replay UI — full
    video isolation per participant is out of scope for Phase 6.
    """
    from livekit.api import (
        EncodedFileOutput,
        EncodedFileType,
        ParticipantEgressRequest,
        S3Upload,
    )

    s3 = S3Upload(
        access_key=r2.access_key_id,
        secret=r2.secret_access_key,
        region="auto",
        endpoint=r2.endpoint,
        bucket=r2.bucket,
        force_path_style=True,
    )
    file_output = EncodedFileOutput(
        file_type=EncodedFileType.OGG,
        filepath=filepath,
        s3=s3,
    )
    return ParticipantEgressRequest(
        room_name=room_name,
        identity=identity,
        file_outputs=[file_output],
    )
