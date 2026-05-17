"""Invariants of the curated voice library + `get_voice` lookup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verbio_engine.mouth.persona import VoiceProvider
from verbio_engine.voices import (
    VOICE_LIBRARY,
    CuratedVoice,
    UnknownVoiceError,
    get_voice,
    list_voices_for_provider,
)

PROVIDERS: tuple[VoiceProvider, ...] = ("cartesia", "elevenlabs")


class TestLibraryInvariants:
    @pytest.mark.parametrize("provider", PROVIDERS)
    def test_each_provider_carries_at_least_six_voices(self, provider: VoiceProvider) -> None:
        # Brief §9 mandates 6-8 voices per provider; we ship 7 each but
        # pin the floor so a future trim accidentally below 6 fails.
        voices = list_voices_for_provider(provider)
        assert (
            len(voices) >= 6
        ), f"{provider} library has only {len(voices)} voices; brief §9 requires 6-8."

    def test_voice_ids_are_unique_within_the_library(self) -> None:
        # If a duplicate id sneaks in, get_voice's first-match behaviour
        # would mask the second voice entirely. Catch it at the boundary.
        ids = [v.voice_id for v in VOICE_LIBRARY]
        assert len(ids) == len(set(ids)), "duplicate voice_id in VOICE_LIBRARY"

    def test_display_names_are_unique_within_provider(self) -> None:
        # The UI dropdown shows display names; two voices with the same
        # name in the same provider would confuse researchers.
        for provider in PROVIDERS:
            names = [v.display_name for v in list_voices_for_provider(provider)]
            assert len(names) == len(
                set(names)
            ), f"duplicate display_name within {provider} library"

    def test_every_voice_has_provider_matching_its_bucket(self) -> None:
        for provider in PROVIDERS:
            for voice in list_voices_for_provider(provider):
                assert voice.provider == provider

    def test_library_spans_at_least_two_formality_levels_per_provider(self) -> None:
        # If every voice is the same formality, the picker becomes a
        # no-op and researchers can't honour a casual/formal persona.
        for provider in PROVIDERS:
            formalities = {v.formality for v in list_voices_for_provider(provider)}
            assert (
                len(formalities) >= 2
            ), f"{provider} library covers only formality={formalities}; need ≥2 levels."


class TestListVoicesForProvider:
    def test_filters_to_a_single_provider(self) -> None:
        cartesia = list_voices_for_provider("cartesia")
        assert all(v.provider == "cartesia" for v in cartesia)

    def test_preserves_declaration_order(self) -> None:
        # Picker tiebreak relies on library order being stable — pin it.
        ordered = list_voices_for_provider("cartesia")
        assert ordered == tuple(v for v in VOICE_LIBRARY if v.provider == "cartesia")

    def test_accepts_custom_library(self) -> None:
        custom = (
            CuratedVoice(
                voice_id="custom-1",
                provider="cartesia",
                display_name="Test",
                formality="casual",
                warmth="warm",
                pace="brisk",
                description="Test voice",
            ),
        )
        assert list_voices_for_provider("cartesia", library=custom) == custom
        assert list_voices_for_provider("elevenlabs", library=custom) == ()


class TestGetVoice:
    def test_returns_matching_voice(self) -> None:
        first = VOICE_LIBRARY[0]
        assert get_voice(first.voice_id, first.provider) is first

    def test_raises_unknown_voice_when_id_absent(self) -> None:
        with pytest.raises(UnknownVoiceError, match="not present in curated library"):
            get_voice("does-not-exist", "cartesia")

    def test_raises_when_provider_mismatch_even_if_id_exists(self) -> None:
        # A real elevenlabs id, queried as if it were cartesia, must
        # not return the elevenlabs voice — the (id, provider) pair is
        # the key, not the id alone.
        eleven = list_voices_for_provider("elevenlabs")[0]
        with pytest.raises(UnknownVoiceError):
            get_voice(eleven.voice_id, "cartesia")


class TestCuratedVoiceValidation:
    def test_rejects_unknown_formality(self) -> None:
        with pytest.raises(ValidationError):
            CuratedVoice(
                voice_id="x",
                provider="cartesia",
                display_name="X",
                formality="brusque",  # type: ignore[arg-type]
                warmth="warm",
                pace="steady",
                description="x",
            )

    def test_rejects_extra_fields(self) -> None:
        # Catches typos like `warmnth=` at construction, not at runtime.
        with pytest.raises(ValidationError):
            CuratedVoice(
                voice_id="x",
                provider="cartesia",
                display_name="X",
                formality="casual",
                warmth="warm",
                pace="steady",
                description="x",
                accent="british",  # type: ignore[call-arg]
            )

    def test_is_frozen(self) -> None:
        voice = VOICE_LIBRARY[0]
        with pytest.raises(ValidationError):
            voice.display_name = "renamed"  # type: ignore[misc]
