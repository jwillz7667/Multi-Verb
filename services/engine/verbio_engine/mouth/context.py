"""`PhrasingContext` — the narrow extract the mouth sees (brief §8.1).

The mouth layer MUST NOT see `SessionState`, `ParticipantState`, or the
rule evaluations — only the small slice required to phrase one already-
decided intervention. This module is that typed seam: code that hands
the mouth a `PhrasingContext` can't accidentally smuggle the full state.

`extract_phrasing_context` is the canonical projection. It lives at the
rule-decision → mouth boundary so the extraction rules stay in one
place — if a later layer needs more context, the addition is reviewed
here, not in seven scattered call sites.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat

from verbio_engine.domain.participant import ParticipantState
from verbio_engine.domain.session_state import SessionState


class PhrasingContext(BaseModel):
    """Minimal context the mouth layer needs to phrase one decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_display_name: str | None = Field(
        default=None,
        description=(
            "Human name to address; None when the action is not targeted "
            "(redirect_topic, summarize_thread, suggest_turn_taking)."
        ),
    )
    target_last_contribution_minutes_ago: NonNegativeFloat | None = Field(
        default=None,
        description=(
            "Minutes since the target last spoke; None when never spoken or "
            "untargeted. Clamped at zero for safety against tiny clock skew."
        ),
    )
    target_engagement_note: str | None = Field(
        default=None,
        description=(
            "Short descriptive phrase (e.g. 'has been actively listening'); "
            "advisory only — the LLM may ignore it."
        ),
        max_length=80,
    )
    last_speaker_quote: str | None = Field(
        default=None,
        description=(
            "Verbatim recent utterance text that triggered the decision; "
            "trimmed to 240 chars to keep prompts compact."
        ),
        max_length=240,
    )


def extract_phrasing_context(
    state: SessionState,
    *,
    target_participant_id: str | None,
    now: datetime,
) -> PhrasingContext:
    """Project `SessionState` down to the narrow `PhrasingContext`.

    Called once per spoken decision — never from inside the mouth.
    Keeps the §8.1 contract enforceable by typing alone.
    """
    target: ParticipantState | None = None
    if target_participant_id is not None:
        target = state.participants.get(target_participant_id)

    target_name: str | None = None
    minutes_ago: float | None = None
    engagement: str | None = None
    if target is not None:
        target_name = target.display_name
        if target.last_spoke_at is not None:
            delta_sec = (now - target.last_spoke_at).total_seconds()
            # Clamp at zero so a tiny clock-skew negative doesn't fail
            # Pydantic's NonNegativeFloat validator at the boundary.
            minutes_ago = max(0.0, delta_sec / 60.0)
        engagement = _engagement_note(target)

    last_quote: str | None = None
    speaker_id = _find_most_recent_speaker(state)
    if speaker_id is not None:
        speaker = state.participants[speaker_id]
        if speaker.recent_utterances:
            # Cap defensively — schema-validate would reject overflow,
            # but the slice keeps us from raising on a long utterance.
            last_quote = speaker.recent_utterances[-1].text[:240]

    return PhrasingContext(
        target_display_name=target_name,
        target_last_contribution_minutes_ago=minutes_ago,
        target_engagement_note=engagement,
        last_speaker_quote=last_quote,
    )


def _engagement_note(target: ParticipantState) -> str | None:
    """Derive a short engagement phrase from the target's signals."""
    if target.backchannel_count_last_2min > 0:
        return "has been actively listening"
    if target.flags.disengaged:
        return "appears disengaged"
    if target.was_interrupted_count > 0:
        return "was interrupted earlier"
    return None


def _find_most_recent_speaker(state: SessionState) -> str | None:
    """Return the participant id of whoever spoke most recently, or None."""
    best: tuple[datetime, str] | None = None
    for pid, participant in state.participants.items():
        last = participant.last_spoke_at
        if last is None:
            continue
        if best is None or last > best[0]:
            best = (last, pid)
    return best[1] if best is not None else None
