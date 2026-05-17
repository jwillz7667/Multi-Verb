"""`FallbackPhraseCache` — pre-synthesised audio for the §8.4 fallback path.

When the mouth's LLM call exceeds its 800 ms wall-clock budget (brief
§8.4), the orchestrator (P4 L8) falls back to a templated phrasing.
Live TTS on that fallback adds another ~200-400 ms — exactly the
budget the user is already over. Brief §9 calls out the fix: pre-
synthesise the templated phrasings per persona and serve the cached
PCM bytes directly.

Scope is narrow:
  * Untargeted templates only. Templates that interpolate
    `target_display_name` ("Sarah, I'd love to hear ...") are session-
    and participant-specific — caching them per persona would require
    knowing every potential target up front. The orchestrator falls
    through to live TTS in that case; the cache covers the wider
    redirect/summarize/turn-taking surface.
  * Five spoken `DecisionAction`s. `stay_silent` never reaches the
    mouth (the orchestrator gates it); `close_session` is the session
    lifecycle's responsibility (Phase 5), not the mouth's.
  * Keyed on `(voice_provider, voice_id, action)`. Other persona fields
    (`style_prompt`, `tone`, `formality`) affect the LLM path, not the
    pre-rendered template — they're correctly absent from the key.

The cache is in-memory and per-process. A future iteration can flush
to Redis or R2 if cold-start latency becomes a constraint; the API
shape is stable enough that a backing-store swap is local.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.templates import format_template
from verbio_engine.tts.protocol import DEFAULT_SAMPLE_RATE, TTSRequest

if TYPE_CHECKING:
    from verbio_engine.mouth.persona import ModeratorPersona
    from verbio_engine.tts.protocol import TTSClient

log = structlog.get_logger(__name__)

SPOKEN_FALLBACK_ACTIONS: tuple[DecisionAction, ...] = (
    "prompt_participant",
    "redirect_topic",
    "summarize_thread",
    "request_clarification",
    "suggest_turn_taking",
)
"""Subset of `DecisionAction` that has a templated phrasing worth caching."""

_CacheKey = tuple[str, str, DecisionAction]


@dataclass(frozen=True)
class CachedFallback:
    """One pre-synthesised fallback phrasing — PCM bytes ready to publish."""

    pcm: bytes
    sample_rate: int
    text: str


class FallbackPhraseCache:
    """Per-process cache of pre-synthesised templated phrasings."""

    def __init__(self, *, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        self._sample_rate = sample_rate
        self._entries: dict[_CacheKey, CachedFallback] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @staticmethod
    def _key(persona: ModeratorPersona, action: DecisionAction) -> _CacheKey:
        # voice_provider + voice_id fully determines the rendered audio;
        # other persona fields shape the LLM path, not the template path.
        return (persona.voice_provider, persona.voice_id, action)

    async def warm(self, persona: ModeratorPersona, tts: TTSClient) -> None:
        """Pre-synthesise every spoken-action template for `persona`.

        Existing entries for the persona are overwritten (callers who
        change a persona's `style_prompt` mid-session don't need to
        invalidate first; the new audio is identical and the dict
        assignment is the no-op-equivalent).

        Errors from the underlying TTS provider propagate. The caller
        decides whether one failed warm-up should abort the session or
        leave the cache partially populated.
        """
        # An empty PhrasingContext drives `format_template` to its
        # without-target branch for every action.
        ctx = PhrasingContext()
        request = TTSRequest(voice_id=persona.voice_id, sample_rate=self._sample_rate)

        for action in SPOKEN_FALLBACK_ACTIONS:
            text = format_template(action, ctx)
            pcm = await _collect_pcm(tts, request, text)
            self._entries[self._key(persona, action)] = CachedFallback(
                pcm=pcm,
                sample_rate=self._sample_rate,
                text=text,
            )
            log.debug(
                "fallback_cache_warmed",
                voice_provider=persona.voice_provider,
                voice_id=persona.voice_id,
                action=action,
                pcm_bytes=len(pcm),
            )

    def get(
        self,
        persona: ModeratorPersona,
        action: DecisionAction,
    ) -> CachedFallback | None:
        """Cached entry for `(persona, action)`, or None if not warmed."""
        return self._entries.get(self._key(persona, action))

    def invalidate(self, persona: ModeratorPersona) -> int:
        """Drop every entry for `persona`; return count removed.

        Called when a persona's `voice_id`/`voice_provider` mutates so
        the next utterance doesn't replay audio from the prior voice.
        Returns 0 when nothing was cached (idempotent).
        """
        prefix = (persona.voice_provider, persona.voice_id)
        stale = [k for k in self._entries if k[0] == prefix[0] and k[1] == prefix[1]]
        for key in stale:
            del self._entries[key]
        return len(stale)

    def clear(self) -> None:
        """Drop every entry; used by tests and on process shutdown."""
        self._entries.clear()


async def _collect_pcm(tts: TTSClient, request: TTSRequest, text: str) -> bytes:
    """Drain a TTS stream into a single PCM byte buffer.

    Excludes the terminator `AudioChunk` (which carries no payload) so
    the cached blob can be replayed verbatim later without trimming.
    """
    buf = bytearray()
    async for chunk in tts.synthesize(request, text):
        if not chunk.is_final:
            buf.extend(chunk.pcm)
    return bytes(buf)
