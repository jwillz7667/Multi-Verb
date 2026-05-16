"""`silence_gap` rule — brief §7.2 rule #1.

Trigger: no one has spoken (and no VAD activity) for ≥ `threshold_sec`.
Action: `prompt_participant`, targeting the least-recently-active joiner —
the heuristic being that the longest-silent person is the most likely
candidate to have something to say but be reluctant to break the quiet.

Why this rule exists (brief §2 / product principle #1): silence is the
moderator's *default*; this rule earns the right to break it only after
a substantial pause. The threshold is deliberately conservative — 8s
without speech in a group conversation is unusual enough to warrant a
gentle prompt without being intrusive.

Tie-breaking when multiple participants share the longest-silent time
(e.g. session just started and nobody has spoken): the predicate
falls back to participant_id alphabetical order so two runs of the
same session produce identical audit rows. Without a stable tiebreak,
the choice would be Python dict-iteration-order, which is
insertion-order and therefore depends on the event sequence — fine for
correctness but bad for replay.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat

from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class SilenceGapConfig(BaseModel):
    """Per-study tunable for `silence_gap`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    threshold_sec: NonNegativeFloat = Field(
        default=8.0,
        description="Seconds of continuous silence (no STT, no VAD) before firing.",
    )


class SilenceGapRule:
    """Fires when `state.silence_run_sec >= threshold_sec` and nobody's
    VAD is active.

    `state.silence_run_sec` is maintained by the state store (brief §5.1
    / §6); we don't re-derive it here.
    """

    name = "silence_gap"
    version = "v1.0"
    # Mid-range priority: silence is important but a participant being
    # ignored (unheard_participant) or running out of time
    # (time_remaining_pressure) wins ties.
    priority = 50
    default_cooldown_sec = 45.0

    def __init__(self, config: SilenceGapConfig | None = None) -> None:
        self._config = config if config is not None else SilenceGapConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> SilenceGapRule:
        """Build the rule from a snapshotted `RulesConfig` (brief §7.5).

        Missing entries → defaults. The per-rule dict is validated
        through Pydantic so a malformed override (e.g. a typo in
        `threshold_sec`) fails loud at session start instead of
        silently using the default mid-session.
        """
        raw = rules_config.rules.get(cls.name, {})
        return cls(SilenceGapConfig.model_validate(raw))

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        threshold = self._config.threshold_sec
        any_vad = any(p.vad_active for p in state.participants.values())
        silence = state.silence_run_sec
        # The least-recently-active participant — the prompt target.
        # Participants with `last_spoke_at == None` (haven't spoken yet)
        # are *more* silent than anyone who has spoken, so they sort
        # ahead of everyone with a real timestamp. Within that group we
        # tiebreak by participant_id (stable across replays).
        target_id: str | None = _least_recently_active(state)

        inputs = {
            "silence_run_sec": silence,
            "threshold_sec": threshold,
            "any_vad_active": any_vad,
            "least_recently_active_participant_id": target_id,
        }

        if any_vad or silence < threshold or target_id is None:
            return RulePredicateResult(
                fired=False,
                confidence=0.0,
                target_participant_id=None,
                reason_codes=[],
                inputs_snapshot=inputs,
                proposed_action="prompt_participant",
            )

        # Confidence climbs from 0 at the threshold to 1.0 at 2x threshold.
        # Saturating early keeps the dashboard's confidence meter
        # legible — past 2x we're already firmly inside "long silence".
        confidence = min(1.0, (silence - threshold) / threshold) if threshold > 0 else 1.0

        # `silence_gap_<int_seconds>s` is a stable structured code; the
        # int floor avoids the cardinality explosion of writing
        # `silence_gap_8.137s` into the audit log.
        reason_code = f"silence_gap_{int(silence)}s"

        return RulePredicateResult(
            fired=True,
            confidence=max(confidence, 0.05),  # never zero when firing
            target_participant_id=target_id,
            reason_codes=[reason_code],
            inputs_snapshot=inputs,
            proposed_action="prompt_participant",
        )


def _least_recently_active(state: SessionState) -> str | None:
    """Return the `participant_id` of the longest-silent participant.

    Rules:
      * Never-spoken participants (last_spoke_at is None) are
        considered infinitely silent, so they win over anyone who
        has spoken. Among never-spokens, tie-break by participant_id
        ascending.
      * Among participants who have spoken, the earliest
        last_spoke_at wins; tie-break by participant_id ascending.
      * Returns None iff there are no participants.
    """
    if not state.participants:
        return None

    # Never-spoken pool wins outright if non-empty.
    never_spoken = [pid for pid, p in state.participants.items() if p.last_spoke_at is None]
    if never_spoken:
        return min(never_spoken)

    # Otherwise the participant with the smallest last_spoke_at.
    by_age: list[tuple[datetime, str]] = [
        (p.last_spoke_at, pid)
        for pid, p in state.participants.items()
        if p.last_spoke_at is not None
    ]
    earliest_ts = min(ts for ts, _ in by_age)
    candidates = sorted(pid for ts, pid in by_age if ts == earliest_ts)
    return candidates[0]
