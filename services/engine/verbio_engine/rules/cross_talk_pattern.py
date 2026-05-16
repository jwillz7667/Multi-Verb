"""`cross_talk_pattern` rule — brief §7.2 rule #4.

Trigger: cumulative interruptions across all participants in the last
2 minutes ≥ `min_interruptions`. Action: `suggest_turn_taking`, with no
specific target — the intervention is a group nudge, not a callout.

We use each participant's `interruption_count` (times *they* cut someone
off) as the per-participant counter. The state store keeps these as
rolling values over the brief's 2-min window (see ParticipantState
field comments in §5.1). Summing them gives the total cross-talk
volume in that window.

We do NOT also sum `was_interrupted_count`: that would double-count
each interruption event (one cutter, one cuttee). Total = sum(cutter
counts) keeps the math clean.

Lower priority (40) than silence_gap (50) and unheard_participant (60).
Cross-talk is annoying but rarely an emergency — a few interruptions
indicate engagement; the rule only flags sustained patterns.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class CrossTalkPatternConfig(BaseModel):
    """Per-study tunable for `cross_talk_pattern`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_interruptions: PositiveInt = Field(
        default=3,
        description=(
            "Cumulative interruption events across all participants "
            "in the last 2 minutes that triggers the rule."
        ),
    )


class CrossTalkPatternRule:
    """Fires when sum(interruption_count) over participants ≥ threshold."""

    name = "cross_talk_pattern"
    version = "v1.0"
    # Below silence_gap/unheard_participant — cross-talk is a softer
    # issue than silence or someone being talked over.
    priority = 40
    default_cooldown_sec = 180.0

    def __init__(self, config: CrossTalkPatternConfig | None = None) -> None:
        self._config = config if config is not None else CrossTalkPatternConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> CrossTalkPatternRule:
        raw = rules_config.rules.get(cls.name, {})
        return cls(CrossTalkPatternConfig.model_validate(raw))

    def predicate(
        self,
        state: SessionState,
        t: datetime,
    ) -> RulePredicateResult:
        per_participant_counts = {
            pid: p.interruption_count for pid, p in state.participants.items()
        }
        total_interruptions = sum(per_participant_counts.values())
        threshold = self._config.min_interruptions

        inputs = {
            "total_interruptions_last_2min": total_interruptions,
            "min_interruptions": threshold,
            "per_participant_interruption_counts": per_participant_counts,
        }

        if total_interruptions < threshold:
            return RulePredicateResult(
                fired=False,
                confidence=0.0,
                target_participant_id=None,
                reason_codes=[],
                inputs_snapshot=inputs,
                proposed_action="suggest_turn_taking",
            )

        # Confidence ramps from threshold to 2x threshold (e.g. 3 → 6
        # interruptions = full confidence). Beyond that it saturates;
        # the dashboard's bar would otherwise misrepresent "more is
        # worse" as a runaway value.
        excess = total_interruptions - threshold
        confidence = min(1.0, excess / threshold) if threshold > 0 else 1.0
        confidence = max(confidence, 0.2)  # floor so a fresh trigger isn't 0.0

        return RulePredicateResult(
            fired=True,
            confidence=confidence,
            target_participant_id=None,
            reason_codes=[f"cross_talk_{total_interruptions}_in_2min"],
            inputs_snapshot=inputs,
            proposed_action="suggest_turn_taking",
        )
