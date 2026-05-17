"""`pick_voice_for_persona` — best-fit voice given persona attributes."""

from __future__ import annotations

import pytest

from verbio_engine.mouth.persona import ModeratorPersona
from verbio_engine.voices import (
    CuratedVoice,
    list_voices_for_provider,
    pick_voice_for_persona,
)


def _persona(**overrides: object) -> ModeratorPersona:
    base: dict[str, object] = {
        "style_prompt": "Test persona",
        "tone": "neutral",
        "formality": "neutral",
        "voice_provider": "cartesia",
        "voice_id": "placeholder",
    }
    base.update(overrides)
    return ModeratorPersona(**base)  # type: ignore[arg-type]


def _voice(
    voice_id: str,
    *,
    provider: str = "cartesia",
    formality: str = "neutral",
    warmth: str = "neutral",
    pace: str = "steady",
) -> CuratedVoice:
    return CuratedVoice(
        voice_id=voice_id,
        provider=provider,  # type: ignore[arg-type]
        display_name=voice_id,
        formality=formality,  # type: ignore[arg-type]
        warmth=warmth,  # type: ignore[arg-type]
        pace=pace,  # type: ignore[arg-type]
        description="t",
    )


class TestProviderRouting:
    def test_returns_voice_from_personas_provider(self) -> None:
        for provider in ("cartesia", "elevenlabs"):
            persona = _persona(voice_provider=provider)
            picked = pick_voice_for_persona(persona)
            assert picked.provider == provider

    def test_raises_when_library_has_no_voices_for_provider(self) -> None:
        # Library only has cartesia voices, but persona asks elevenlabs.
        library = (_voice("c-only", provider="cartesia"),)
        persona = _persona(voice_provider="elevenlabs")
        with pytest.raises(ValueError, match="no voices for provider"):
            pick_voice_for_persona(persona, library=library)


class TestScoringPrefersFormalityOverWarmth:
    def test_formality_match_beats_warmth_only_match(self) -> None:
        # Persona: formal + warm tone.
        # Voice A: matches formality (formal) but cool warmth.
        # Voice B: matches warmth (warm) but casual formality.
        # Formality weighs 3, warmth weighs 2 → A wins.
        library = (
            _voice("a", formality="formal", warmth="cool"),
            _voice("b", formality="casual", warmth="warm"),
        )
        picked = pick_voice_for_persona(
            _persona(formality="formal", tone="warm"),
            library=library,
        )
        assert picked.voice_id == "a"

    def test_double_match_wins_over_single_match(self) -> None:
        library = (
            _voice("partial", formality="formal", warmth="cool"),
            _voice("perfect", formality="formal", warmth="warm"),
        )
        picked = pick_voice_for_persona(
            _persona(formality="formal", tone="warm"),
            library=library,
        )
        assert picked.voice_id == "perfect"

    def test_ties_broken_by_library_order(self) -> None:
        # Two voices score identically (both match nothing). The first
        # in the library wins — researchers can re-order to set the
        # default; without that contract, two identical-score voices
        # would be picked nondeterministically.
        library = (
            _voice("first", formality="casual", warmth="cool"),
            _voice("second", formality="casual", warmth="cool"),
        )
        picked = pick_voice_for_persona(
            _persona(formality="formal", tone="warm"),
            library=library,
        )
        assert picked.voice_id == "first"


class TestToneMapping:
    @pytest.mark.parametrize(
        ("tone", "expected_warmth"),
        [("warm", "warm"), ("neutral", "neutral"), ("professional", "cool")],
    )
    def test_tone_maps_to_voice_warmth(
        self,
        tone: str,
        expected_warmth: str,
    ) -> None:
        # Build a library whose only differentiator is warmth.
        library = (
            _voice("warm", warmth="warm"),
            _voice("neutral", warmth="neutral"),
            _voice("cool", warmth="cool"),
        )
        picked = pick_voice_for_persona(
            _persona(tone=tone, formality="neutral"),  # neutral formality so warmth decides
            library=library,
        )
        assert picked.warmth == expected_warmth


class TestDefaultLibraryProducesUsableDefaults:
    @pytest.mark.parametrize(
        ("voice_provider", "tone", "formality"),
        [
            ("cartesia", "warm", "casual"),
            ("cartesia", "neutral", "neutral"),
            ("cartesia", "professional", "formal"),
            ("elevenlabs", "warm", "casual"),
            ("elevenlabs", "neutral", "neutral"),
            ("elevenlabs", "professional", "formal"),
        ],
    )
    def test_picker_returns_a_curated_voice_for_each_persona_combo(
        self,
        voice_provider: str,
        tone: str,
        formality: str,
    ) -> None:
        # Smoke-test against the shipped library: every reasonable
        # persona combination must resolve to a real voice. If a future
        # trim of the library leaves a combination unanswered, the
        # picker should still return something (highest scorer, even
        # if score is 0) — pin that fallback behaviour.
        persona = _persona(
            voice_provider=voice_provider,
            tone=tone,
            formality=formality,
        )
        picked = pick_voice_for_persona(persona)
        assert picked.provider == voice_provider
        assert picked in list_voices_for_provider(voice_provider)
