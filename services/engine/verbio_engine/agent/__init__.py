"""LiveKit moderator agent — Phase 1 audio plumbing.

Public surface:
  - `SessionRuntime`       : transport-agnostic per-session orchestrator
  - `ParticipantSnapshot`  : lightweight DTO the LiveKit glue passes in
  - `TrackTranscriber`     : per-track VAD → Deepgram → utterance pipeline
  - `run_worker`           : LiveKit Agents CLI entrypoint
  - `ModeratorAudioPublisher` (P4 L7) : pushes TTS PCM onto a LiveKit
    track as the moderator participant. `LiveKitAudioBackend` is the
    default backend; tests substitute a recording fake via the
    `AudioPublishBackend` Protocol.

`SessionRuntime` is the heart of the agent. It owns the per-session
persistence transitions (session start/end, participant join/leave) and
is intentionally independent of the LiveKit SDK so it can be unit-tested
without spinning up a real room. The LiveKit-facing glue lives in
`agent.worker` and calls `SessionRuntime` from event callbacks.
"""

from verbio_engine.agent.audio_publisher import (
    AudioPublishBackend,
    LiveKitAudioBackend,
    ModeratorAudioPublisher,
)
from verbio_engine.agent.runtime import ParticipantSnapshot, SessionRuntime
from verbio_engine.agent.transcribe import TrackTranscriber
from verbio_engine.agent.worker import run_worker

__all__ = [
    "AudioPublishBackend",
    "LiveKitAudioBackend",
    "ModeratorAudioPublisher",
    "ParticipantSnapshot",
    "SessionRuntime",
    "TrackTranscriber",
    "run_worker",
]
