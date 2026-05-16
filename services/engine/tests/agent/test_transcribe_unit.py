"""Unit tests for `agent.transcribe` — no LiveKit, no Deepgram.

The transcriber is the audio → utterance bridge. These tests pin the
parts that don't need a real audio stream:

  - `_speech_event_to_utterance` mapping: which SpeechEvents become
    rows, which get filtered, how confidence / timing are derived.
  - `TrackTranscriber.run` orchestration against fake STT + audio
    streams: events drain into `runtime.persist_utterance`, errors
    are logged-and-swallowed, cancellation tears down cleanly.

The real Deepgram + Silero are exercised by the L6 Playwright E2E
which spins up fake-participant audio.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from livekit.agents.stt import SpeechData, SpeechEvent, SpeechEventType

from verbio_engine.agent.transcribe import TrackTranscriber, _speech_event_to_utterance

if TYPE_CHECKING:
    from verbio_engine.persistence import UtteranceInsert


# ---------------------------------------------------------------------------
# _speech_event_to_utterance — pure mapping
# ---------------------------------------------------------------------------


def _mk_event(
    text: str,
    *,
    event_type: SpeechEventType = SpeechEventType.FINAL_TRANSCRIPT,
    confidence: float = 0.0,
    start_time: float = 0.0,
    end_time: float = 0.0,
    alternatives: list[SpeechData] | None = None,
) -> SpeechEvent:
    alts = (
        alternatives
        if alternatives is not None
        else [
            SpeechData(
                language="en-US",
                text=text,
                start_time=start_time,
                end_time=end_time,
                confidence=confidence,
            )
        ]
    )
    return SpeechEvent(type=event_type, alternatives=alts)


_SESSION = uuid.uuid4()
_PARTICIPANT = uuid.uuid4()


def test_final_transcript_maps_with_is_final_true() -> None:
    record = _speech_event_to_utterance(
        _mk_event("hello world", confidence=0.92, start_time=1.0, end_time=2.5),
        session_id=_SESSION,
        participant_id=_PARTICIPANT,
    )

    assert record is not None
    assert record.text == "hello world"
    assert record.is_final is True
    assert record.confidence == pytest.approx(0.92)
    assert record.session_id == _SESSION
    assert record.participant_id == _PARTICIPANT
    # Duration anchored to wall clock: end - start ≈ 1.5s.
    delta = (record.end_ts - record.start_ts).total_seconds()
    assert delta == pytest.approx(1.5, abs=1e-3)


def test_interim_transcript_maps_with_is_final_false() -> None:
    record = _speech_event_to_utterance(
        _mk_event("partial", event_type=SpeechEventType.INTERIM_TRANSCRIPT),
        session_id=_SESSION,
        participant_id=_PARTICIPANT,
    )

    assert record is not None
    assert record.is_final is False


def test_preflight_transcript_treated_as_interim() -> None:
    record = _speech_event_to_utterance(
        _mk_event("almost", event_type=SpeechEventType.PREFLIGHT_TRANSCRIPT),
        session_id=_SESSION,
        participant_id=_PARTICIPANT,
    )

    assert record is not None
    # Only FINAL_TRANSCRIPT is treated as final; preflight stays interim
    # so the UI distinguishes still-finalising text from the canonical
    # transcript rows rules consume.
    assert record.is_final is False


def test_empty_alternatives_returns_none() -> None:
    event = SpeechEvent(type=SpeechEventType.FINAL_TRANSCRIPT, alternatives=[])
    assert (
        _speech_event_to_utterance(event, session_id=_SESSION, participant_id=_PARTICIPANT) is None
    )


def test_whitespace_only_text_returns_none() -> None:
    assert (
        _speech_event_to_utterance(
            _mk_event("   "),
            session_id=_SESSION,
            participant_id=_PARTICIPANT,
        )
        is None
    )


def test_zero_confidence_is_dropped() -> None:
    """Deepgram defaults `confidence=0.0` when not reported.

    We treat 0.0 as "unknown" and persist NULL rather than a falsely-low
    score that downstream consumers might filter on.
    """
    record = _speech_event_to_utterance(
        _mk_event("test", confidence=0.0),
        session_id=_SESSION,
        participant_id=_PARTICIPANT,
    )
    assert record is not None
    assert record.confidence is None


def test_zero_duration_falls_back_to_now_for_both_ends() -> None:
    """Interim events sometimes omit timing; never insert NULL timestamps."""
    record = _speech_event_to_utterance(
        _mk_event("blip", start_time=0.0, end_time=0.0),
        session_id=_SESSION,
        participant_id=_PARTICIPANT,
    )
    assert record is not None
    # Both timestamps equal when duration is zero (degenerate but valid).
    assert record.start_ts == record.end_ts


# ---------------------------------------------------------------------------
# TrackTranscriber.run — orchestration with fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeSTTStream:
    events: list[SpeechEvent]
    pushed_frames: list[object] = field(default_factory=list)
    closed: bool = False
    input_ended: bool = False
    _drained: asyncio.Event = field(default_factory=asyncio.Event)

    def push_frame(self, frame: object) -> None:
        self.pushed_frames.append(frame)

    def end_input(self) -> None:
        self.input_ended = True

    async def aclose(self) -> None:
        self.closed = True

    def __aiter__(self) -> _FakeSTTStream:
        return self

    async def __anext__(self) -> SpeechEvent:
        # Yield to the event loop between events so the feeder task
        # gets fair scheduling time — otherwise the drainer would
        # consume all pre-loaded events synchronously and starve the
        # feeder before it ever pushes a frame.
        await asyncio.sleep(0)
        if not self.events:
            self._drained.set()
            raise StopAsyncIteration
        return self.events.pop(0)


class _FakeSTT:
    def __init__(self, stream: _FakeSTTStream) -> None:
        self._stream = stream

    def stream(self) -> _FakeSTTStream:
        return self._stream


@dataclass
class _FakeAudioFrameEvent:
    frame: object


class _FakeAudioStream:
    """Async iterable that yields a fixed sequence of frames then stops."""

    def __init__(self, frames: list[object]) -> None:
        self._frames = frames

    def __aiter__(self) -> AsyncIterator[_FakeAudioFrameEvent]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[_FakeAudioFrameEvent]:
        for f in self._frames:
            # Yield control so the drainer + feeder interleave realistically.
            await asyncio.sleep(0)
            yield _FakeAudioFrameEvent(frame=f)


class _FakeRuntime:
    """Stand-in for SessionRuntime — captures persist calls in-memory."""

    def __init__(self, *, participant_id: uuid.UUID) -> None:
        self._participant_id = participant_id
        self.session_id = uuid.uuid4()
        self.persisted: list[UtteranceInsert] = []

    async def await_participant_id(
        self,
        identity: str,
        *,
        timeout: float = 10.0,  # noqa: ASYNC109 — mirrors SessionRuntime API
    ) -> uuid.UUID:
        _ = identity, timeout
        return self._participant_id

    async def persist_utterance(self, record: UtteranceInsert) -> None:
        self.persisted.append(record)


async def test_run_drains_events_into_persist() -> None:
    pid = uuid.uuid4()
    runtime = _FakeRuntime(participant_id=pid)
    stream = _FakeSTTStream(
        events=[
            _mk_event("hi", event_type=SpeechEventType.INTERIM_TRANSCRIPT),
            _mk_event("hi there", event_type=SpeechEventType.FINAL_TRANSCRIPT),
            # bookkeeping events must be ignored
            SpeechEvent(type=SpeechEventType.START_OF_SPEECH, alternatives=[]),
        ]
    )
    audio = _FakeAudioStream(frames=[object(), object(), object()])

    transcriber = TrackTranscriber(
        stt=_FakeSTT(stream),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        participant_identity="id-x",
    )
    await transcriber.run(audio)  # type: ignore[arg-type]

    assert [u.text for u in runtime.persisted] == ["hi", "hi there"]
    assert [u.is_final for u in runtime.persisted] == [False, True]
    assert all(u.participant_id == pid for u in runtime.persisted)
    assert stream.input_ended is True
    assert stream.closed is True
    assert len(stream.pushed_frames) == 3


async def test_run_aborts_when_participant_never_joins() -> None:
    class _NeverJoinsRuntime(_FakeRuntime):
        async def await_participant_id(
            self,
            identity: str,
            *,
            timeout: float = 10.0,  # noqa: ASYNC109 — mirrors SessionRuntime API
        ) -> uuid.UUID:
            _ = identity, timeout
            raise TimeoutError

    runtime = _NeverJoinsRuntime(participant_id=uuid.uuid4())
    stream = _FakeSTTStream(events=[])
    audio = _FakeAudioStream(frames=[])

    transcriber = TrackTranscriber(
        stt=_FakeSTT(stream),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        participant_identity="id-ghost",
    )
    # Must not raise — timeout is the expected exit path; transcriber
    # logs and returns so the worker doesn't crash on a slow join.
    await transcriber.run(audio)  # type: ignore[arg-type]

    assert runtime.persisted == []
    # Stream is never opened, so no aclose / end_input was called.
    assert stream.closed is False


async def test_run_swallows_persist_errors_and_continues() -> None:
    """A single failing insert must not stop the STT loop.

    Losing one utterance row is bad; losing the rest of the session's
    transcript because of it is worse. The brief's audit-trail invariant
    is best-effort per row, not transactional across the whole stream.
    """
    pid = uuid.uuid4()

    class _FlakyRuntime(_FakeRuntime):
        def __init__(self) -> None:
            super().__init__(participant_id=pid)
            self.calls = 0

        async def persist_utterance(self, record: UtteranceInsert) -> None:
            self.calls += 1
            if self.calls == 1:
                msg = "simulated DB hiccup"
                raise RuntimeError(msg)
            self.persisted.append(record)

    runtime = _FlakyRuntime()
    stream = _FakeSTTStream(
        events=[
            _mk_event("first", event_type=SpeechEventType.FINAL_TRANSCRIPT),
            _mk_event("second", event_type=SpeechEventType.FINAL_TRANSCRIPT),
        ]
    )
    audio = _FakeAudioStream(frames=[])

    transcriber = TrackTranscriber(
        stt=_FakeSTT(stream),  # type: ignore[arg-type]
        runtime=runtime,  # type: ignore[arg-type]
        participant_identity="id-flaky",
    )
    await transcriber.run(audio)  # type: ignore[arg-type]

    # First failed; second succeeded.
    assert [u.text for u in runtime.persisted] == ["second"]
    assert runtime.calls == 2
