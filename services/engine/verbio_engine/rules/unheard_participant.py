"""`unheard_participant` rule — brief §7.2 rule #5.

Trigger: a participant has been silent for ≥ N minutes AND shows
engagement signals (backchannels > 0 OR was-interrupted in the recent
window). Targets them with a `prompt_participant` action.

The engagement gate matters: a participant who has truly disengaged
(no backchannels, never interrupted, no signals at all) is not someone
the moderator should pull back in — they might be observing on purpose,
or simply checked out. We only fire for participants who *want* to
contribute but haven't managed to break in.

Priority is intentionally higher than `silence_gap` (60 vs 50): being
talked over is a worse moderation failure than collective quiet. When
both fire, "lift up the unheard person" wins over "fill the silence".

Tie-break on multiple candidates: pick the one silent longest, then
participant_id ascending — deterministic for replay.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, NonNegativeInt

from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.participant import ParticipantState
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class UnheardParticipantConfig(BaseModel):
    """Per-study tunable for `unheard_participant`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    silence_threshold_min: NonNegativeFloat = Field(
        default=4.0,
        description="Minutes since last_spoke_at before a participant qualifies.",
    )
    min_backchannels: NonNegativeInt = Field(
        default=1,
        description=(
            "Threshold on backchannel_count_last_2min that counts as "
            "engagement. Default 1 — a single nod-equivalent is enough."
        ),
    )
    min_was_interrupted: NonNegativeInt = Field(
        default=1,
        description=(
            "Threshold on was_interrupted_count that counts as engagement. "
            "Tried to speak and got cut off ≥ this many times."
        ),
    )


class UnheardParticipantRule:
    """Fires for the longest-silent engaged participant beyond threshold."""

    name = "unheard_participant"
    version = "v1.0"
    # Higher than silence_gap (50): a person trying-but-not-managing-to-speak
    # is a more urgent moderation failure than collective silence.
    priority = 60
    default_cooldown_sec = 90.0

    def __init__(self, config: UnheardParticipantConfig | None = None) -> None:
        self._config = config if config is not None else UnheardParticipantConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> UnheardParticipantRule:
        raw = rules_config.rules.get(cls.name, {})
        return cls(UnheardParticipantConfig.model_validate(raw))

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        cfg = self._config
        threshold = timedelta(minutes=cfg.silence_threshold_min)

        candidates: list[tuple[float, str]] = []
        for pid, p in state.participants.items():
            silent_for = _silent_for(p, t)
            if silent_for is None or silent_for < threshold.total_seconds():
                continue
            if not _is_engaged(p, cfg):
                continue
            candidates.append((silent_for, pid))

        inputs = {
            "threshold_sec": threshold.total_seconds(),
            "min_backchannels": cfg.min_backchannels,
            "min_was_interrupted": cfg.min_was_interrupted,
            "candidate_count": len(candidates),
        }

        if not candidates:
            return RulePredicateResult(
                fired=False,
                confidence=0.0,
                target_participant_id=None,
                reason_codes=[],
                inputs_snapshot=inputs,
                proposed_action="prompt_participant",
            )

        # Sort by (-silent_for, pid) to get longest-silent first, then
        # alphabetical participant_id for a stable tie-break.
        candidates.sort(key=lambda pair: (-pair[0], pair[1]))
        silent_for, target_id = candidates[0]
        silent_min = silent_for / 60.0

        # Confidence: 0 at threshold, 1.0 at 2x threshold. Same shape
        # as `silence_gap` so the dashboard's bar reads consistently.
        threshold_sec = threshold.total_seconds()
        confidence = (
            min(1.0, (silent_for - threshold_sec) / threshold_sec) if threshold_sec > 0 else 1.0
        )
        confidence = max(confidence, 0.1)  # never zero when firing

        reason_code = f"{target_id}_unheard_{int(silent_min)}min"

        return RulePredicateResult(
            fired=True,
            confidence=confidence,
            target_participant_id=target_id,
            reason_codes=[reason_code],
            inputs_snapshot={
                **inputs,
                "target_participant_id": target_id,
                "target_silent_for_sec": silent_for,
            },
            proposed_action="prompt_participant",
        )


def _silent_for(participant: ParticipantState, t: datetime) -> float | None:
    """Seconds since `last_spoke_at`. None if the participant has never spoken.

    Never-spoken participants are excluded from this rule by design —
    they have no engagement signal yet (just joined / silent observer).
    `silence_gap` covers the 'whole room is quiet' case; this rule is
    about *known engaged* participants going unheard.
    """
    if participant.last_spoke_at is None:
        return None
    delta = t - participant.last_spoke_at
    return max(0.0, delta.total_seconds())


def _is_engaged(participant: ParticipantState, cfg: UnheardParticipantConfig) -> bool:
    """Engagement gate: at least one positive signal in the recent window."""
    return (
        participant.backchannel_count_last_2min >= cfg.min_backchannels
        or participant.was_interrupted_count >= cfg.min_was_interrupted
    )
