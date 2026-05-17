"""Per-action fallback phrasings (brief §8.4).

When the LLM call exceeds the 800 ms wall-clock budget or returns an
error, the mouth falls back to a pre-written, persona-neutral
phrasing. Templates are deliberately bland — they're a real product
shape (one valid intervention), not a panic exit, so the floor on
quality matters.

Templates exist for the five spoken action variants. `stay_silent`
never reaches the mouth at all (the orchestrator gates this), and
`close_session` is handled by the session lifecycle layer (Phase 5),
not phrased aloud.
"""

from __future__ import annotations

from typing import Final

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.mouth.context import PhrasingContext


class NoFallbackTemplateError(ValueError):
    """Raised when an action has no fallback template defined."""


# Two phrasings per action — one when we know the target's name, one
# without. The without-name variant is also the default for untargeted
# actions (redirect, summarize, turn-taking).
_TEMPLATES: Final[dict[DecisionAction, tuple[str, str]]] = {
    "prompt_participant": (
        "{target_display_name}, I'd love to hear your thoughts on this.",
        "I'd love to hear another perspective on this.",
    ),
    "redirect_topic": (
        "Thanks — let's bring it back to the original question.",
        "Thanks — let's bring it back to the original question.",
    ),
    "summarize_thread": (
        "Let me pause us briefly to make sure I'm following the through-line.",
        "Let me pause us briefly to make sure I'm following the through-line.",
    ),
    "request_clarification": (
        "{target_display_name}, could you say a bit more about what you mean?",
        "Could whoever is holding that thread say a bit more about what they mean?",
    ),
    "suggest_turn_taking": (
        "Let's give one voice at a time — go ahead and finish your thought.",
        "Let's give one voice at a time — go ahead and finish your thought.",
    ),
}


def format_template(action: DecisionAction, context: PhrasingContext) -> str:
    """Return the fallback phrasing for `action`, formatted with `context`.

    Raises:
        NoFallbackTemplateError: for actions that never reach the mouth
            (`stay_silent`) or are handled by another layer
            (`close_session`).
    """
    if action == "stay_silent":
        raise NoFallbackTemplateError("stay_silent never reaches the mouth layer")
    if action == "close_session":
        raise NoFallbackTemplateError(
            "close_session is handled by the session lifecycle, not the mouth"
        )
    if action not in _TEMPLATES:
        raise NoFallbackTemplateError(f"no fallback template for action={action!r}")

    with_target, without_target = _TEMPLATES[action]
    if context.target_display_name is not None and "{target_display_name}" in with_target:
        return with_target.format(target_display_name=context.target_display_name)
    return without_target
