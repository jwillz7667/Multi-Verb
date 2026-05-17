"""`verbio_engine.tts` — text-to-speech layer (brief §9).

Synthesises one already-decided, already-phrased intervention into
PCM audio that the LiveKit wiring layer (P4 L7) publishes as the
moderator participant's audio track.

The contract is intentionally minimal: a single `synthesize(request,
text) -> AsyncIterator[AudioChunk]` method per implementation, so the
default provider (Cartesia Sonic) and the fallback (ElevenLabs Flash,
P4 L4) are interchangeable. Voice selection is driven by the
`ModeratorPersona`'s `voice_provider` + `voice_id` fields — the wiring
layer picks which TTSClient instance to invoke per persona.

Public surface:
  - `TTSClient` (Protocol) + `TTSRequest` + `AudioChunk` — the contract.
  - `TTSError` — boundary exception wrapping HTTP / parse failures.
  - `CartesiaTTS` (P4 L3) — production default.
"""

from verbio_engine.tts.cartesia import (
    DEFAULT_BASE_URL as CARTESIA_DEFAULT_BASE_URL,
    DEFAULT_CARTESIA_VERSION,
    DEFAULT_MODEL_ID as CARTESIA_DEFAULT_MODEL_ID,
    CartesiaTTS,
)
from verbio_engine.tts.protocol import (
    DEFAULT_SAMPLE_RATE,
    AudioChunk,
    TTSClient,
    TTSError,
    TTSRequest,
)

__all__ = [
    "CARTESIA_DEFAULT_BASE_URL",
    "CARTESIA_DEFAULT_MODEL_ID",
    "DEFAULT_CARTESIA_VERSION",
    "DEFAULT_SAMPLE_RATE",
    "AudioChunk",
    "CartesiaTTS",
    "TTSClient",
    "TTSError",
    "TTSRequest",
]
