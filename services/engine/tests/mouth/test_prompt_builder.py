"""`build_prompt` — pins the §8.2 wire shape sent to the mouth LLM.

The mouth-layer JSON is a contract: every implementation (DeepSeek,
templated fallback, any future provider) receives this exact shape.
Drift here would corrupt the §8.1 boundary even if `PhrasingContext`
is fine, because providers parse `system` + `user` directly.
"""

from __future__ import annotations

import pytest

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.persona import ModeratorPersona
from verbio_engine.mouth.prompt_builder import build_prompt


def _persona(
    *,
    style_prompt: str = "You are calm and curious.",
    tone: str = "warm",
) -> ModeratorPersona:
    return ModeratorPersona(
        style_prompt=style_prompt,
        tone=tone,  # type: ignore[arg-type]
        voice_id="sonic-en-us-1",
    )


class TestPromptShapeMatchesBrief:
    def test_minimal_prompt_for_untargeted_redirect(self) -> None:
        # The minimum-payload case — no target, no speaker quote, no
        # engagement note. Pins what every field is when nothing is set.
        prompt = build_prompt(
            "redirect_topic",
            _persona(style_prompt="Calm moderator."),
            PhrasingContext(),
        )
        assert prompt == {
            "system": (
                "Calm moderator. "
                "You are the moderator of a research conversation. "
                "You speak rarely and briefly. "
                "You never introduce new topics or opinions. "
                "You phrase exactly the intervention specified, "
                "in one sentence, no preamble."
            ),
            "user": {
                "intervention": "redirect_topic",
                "constraints": {
                    "max_sentences": 1,
                    "address_target_by_name": False,
                    "tone": "warm, gentle but clear",
                },
            },
        }

    def test_full_prompt_for_targeted_prompt_participant(self) -> None:
        # The dense case — every optional field present. Mirrors the
        # brief §8.2 example structure (intervention + constraints +
        # target_name + context with quote / minutes / engagement).
        prompt = build_prompt(
            "prompt_participant",
            _persona(style_prompt="Calm moderator.", tone="warm"),
            PhrasingContext(
                target_display_name="Alice",
                target_last_contribution_minutes_ago=4.5,
                target_engagement_note="has been actively listening",
                last_speaker_quote="I think we should focus on onboarding.",
            ),
        )
        assert prompt["user"] == {
            "intervention": "prompt_participant",
            "constraints": {
                "max_sentences": 1,
                "address_target_by_name": True,
                "tone": "warm, inviting, not interrogative",
            },
            "target_name": "Alice",
            "context": {
                "last_speaker_quote": "I think we should focus on onboarding.",
                "target_last_contribution_minutes_ago": 4.5,
                "target_engagement_note": "has been actively listening",
            },
        }


class TestPromptOmitsAbsentFields:
    def test_no_context_block_when_all_context_fields_none(self) -> None:
        # An untargeted summarize_thread with a silent room — the
        # `context` key is omitted entirely so the LLM doesn't see
        # `null`s that imply we considered the field and chose nothing.
        prompt = build_prompt(
            "summarize_thread",
            _persona(),
            PhrasingContext(),
        )
        assert "context" not in prompt["user"]
        assert "target_name" not in prompt["user"]

    def test_target_name_omitted_when_no_display_name(self) -> None:
        prompt = build_prompt(
            "redirect_topic",
            _persona(),
            PhrasingContext(last_speaker_quote="Let's pivot."),
        )
        assert "target_name" not in prompt["user"]
        assert prompt["user"]["constraints"]["address_target_by_name"] is False
        assert prompt["user"]["context"] == {"last_speaker_quote": "Let's pivot."}

    def test_address_by_name_true_only_when_target_present(self) -> None:
        with_target = build_prompt(
            "prompt_participant",
            _persona(),
            PhrasingContext(target_display_name="Alice"),
        )
        assert with_target["user"]["constraints"]["address_target_by_name"] is True

        without_target = build_prompt(
            "prompt_participant",
            _persona(),
            PhrasingContext(),
        )
        assert without_target["user"]["constraints"]["address_target_by_name"] is False


class TestActionToneHints:
    def test_each_spoken_action_has_a_tone_hint_appended(self) -> None:
        persona = _persona(tone="neutral")
        expected_tones: dict[DecisionAction, str] = {
            "prompt_participant": "neutral, inviting, not interrogative",
            "redirect_topic": "neutral, gentle but clear",
            "summarize_thread": "neutral, neutral, recapping",
            "request_clarification": "neutral, curious",
            "suggest_turn_taking": "neutral, neutral, brief",
        }
        for action, expected in expected_tones.items():
            prompt = build_prompt(action, persona, PhrasingContext())
            assert prompt["user"]["constraints"]["tone"] == expected, action

    def test_persona_tone_is_the_prefix(self) -> None:
        # The persona tone always leads — per-action hint is the suffix
        # so a researcher reading the prompt sees the study's voice first.
        prompt = build_prompt(
            "prompt_participant",
            _persona(tone="professional"),
            PhrasingContext(),
        )
        assert prompt["user"]["constraints"]["tone"].startswith("professional, ")


class TestSystemMessageComposition:
    def test_system_message_prefixes_persona_style_prompt(self) -> None:
        prompt = build_prompt(
            "redirect_topic",
            _persona(style_prompt="UNIQUE_STYLE_TOKEN."),
            PhrasingContext(),
        )
        assert prompt["system"].startswith("UNIQUE_STYLE_TOKEN. ")

    def test_system_message_carries_persona_neutral_suffix(self) -> None:
        # The §8.2 mandated suffix is invariant — every prompt carries
        # the "speak rarely, no preamble" guardrails regardless of persona.
        prompt = build_prompt(
            "redirect_topic",
            _persona(),
            PhrasingContext(),
        )
        assert "You speak rarely and briefly." in prompt["system"]
        assert "in one sentence, no preamble." in prompt["system"]


class TestRejectsNonSpokenActions:
    def test_stay_silent_raises(self) -> None:
        # The orchestrator must never call the mouth for stay_silent.
        # If it does, the loud failure is what surfaces the routing bug
        # instead of a wasted LLM round-trip.
        with pytest.raises(ValueError, match="stay_silent"):
            build_prompt("stay_silent", _persona(), PhrasingContext())

    def test_close_session_raises(self) -> None:
        with pytest.raises(ValueError, match="close_session"):
            build_prompt("close_session", _persona(), PhrasingContext())
