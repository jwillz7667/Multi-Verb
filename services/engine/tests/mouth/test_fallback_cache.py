"""`FallbackPhraseCache` — pre-synth + lookup + invalidation.

Stubs the `TTSClient` Protocol so the cache contract gets tested
without exercising Cartesia/ElevenLabs. Each spoken action is
synthesised exactly once per persona warm-up, the stored bytes are
the concatenation of every non-terminator chunk, and lookups respect
the `(voice_provider, voice_id, action)` key.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.fallback_cache import (
    SPOKEN_FALLBACK_ACTIONS,
    CachedFallback,
    FallbackPhraseCache,
)
from verbio_engine.mouth.persona import ModeratorPersona
from verbio_engine.tts.protocol import (
    DEFAULT_SAMPLE_RATE,
    AudioChunk,
    TTSError,
    TTSRequest,
)

# ---------------------------------------------------------------------------
# Stub TTS client
# ---------------------------------------------------------------------------


class _StubTTS:
    """Yields a deterministic 2-chunk PCM payload derived from the text.

    Records every (voice_id, sample_rate, text) call so tests can pin
    exactly what the cache asked for. The bytes themselves encode the
    text so identical text always produces identical PCM — the cache's
    key invariant.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []

    def synthesize(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        return self._stream(request, text)

    async def _stream(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        self.calls.append((request.voice_id, request.sample_rate, text))
        body = text.encode("utf-8")
        mid = max(1, len(body) // 2)
        yield AudioChunk(pcm=body[:mid], sample_rate=request.sample_rate)
        yield AudioChunk(pcm=body[mid:], sample_rate=request.sample_rate)
        yield AudioChunk(pcm=b"", sample_rate=request.sample_rate, is_final=True)


class _RaisingAsyncIterator:
    """AsyncIterator that raises `TTSError` on the first `__anext__` pull."""

    def __aiter__(self) -> _RaisingAsyncIterator:
        return self

    async def __anext__(self) -> AudioChunk:
        msg = "upstream provider exploded"
        raise TTSError(msg)


class _RaisingTTS:
    """TTSClient stub that raises `TTSError` once iteration begins.

    Mirrors `CartesiaTTS` failure semantics where the error surfaces
    during streaming, not on the synchronous `synthesize()` call.
    """

    def synthesize(self, request: TTSRequest, text: str) -> AsyncIterator[AudioChunk]:
        del request, text
        return _RaisingAsyncIterator()


def _persona(
    *,
    voice_provider: str = "cartesia",
    voice_id: str = "voice-1",
) -> ModeratorPersona:
    return ModeratorPersona(
        style_prompt="Test persona",
        tone="warm",
        formality="neutral",
        voice_provider=voice_provider,  # type: ignore[arg-type]
        voice_id=voice_id,
    )


# ---------------------------------------------------------------------------
# Warm
# ---------------------------------------------------------------------------


class TestWarm:
    async def test_warms_every_spoken_action_for_persona(self) -> None:
        cache = FallbackPhraseCache()
        tts = _StubTTS()
        persona = _persona()

        await cache.warm(persona, tts)

        # One TTS call per spoken action.
        assert len(tts.calls) == len(SPOKEN_FALLBACK_ACTIONS)
        # Cache holds an entry per action.
        assert len(cache) == len(SPOKEN_FALLBACK_ACTIONS)

    async def test_uses_persona_voice_id_and_default_sample_rate(self) -> None:
        cache = FallbackPhraseCache()
        tts = _StubTTS()
        persona = _persona(voice_id="custom-voice-id")

        await cache.warm(persona, tts)

        for voice_id, sample_rate, _ in tts.calls:
            assert voice_id == "custom-voice-id"
            assert sample_rate == DEFAULT_SAMPLE_RATE

    async def test_respects_custom_sample_rate(self) -> None:
        cache = FallbackPhraseCache(sample_rate=16000)
        tts = _StubTTS()
        persona = _persona()

        await cache.warm(persona, tts)

        for _, sample_rate, _ in tts.calls:
            assert sample_rate == 16000

    async def test_stored_pcm_concatenates_non_terminator_chunks(self) -> None:
        cache = FallbackPhraseCache()
        tts = _StubTTS()
        persona = _persona()

        await cache.warm(persona, tts)

        # Stub encodes text bytes as PCM split across two chunks. So the
        # stored pcm should equal text.encode("utf-8") for each entry,
        # with no terminator byte residue.
        for action in SPOKEN_FALLBACK_ACTIONS:
            entry = cache.get(persona, action)
            assert entry is not None
            assert entry.pcm == entry.text.encode("utf-8")
            assert entry.sample_rate == DEFAULT_SAMPLE_RATE

    async def test_uses_untargeted_template_text(self) -> None:
        # The without-target template variant is what gets cached.
        # Pin it for `redirect_topic` so a future template change moves
        # this test, not silently invalidates every prod cache.
        cache = FallbackPhraseCache()
        tts = _StubTTS()
        persona = _persona()
        await cache.warm(persona, tts)
        entry = cache.get(persona, "redirect_topic")
        assert entry is not None
        assert "let's bring it back" in entry.text.lower()

    async def test_warming_twice_overwrites_idempotently(self) -> None:
        cache = FallbackPhraseCache()
        tts = _StubTTS()
        persona = _persona()

        await cache.warm(persona, tts)
        await cache.warm(persona, tts)

        # Re-warm calls TTS again, but cache size doesn't grow.
        assert len(tts.calls) == 2 * len(SPOKEN_FALLBACK_ACTIONS)
        assert len(cache) == len(SPOKEN_FALLBACK_ACTIONS)

    async def test_warm_failures_propagate_to_caller(self) -> None:
        cache = FallbackPhraseCache()
        with pytest.raises(TTSError, match="upstream provider exploded"):
            await cache.warm(_persona(), _RaisingTTS())


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class TestGet:
    async def test_returns_none_before_warm(self) -> None:
        cache = FallbackPhraseCache()
        assert cache.get(_persona(), "redirect_topic") is None

    async def test_returns_cached_fallback_after_warm(self) -> None:
        cache = FallbackPhraseCache()
        await cache.warm(_persona(), _StubTTS())
        entry = cache.get(_persona(), "redirect_topic")
        assert isinstance(entry, CachedFallback)

    async def test_voice_id_is_part_of_the_cache_key(self) -> None:
        cache = FallbackPhraseCache()
        await cache.warm(_persona(voice_id="voice-A"), _StubTTS())

        # Same provider, different voice id → cache miss.
        assert cache.get(_persona(voice_id="voice-B"), "redirect_topic") is None

    async def test_provider_is_part_of_the_cache_key(self) -> None:
        cache = FallbackPhraseCache()
        await cache.warm(_persona(voice_provider="cartesia", voice_id="x"), _StubTTS())

        # Same voice id, different provider → cache miss.
        assert (
            cache.get(_persona(voice_provider="elevenlabs", voice_id="x"), "redirect_topic") is None
        )

    async def test_non_spoken_action_returns_none(self) -> None:
        # stay_silent and close_session are never cached because they
        # have no template. Lookup should miss cleanly, not raise.
        cache = FallbackPhraseCache()
        await cache.warm(_persona(), _StubTTS())
        non_spoken: tuple[DecisionAction, ...] = ("stay_silent", "close_session")
        for action in non_spoken:
            assert cache.get(_persona(), action) is None


# ---------------------------------------------------------------------------
# Invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    async def test_drops_all_entries_for_persona(self) -> None:
        cache = FallbackPhraseCache()
        await cache.warm(_persona(), _StubTTS())
        removed = cache.invalidate(_persona())
        assert removed == len(SPOKEN_FALLBACK_ACTIONS)
        assert len(cache) == 0

    async def test_preserves_entries_for_other_personas(self) -> None:
        cache = FallbackPhraseCache()
        persona_a = _persona(voice_id="voice-A")
        persona_b = _persona(voice_id="voice-B")

        await cache.warm(persona_a, _StubTTS())
        await cache.warm(persona_b, _StubTTS())
        assert len(cache) == 2 * len(SPOKEN_FALLBACK_ACTIONS)

        cache.invalidate(persona_a)

        # Only persona_b's entries remain.
        assert len(cache) == len(SPOKEN_FALLBACK_ACTIONS)
        for action in SPOKEN_FALLBACK_ACTIONS:
            assert cache.get(persona_a, action) is None
            assert cache.get(persona_b, action) is not None

    async def test_returns_zero_when_persona_not_cached(self) -> None:
        cache = FallbackPhraseCache()
        assert cache.invalidate(_persona()) == 0


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------


def test_spoken_fallback_actions_match_brief_8_4_spec() -> None:
    # Pin the surface: any new spoken action must add a template AND
    # join this tuple, not slip past silently with no fallback audio.
    expected: tuple[DecisionAction, ...] = (
        "prompt_participant",
        "redirect_topic",
        "summarize_thread",
        "request_clarification",
        "suggest_turn_taking",
    )
    assert expected == SPOKEN_FALLBACK_ACTIONS


def test_clear_drops_every_entry() -> None:
    cache = FallbackPhraseCache()
    # Cache starts empty; clear is a no-op that doesn't raise.
    cache.clear()
    assert len(cache) == 0
