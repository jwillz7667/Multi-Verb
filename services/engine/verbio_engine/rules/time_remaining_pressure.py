"""`time_remaining_pressure` rule — brief §7.2 rule #7.

Trigger: fraction of remaining scheduled session time falls below
`min_remaining_pct` of the originally-scheduled session duration.
Action: `redirect_topic` (a global nudge — there is no single
participant to address when the whole group is running out of time).

V1 simplified scope: the brief's full spec ANDs the time-pressure
signal with "the study prompt has unaddressed sub-questions". Detecting
unaddressed sub-questions cleanly requires either a structured prompt
schema (sub-questions as discrete items) or an LLM topic-coverage
analysis pass — neither exists yet in Phase 3. We ship the time gate
alone; the mouth-layer prompt template (Phase 4) can lean on the
study_prompt verbatim to phrase a "we have N minutes left, anything
on the prompt we haven't covered?" redirect. A later layer can tighten
the predicate by ANDing a topic-coverage signal.

Guarded states — all map to "don't fire":

  * `scheduled_end_at is None`. Open-ended sessions don't have a
    pressure curve; the rule is silent.
  * `scheduled_end_at <= started_at`. Zero or negative duration is a
    config bug; refuse to fabricate a "100% pressure" signal off it.

Priority 70 — above `unheard_participant` (60). The silence_gap
docstring already notes that this rule wins ties: a session running
out of time is the most acute "wrap things up" pressure, more urgent
than any per-person nudge.

Cooldown 240s. Long because the moderator only earns a wrap-up nudge
a few times per session — firing every minute would feel nagging.

Confidence: linear in how far past the threshold the remaining
fraction has slipped. At the threshold confidence is 0; at zero
seconds remaining it is 1.0; past the end it saturates at 1.0.
Floored at 0.20 when firing so a freshly-triggered rule is visible
on the dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class TimeRemainingPressureConfig(BaseModel):
    """Per-study tunable for `time_remaining_pressure`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_remaining_pct: float = Field(
        default=0.10,
        gt=0.0,
        le=1.0,
        description=(
            "Fraction of the originally-scheduled session duration that "
            "must remain before the rule stops firing. Brief default 0.10 "
            "(fire when less than 10% of the schedule is left). Must be "
            "strictly positive — a 0% threshold would never fire."
        ),
    )


class TimeRemainingPressureRule:
    """Fires when remaining_pct < min_remaining_pct of scheduled duration."""

    name = "time_remaining_pressure"
    version = "v1.0"
    # Highest of the v1 set: "we're running out of time" beats any
    # per-person nudge. Silence_gap's docstring already promises this
    # rule wins ties; keep it consistent.
    priority = 70
    default_cooldown_sec = 240.0

    def __init__(self, config: TimeRemainingPressureConfig | None = None) -> None:
        self._config = config if config is not None else TimeRemainingPressureConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> TimeRemainingPressureRule:
        raw = rules_config.rules.get(cls.name, {})
        return cls(TimeRemainingPressureConfig.model_validate(raw))

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        cfg = self._config
        threshold = cfg.min_remaining_pct
        scheduled_end_at = state.scheduled_end_at

        inputs: dict[str, object] = {
            "min_remaining_pct": threshold,
            "has_scheduled_end": scheduled_end_at is not None,
        }

        if scheduled_end_at is None:
            return _stay(inputs)

        total_duration_sec = (scheduled_end_at - state.started_at).total_seconds()
        inputs["total_duration_sec"] = total_duration_sec
        if total_duration_sec <= 0.0:
            # Bogus schedule (end at or before start). Refuse to invent
            # a pressure signal; flag it in the audit row so the bad
            # config is visible in replay.
            inputs["invalid_schedule"] = True
            return _stay(inputs)

        remaining_sec = (scheduled_end_at - t).total_seconds()
        remaining_pct = remaining_sec / total_duration_sec
        inputs["remaining_sec"] = remaining_sec
        inputs["remaining_pct"] = remaining_pct
        inputs["is_past_end"] = remaining_sec < 0.0

        if remaining_pct >= threshold:
            return _stay(inputs)

        # Linear ramp: at threshold → 0; at 0 remaining → 1; past end → 1.
        # `threshold > 0` (config-enforced) so the divide is safe.
        confidence = (threshold - remaining_pct) / threshold
        confidence = max(0.0, min(1.0, confidence))
        confidence = max(confidence, 0.20)  # floor so the dashboard shows it

        # `int(remaining_pct * 100)` is a stable, low-cardinality code.
        # Past-end cases produce negative values (e.g. `time_remaining_pct_-3`)
        # which is what the audit log should preserve — silently clamping
        # to 0 would hide that the session ran over.
        reason_code = f"time_remaining_pct_{int(remaining_pct * 100)}"

        return RulePredicateResult(
            fired=True,
            confidence=confidence,
            target_participant_id=None,
            reason_codes=[reason_code],
            inputs_snapshot=inputs,
            proposed_action="redirect_topic",
        )


def _stay(inputs: dict[str, object]) -> RulePredicateResult:
    return RulePredicateResult(
        fired=False,
        confidence=0.0,
        target_participant_id=None,
        reason_codes=[],
        inputs_snapshot=inputs,
        proposed_action="redirect_topic",
    )
