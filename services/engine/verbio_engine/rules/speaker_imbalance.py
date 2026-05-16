"""`speaker_imbalance` rule — brief §7.2 rule #2.

Trigger: in the last 5 minutes, at least one participant has spoken
≥ `over_share_factor` x their fair share AND another has spoken
≤ `under_share_factor` x their fair share. Action: `prompt_participant`
targeting the most under-share person — pull them into the conversation
instead of scolding the over-share person.

Fair share is computed by the state store at session start (and as
participants join/leave): `fair_share_pct = 100 / len(participants)`.
Actual share is a rolling 5-min ratio of speaking time. Both already
live on `ParticipantState`; this rule is pure-state.

We require ≥ 3 participants before firing. With 2 people, "imbalance"
is mathematically forced once anyone speaks for more than half the
window — and the human reading is "they're having a normal asymmetric
conversation", not "this is a moderation failure".

Never-spoken participants are excluded from the under-share candidate
pool — `silence_gap` and `unheard_participant` cover that case. We're
about the dynamic where someone *was* in the conversation but the floor
has tilted away from them.

Priority is 45 — between `cross_talk_pattern` (40, soft) and
`silence_gap` (50). An imbalanced floor matters but is less acute
than total quiet or someone being talked over.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveFloat

from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class SpeakerImbalanceConfig(BaseModel):
    """Per-study tunable for `speaker_imbalance`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    over_share_factor: PositiveFloat = Field(
        default=2.0,
        description=(
            "Multiplier on fair_share_pct that counts as over-sharing. "
            "Brief default 2.0 — speaking twice your share over a 5-min "
            "window is a meaningful skew."
        ),
    )
    under_share_factor: PositiveFloat = Field(
        default=0.4,
        description=(
            "Multiplier on fair_share_pct that counts as under-sharing. "
            "Brief default 0.4 — speaking less than 40 percent of your "
            "share is a meaningful absence. PositiveFloat (not "
            "NonNegativeFloat) so 0 doesn't accidentally re-enable "
            "never-spoken participants."
        ),
    )
    min_participants: NonNegativeInt = Field(
        default=3,
        description=(
            "Minimum participant count before the rule will fire. "
            "Below this, asymmetric speaking is normal conversation, "
            "not a moderation signal."
        ),
    )


class SpeakerImbalanceRule:
    """Fires when one participant over-shares and another under-shares the floor."""

    name = "speaker_imbalance"
    version = "v1.0"
    # Between cross_talk_pattern (40) and silence_gap (50). Lopsided
    # speaking is annoying but not as acute as collective quiet or
    # someone being interrupted out of the conversation.
    priority = 45
    default_cooldown_sec = 90.0

    def __init__(self, config: SpeakerImbalanceConfig | None = None) -> None:
        self._config = config if config is not None else SpeakerImbalanceConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> SpeakerImbalanceRule:
        raw = rules_config.rules.get(cls.name, {})
        return cls(SpeakerImbalanceConfig.model_validate(raw))

    def predicate(
        self,
        state: SessionState,
        t: datetime,
    ) -> RulePredicateResult:
        cfg = self._config
        participants = state.participants

        inputs: dict[str, object] = {
            "participant_count": len(participants),
            "min_participants": cfg.min_participants,
            "over_share_factor": cfg.over_share_factor,
            "under_share_factor": cfg.under_share_factor,
        }

        if len(participants) < cfg.min_participants:
            return _stay(inputs)

        over_sharers: list[tuple[float, str]] = []
        under_sharers: list[tuple[float, str]] = []

        for pid, p in participants.items():
            fair = p.fair_share_pct
            if fair <= 0:
                # No fair share assigned yet (state store hasn't computed
                # it, or this participant is invisible to the metric).
                # Skip — we can't reason about over/under without a baseline.
                continue
            actual = p.actual_share_last_5min_pct
            ratio = actual / fair

            if ratio >= cfg.over_share_factor:
                over_sharers.append((ratio, pid))
            elif ratio <= cfg.under_share_factor and p.last_spoke_at is not None:
                # `last_spoke_at is not None` excludes never-spoken
                # participants — they belong to silence_gap /
                # unheard_participant, not here.
                under_sharers.append((ratio, pid))

        inputs["over_sharer_ids"] = sorted(pid for _, pid in over_sharers)
        inputs["under_sharer_ids"] = sorted(pid for _, pid in under_sharers)

        if not over_sharers or not under_sharers:
            return _stay(inputs)

        # Pick the most under-share person; tie-break alphabetically
        # for deterministic replay.
        under_sharers.sort(key=lambda pair: (pair[0], pair[1]))
        under_ratio, target_id = under_sharers[0]

        # Pick the worst over-sharer for the audit trail and the
        # confidence calculation. Same deterministic ordering.
        over_sharers.sort(key=lambda pair: (-pair[0], pair[1]))
        over_ratio, over_id = over_sharers[0]

        # Confidence climbs with how lopsided the floor is. Anchor on
        # how far the over-sharer is past their factor — that's the
        # easier dimension to reason about as a tuning knob.
        # Saturates at 2x over_share_factor (so 4x fair share = 1.0).
        over_excess = over_ratio - cfg.over_share_factor
        confidence = (
            min(1.0, over_excess / cfg.over_share_factor) if cfg.over_share_factor > 0 else 1.0
        )
        confidence = max(confidence, 0.15)  # floor so a freshly-fired rule isn't 0.0

        reason_code = f"speaker_imbalance_{target_id}_under_{over_id}_over"

        inputs.update(
            {
                "target_participant_id": target_id,
                "target_share_ratio": under_ratio,
                "over_sharer_id": over_id,
                "over_share_ratio": over_ratio,
            }
        )

        return RulePredicateResult(
            fired=True,
            confidence=confidence,
            target_participant_id=target_id,
            reason_codes=[reason_code],
            inputs_snapshot=inputs,
            proposed_action="prompt_participant",
        )


def _stay(inputs: dict[str, object]) -> RulePredicateResult:
    return RulePredicateResult(
        fired=False,
        confidence=0.0,
        target_participant_id=None,
        reason_codes=[],
        inputs_snapshot=inputs,
        proposed_action="prompt_participant",
    )
