"""`format_template` — the §8.4 fallback path for every spoken action.

Each spoken action must have a template (the LLM call can always fail).
`stay_silent` and `close_session` must raise — they have no business
reaching the mouth and the loud failure flushes out routing bugs early.
"""

from __future__ import annotations

import pytest

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.templates import NoFallbackTemplateError, format_template

SPOKEN_ACTIONS: tuple[DecisionAction, ...] = (
    "prompt_participant",
    "redirect_topic",
    "summarize_thread",
    "request_clarification",
    "suggest_turn_taking",
)


@pytest.mark.parametrize("action", SPOKEN_ACTIONS)
def test_every_spoken_action_has_a_no_target_template(action: DecisionAction) -> None:
    # The untargeted variant is always defined, so e.g. `redirect_topic`
    # works even when we never resolved a participant to address.
    text = format_template(action, PhrasingContext())
    assert isinstance(text, str)
    assert len(text) > 0
    # Verify no unsubstituted placeholder leaks into spoken output.
    assert "{target_display_name}" not in text


class TestPromptParticipantTemplate:
    def test_uses_targeted_phrasing_when_name_present(self) -> None:
        ctx = PhrasingContext(target_display_name="Alice")
        assert format_template("prompt_participant", ctx) == (
            "Alice, I'd love to hear your thoughts on this."
        )

    def test_uses_untargeted_phrasing_when_no_name(self) -> None:
        assert format_template("prompt_participant", PhrasingContext()) == (
            "I'd love to hear another perspective on this."
        )


class TestRequestClarificationTemplate:
    def test_uses_targeted_phrasing_when_name_present(self) -> None:
        ctx = PhrasingContext(target_display_name="Bob")
        assert format_template("request_clarification", ctx) == (
            "Bob, could you say a bit more about what you mean?"
        )

    def test_uses_untargeted_phrasing_when_no_name(self) -> None:
        assert format_template("request_clarification", PhrasingContext()) == (
            "Could whoever is holding that thread say a bit more about what they mean?"
        )


class TestStableUntargetedActions:
    # redirect_topic / summarize_thread / suggest_turn_taking are
    # untargeted by design — having a name available shouldn't change
    # the phrasing (no placeholder in the with-target variant).

    def test_redirect_topic_ignores_target_name(self) -> None:
        expected = "Thanks — let's bring it back to the original question."
        assert format_template("redirect_topic", PhrasingContext()) == expected
        assert (
            format_template(
                "redirect_topic",
                PhrasingContext(target_display_name="Alice"),
            )
            == expected
        )

    def test_summarize_thread_ignores_target_name(self) -> None:
        expected = "Let me pause us briefly to make sure I'm following the through-line."
        assert format_template("summarize_thread", PhrasingContext()) == expected
        assert (
            format_template(
                "summarize_thread",
                PhrasingContext(target_display_name="Alice"),
            )
            == expected
        )

    def test_suggest_turn_taking_ignores_target_name(self) -> None:
        expected = "Let's give one voice at a time — go ahead and finish your thought."
        assert format_template("suggest_turn_taking", PhrasingContext()) == expected
        assert (
            format_template(
                "suggest_turn_taking",
                PhrasingContext(target_display_name="Alice"),
            )
            == expected
        )


class TestUnspokenActionsRaise:
    def test_stay_silent_raises(self) -> None:
        # If the orchestrator ever invokes the mouth for stay_silent,
        # we want a loud failure — silence has no fallback by design.
        with pytest.raises(NoFallbackTemplateError, match="stay_silent"):
            format_template("stay_silent", PhrasingContext())

    def test_close_session_raises(self) -> None:
        # close_session is a lifecycle event handled in Phase 5, not a
        # spoken intervention — distinct error message so the mismatch
        # is obvious in the logs.
        with pytest.raises(NoFallbackTemplateError, match="close_session"):
            format_template("close_session", PhrasingContext())
