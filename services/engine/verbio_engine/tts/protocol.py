"""`TTSClient` — the contract every TTS implementation honours (brief §9).

V1 implementations:
  - `CartesiaTTS` (P4 L3) — Cartesia Sonic, low-latency default.
  - `ElevenLabsTTS` (P4 L4) — ElevenLabs Flash, fallback.

Both consume a single text string (the moderator's full one-sentence
intervention) and yield PCM audio chunks as the provider streams them.
The orchestrator (P4 L8) accumulates `MouthChunk` text into the full
sentence and hands it here; true bidirectional streaming (LLM tokens
into TTS as they arrive) is a follow-up optimisation gated by the
brief's §6 1500 ms p95 budget — measured first, optimised later.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

DEFAULT_SAMPLE_RATE = 24000
"""Cartesia Sonic's native sample rate; ElevenLabs Flash defaults match.
LiveKit handles resampling on publish so this stays internal."""


class TTSError(Exception):
    """Boundary error wrapping any provider HTTP / parse failure.

    The orchestrator catches this one type and decides whether to
    retry, fall through to the §9 fallback provider, or abandon the
    intervention. Underlying causes (httpx errors, JSON decode, etc.)
    are preserved via `__cause__`.
    """


@dataclass(frozen=True)
class TTSRequest:
    """Input to the TTS layer — provider-agnostic.

    Provider selection (Cartesia vs ElevenLabs) happens above this
    layer; `voice_id` is interpreted by whichever implementation is
    invoked. `sample_rate` is the requested PCM rate; the provider may
    silently coerce to the closest supported value.
    """

    voice_id: str
    sample_rate: int = DEFAULT_SAMPLE_RATE


@dataclass(frozen=True)
class AudioChunk:
    """One streamed piece of PCM audio.

    `pcm` is raw 16-bit signed little-endian samples (`pcm_s16le`) at
    `sample_rate`. The terminator chunk has `is_final=True` and empty
    `pcm`; consumers feed bytes to TTS-out until they see it.
    """

    pcm: bytes
    sample_rate: int
    is_final: bool = False


@runtime_checkable
class TTSClient(Protocol):
    """The TTS-layer contract per brief §9."""

    def synthesize(
        self,
        request: TTSRequest,
        text: str,
    ) -> AsyncIterator[AudioChunk]:
        """Yield PCM chunks for `text` in the requested voice."""
        ...
