"""`build_prompt` — pure function that produces the §8.2 mouth-layer JSON.

Returns a dict matching brief §8.2 exactly. Deterministic, no I/O,
trivially testable. The mouth implementations call this and forward
the result to whichever LLM provider they're configured for.

`stay_silent` and `close_session` are rejected: the orchestrator must
never invoke the mouth for them.
"""

from __future__ import annotations

from typing import Any

from verbio_engine.domain.decision import DecisionAction, ModeratorDecision
from verbio_engine.mouth.context import PhrasingContext
from verbio_engine.mouth.persona import ModeratorPersona

# Persona-neutral system suffix mandated by brief §8.2. Concatenated
# to whatever persona-specific `style_prompt` the study sets.
_SYSTEM_SUFFIX = (
    "You are the moderator of a research conversation. "
    "You speak rarely and briefly. "
    "You never introduce new topics or opinions. "
    "You phrase exactly the intervention specified, in one sentence, no preamble."
)

# Per-action tone hint appended to the persona's base tone. Picked to
# match the brief §8.2 example ("warm, inviting, not interrogative") and
# the documented action semantics in §5.2.
_ACTION_TONE: dict[DecisionAction, str] = {
    "prompt_participant": "inviting, not interrogative",
    "redirect_topic": "gentle but clear",
    "summarize_thread": "neutral, recapping",
    "request_clarification": "curious",
    "suggest_turn_taking": "neutral, brief",
}


def build_prompt(
    decision: ModeratorDecision,
    persona: ModeratorPersona,
    context: PhrasingContext,
) -> dict[str, Any]:
    """Return the §8.2 prompt JSON for `decision`.

    Raises:
        ValueError: when `decision.action` is `stay_silent` or
            `close_session` — neither reaches the mouth.
    """
    if decision.action in ("stay_silent", "close_session"):
        raise ValueError(
            f"build_prompt invoked with action={decision.action!r}; "
            "the mouth layer is not invoked for stay_silent or close_session"
        )

    context_payload: dict[str, Any] = {}
    if context.last_speaker_quote is not None:
        context_payload["last_speaker_quote"] = context.last_speaker_quote
    if context.target_last_contribution_minutes_ago is not None:
        context_payload["target_last_contribution_minutes_ago"] = (
            context.target_last_contribution_minutes_ago
        )
    if context.target_engagement_note is not None:
        context_payload["target_engagement_note"] = context.target_engagement_note

    user_payload: dict[str, Any] = {
        "intervention": decision.action,
        "constraints": {
            "max_sentences": 1,
            "address_target_by_name": context.target_display_name is not None,
            "tone": _tone_descriptor(persona, decision.action),
        },
    }
    if context.target_display_name is not None:
        user_payload["target_name"] = context.target_display_name
    if context_payload:
        user_payload["context"] = context_payload

    return {
        "system": f"{persona.style_prompt} {_SYSTEM_SUFFIX}",
        "user": user_payload,
    }


def _tone_descriptor(persona: ModeratorPersona, action: DecisionAction) -> str:
    """Combine the persona's base tone with the per-action hint."""
    action_hint = _ACTION_TONE.get(action, "neutral")
    return f"{persona.tone}, {action_hint}"
