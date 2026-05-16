"""Per-track audio → VAD → Deepgram → utterance pipeline.

Each remote audio track in a LiveKit room is owned by exactly one
`TrackTranscriber`. The transcriber pulls 16 kHz mono frames from the
LiveKit `AudioStream`, pushes them through a VAD-gated Deepgram stream,
and persists each STT `SpeechEvent` as an `Utterance` row.

Why per-track and not a single mixed stream:
  - The brief mandates per-participant attribution (rules consume
    `(speaker_id, text, timing)` tuples; mixed audio loses that).
  - VAD is per-speaker, not per-room — a mixed stream produces
    cross-talk soup that wastes Deepgram budget on overlapping speech.
  - Each Deepgram WebSocket is independent: one slow speaker's
    backpressure doesn't stall the others.

Why we persist *both* interim and final transcripts:
  - The dashboard needs partials to render text as it streams (brief
    §5.1 — live transcript pane updates within ~500 ms of speech).
  - Final rows are the canonical record rules consume; interim rows
    are filtered out of rule-relevant queries via
    `UtteranceRepo.recent(..., finals_only=True)`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from livekit.agents import stt as agents_stt

from verbio_engine.logging import get_logger
from verbio_engine.persistence import UtteranceInsert

if TYPE_CHECKING:
    import uuid

    from livekit import rtc

    from verbio_engine.agent.runtime import SessionRuntime

log = get_logger(__name__)


# SpeechEventTypes we persist. START_OF_SPEECH / END_OF_SPEECH /
# RECOGNITION_USAGE are bookkeeping events; we ignore them for the
# utterances table. PREFLIGHT is treated as interim (a low-latency
# pre-final emitted while finalisation is in flight).
_PERSIST_TYPES = frozenset(
    {
        agents_stt.SpeechEventType.INTERIM_TRANSCRIPT,
        agents_stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
        agents_stt.SpeechEventType.FINAL_TRANSCRIPT,
    }
)


class TrackTranscriber:
    """Owns one (participant, audio-track) pair end-to-end.

    Lifecycle:
      - Constructed by `agent.worker` when `track_subscribed` fires.
      - `run(audio_stream)` is a long-running coroutine that returns
        when the audio stream closes (track unpublished / participant
        disconnected) or the task is cancelled.
      - Cancellation drains the STT stream (`end_input` → `aclose`)
        so partial in-flight transcripts don't leak as ghost rows.
    """

    def __init__(
        self,
        *,
        stt: agents_stt.STT[Any],
        runtime: SessionRuntime,
        participant_identity: str,
    ) -> None:
        self._stt = stt
        self._runtime = runtime
        self._identity = participant_identity
        # Resolved on first `run()` so construction stays cheap and
        # synchronous (matches the rest of the worker's sync handlers).
        self._participant_id: uuid.UUID | None = None

    async def run(self, audio_stream: rtc.AudioStream) -> None:
        """Drive the audio → STT → DB loop until `audio_stream` closes."""
        # Block until the participant row exists. The connect handler
        # runs the upsert; `track_subscribed` can fire before that
        # finishes, so we wait on the runtime's per-identity Event.
        try:
            self._participant_id = await self._runtime.await_participant_id(self._identity)
        except TimeoutError:
            log.error(
                "transcribe.participant_join_timeout",
                identity=self._identity,
                hint="track_subscribed fired but participant_connected never resolved",
            )
            return

        stt_stream = self._stt.stream()

        async def _feed() -> None:
            """Pump audio frames from LiveKit into the STT stream."""
            async for event in audio_stream:
                stt_stream.push_frame(event.frame)

        feeder = asyncio.create_task(_feed(), name=f"transcribe-feed-{self._identity}")
        try:
            await self._drain(stt_stream)
        finally:
            feeder.cancel()
            # Cancellation is the expected exit path for the feeder when
            # the drainer finishes first (e.g., STT stream closes from
            # the server side). Any other error here would already have
            # been logged by the feeder body itself — `_feed` only does
            # `push_frame`, which is sync; the only realistic raise is
            # CancelledError. Suppress Exception too defensively.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await feeder
            # `end_input` flushes the STT side so any in-flight
            # transcript is emitted. It's idempotent — safe to call
            # whether or not the feeder ever ran or already ended.
            stt_stream.end_input()
            await stt_stream.aclose()

    async def _drain(self, stt_stream: agents_stt.RecognizeStream) -> None:
        """Persist STT events as they arrive."""
        assert self._participant_id is not None, "_drain called before participant resolved"
        session_id = self._runtime.session_id
        async for event in stt_stream:
            if event.type not in _PERSIST_TYPES:
                continue
            record = _speech_event_to_utterance(
                event,
                session_id=session_id,
                participant_id=self._participant_id,
            )
            if record is None:
                # Empty alternatives or empty text — Deepgram occasionally
                # emits these on connection hiccups. Nothing to persist.
                continue
            try:
                await self._runtime.persist_utterance(record)
            except Exception as exc:
                log.exception(
                    "transcribe.persist_failed",
                    identity=self._identity,
                    text=record.text[:80],
                    error=str(exc),
                )


def _speech_event_to_utterance(
    event: agents_stt.SpeechEvent,
    *,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> UtteranceInsert | None:
    """Map a Deepgram `SpeechEvent` to our `UtteranceInsert` shape.

    Returns None when there's nothing worth persisting (no alternatives
    or empty text). The top alternative wins — Deepgram doesn't surface
    secondary alternatives in interim or final transcripts by default.

    Timing: Deepgram returns `start_time` / `end_time` as seconds-from-
    stream-start (floats). We anchor them to wall-clock at insert time
    so utterances align with `participants.joined_at` and other event
    timestamps within the session.
    """
    if not event.alternatives:
        return None
    primary = event.alternatives[0]
    text = primary.text.strip()
    if not text:
        return None

    now = datetime.now(UTC)
    duration = max(0.0, primary.end_time - primary.start_time)
    # When the SDK omits start/end (some interim events), fall back to
    # zero duration anchored at `now` — better a degenerate timestamp
    # than a NULL violation on a NOT NULL column.
    end_ts = now
    start_ts = now - timedelta(seconds=duration) if duration > 0 else now

    return UtteranceInsert(
        session_id=session_id,
        participant_id=participant_id,
        start_ts=start_ts,
        end_ts=end_ts,
        text=text,
        # SpeechData.confidence defaults to 0.0; treat that as "not
        # reported" rather than persisting a falsely-low score.
        confidence=primary.confidence if primary.confidence > 0 else None,
        is_final=event.type == agents_stt.SpeechEventType.FINAL_TRANSCRIPT,
    )
