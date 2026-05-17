"""`ModeratorPersona` validation — frozen, strict, defaulted (brief §7.5).

The persona ships per-study and gets snapshotted into the session at
start. These tests pin the shape so a typo in a study row surfaces at
session-load, not as a silently dropped prompt clause hours later.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verbio_engine.mouth.persona import ModeratorPersona


class TestModeratorPersonaConstruction:
    def test_minimal_construction_uses_documented_defaults(self) -> None:
        # Only the two required fields — everything else defaults so a
        # study can opt out of bikeshedding tone/formality and still
        # get a sensible moderator.
        persona = ModeratorPersona(
            style_prompt="You are a calm, curious moderator.",
            voice_id="sonic-en-us-1",
        )
        assert persona.tone == "warm"
        assert persona.formality == "neutral"
        assert persona.voice_provider == "cartesia"

    def test_full_construction_round_trip(self) -> None:
        persona = ModeratorPersona(
            style_prompt="Speak briefly and stay neutral.",
            tone="professional",
            formality="formal",
            voice_provider="elevenlabs",
            voice_id="flash-uk-2",
        )
        # model_dump / model_validate round-trips through the JSON
        # representation that ends up in `sessions.config_snapshot`.
        rebuilt = ModeratorPersona.model_validate(persona.model_dump())
        assert rebuilt == persona


class TestModeratorPersonaIsFrozen:
    def test_assignment_after_construction_raises(self) -> None:
        persona = ModeratorPersona(
            style_prompt="Calm.",
            voice_id="v1",
        )
        # frozen=True — guards against runtime code accidentally
        # mutating the shared per-session config snapshot.
        with pytest.raises(ValidationError):
            persona.tone = "professional"  # type: ignore[misc]


class TestModeratorPersonaRejectsBadInput:
    def test_unknown_field_is_rejected(self) -> None:
        # extra="forbid" — a typo'd persona key (`stlye_prompt`) must
        # crash the study config validator, not silently land as no-op.
        with pytest.raises(ValidationError, match="Extra inputs"):
            ModeratorPersona(
                style_prompt="x",
                voice_id="v1",
                stlye_prompt="oops",  # type: ignore[call-arg]
            )

    def test_empty_style_prompt_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModeratorPersona(style_prompt="", voice_id="v1")

    def test_overlong_style_prompt_is_rejected(self) -> None:
        # Long persona prompts crowd out the §8.2 constant suffix and
        # the per-decision context. 500 chars is plenty for 1-2 sentences.
        with pytest.raises(ValidationError):
            ModeratorPersona(style_prompt="x" * 501, voice_id="v1")

    def test_empty_voice_id_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModeratorPersona(style_prompt="ok", voice_id="")

    def test_unknown_tone_is_rejected(self) -> None:
        # Closed Literal — guards the prompt builder from a downstream
        # KeyError when it interpolates `persona.tone` into the prompt.
        with pytest.raises(ValidationError):
            ModeratorPersona(
                style_prompt="ok",
                voice_id="v1",
                tone="sassy",  # type: ignore[arg-type]
            )

    def test_unknown_formality_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModeratorPersona(
                style_prompt="ok",
                voice_id="v1",
                formality="ultra-formal",  # type: ignore[arg-type]
            )

    def test_unknown_voice_provider_is_rejected(self) -> None:
        # Only Cartesia + ElevenLabs ship in v1 (brief §8.3); a new
        # provider must be added in code, not configured in.
        with pytest.raises(ValidationError):
            ModeratorPersona(
                style_prompt="ok",
                voice_id="v1",
                voice_provider="openai-tts",  # type: ignore[arg-type]
            )
