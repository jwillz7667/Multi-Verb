"""Decision resolver — brief §7.3 + §7.4.

One pure function per tick. Inputs: the immutable `SessionState`, the
wall-clock `t`, the rules to evaluate, and the per-rule cooldown map
(rule_name → last winning timestamp). Outputs: one `ModeratorDecision`
plus one `RuleEvaluation` per rule.

The brief's persistence invariant — every rule logs every tick whether
it fired or not — falls out of this shape: the evaluations list always
has `len(rules)` entries.

Cooldown state is externalised: the resolver reads from `cooldowns` and
the caller (`SessionRuntime`) is responsible for updating it from the
returned decision's `triggering_rule`. Keeping the resolver stateless
lets us re-run it deterministically over a historical state log during
replay.

Shadow mode (Phase 3) ramifications:
  * `was_executed` is always False — nothing reaches mouth/TTS yet.
  * `current_window_count` on the QuietnessBudget stays 0 because no
    real utterance happens, so the budget gate never fires in shadow
    mode runs. It is still wired here so Phase 4 only needs to update
    the budget on speech events; no resolver change.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from verbio_engine.domain.decision import ModeratorDecision
from verbio_engine.domain.evaluation import RuleEvaluation

if TYPE_CHECKING:
    from uuid import UUID

    from verbio_engine.domain.budget import QuietnessBudget
    from verbio_engine.domain.session_state import SessionState
    from verbio_engine.rules.protocol import Rule, RulePredicateResult


class ResolverOutput(BaseModel):
    """Per-tick output of decision resolution.

    `decision` is always present — `stay_silent` ones still get
    persisted (brief invariant: silence is auditable). `rule_evaluations`
    has exactly one row per rule passed in, in the same order, so
    callers can zip them against `registry.all()` for indexed access.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    decision: ModeratorDecision
    rule_evaluations: list[RuleEvaluation]


def resolve(
    state: SessionState,
    t: datetime,
    rules: Iterable[Rule],
    cooldowns: Mapping[str, datetime],
) -> ResolverOutput:
    """Evaluate every rule, apply cooldown + budget filters, pick a winner."""
    decision_id = uuid4()

    # Evaluate every rule first. We do this even when the budget will
    # block speech because researchers want to see "rule X *would have*
    # fired here" in the audit log on suppressed ticks too. Predicates
    # are pure functions of state, so running them is free; the cost is
    # the storage row, which we want anyway.
    rule_results: list[tuple[Rule, RulePredicateResult]] = [
        (rule, rule.predicate(state, t)) for rule in rules
    ]

    cooldown_locked = {
        rule.name: _is_cooldown_locked(rule, cooldowns.get(rule.name), t)
        for rule, _ in rule_results
    }
    budget_blocked = _budget_blocks(state.quietness_budget, t)

    eligible: list[tuple[Rule, RulePredicateResult]] = [
        (rule, res) for rule, res in rule_results if res.fired and not cooldown_locked[rule.name]
    ]

    decision: ModeratorDecision
    winner_name: str | None = None
    if not eligible:
        # Distinguish "nothing fired" (silent because no signal) from
        # "everything fired but cooldowns blocked" (silent because the
        # rules just spoke recently). The first is a clean tick; the
        # second deserves a suppressed_by entry so the dashboard's
        # "Why quiet now?" panel can answer honestly.
        suppressed: list[str] = ["cooldown"] if any(res.fired for _, res in rule_results) else []
        decision = _stay_silent(decision_id, state, t, suppressed)
    elif budget_blocked:
        decision = _stay_silent(decision_id, state, t, ["quietness_budget"])
    else:
        # Brief §7.3: sort by priority desc, then confidence desc.
        eligible.sort(key=lambda pair: (-pair[0].priority, -pair[1].confidence))
        winner_rule, winner_res = eligible[0]
        winner_name = winner_rule.name
        decision = _from_winner(decision_id, state, t, winner_rule, winner_res)

    evaluations = [
        _evaluation(
            decision_id=decision_id,
            rule=rule,
            result=res,
            cooldown_locked=cooldown_locked[rule.name],
            budget_blocked=budget_blocked,
            won=(rule.name == winner_name),
        )
        for rule, res in rule_results
    ]

    return ResolverOutput(decision=decision, rule_evaluations=evaluations)


def _is_cooldown_locked(rule: Rule, last_fired_at: datetime | None, t: datetime) -> bool:
    """True iff this rule's per-rule cooldown window has not yet elapsed.

    Cooldown counts from the most recent tick where this rule WON
    resolution; rules that fired but lost don't accrue cooldown debt.
    """
    if last_fired_at is None:
        return False
    return (t - last_fired_at).total_seconds() < rule.default_cooldown_sec


def _budget_blocks(budget: QuietnessBudget, t: datetime) -> bool:
    """True iff QuietnessBudget would block any utterance at `t`.

    Two sub-conditions per brief §7.4:
      * Hard cap on count in the rolling 10-min window.
      * Floor on seconds since the last utterance.

    We collapse both into a single "quietness_budget" suppression
    reason for the audit trail. The dashboard can show the underlying
    counter values from the SessionState snapshot if researchers want
    to know which sub-condition actually clamped this tick.
    """
    if budget.current_window_count >= budget.max_utterances_per_10min:
        return True
    if budget.last_utterance_at is not None:
        gap_sec = (t - budget.last_utterance_at).total_seconds()
        if gap_sec < budget.min_seconds_between_utterances:
            return True
    return False


def _stay_silent(
    decision_id: UUID,
    state: SessionState,
    t: datetime,
    suppressed_by: list[str],
) -> ModeratorDecision:
    """Build a stay_silent ModeratorDecision. Persisted at full fidelity."""
    return ModeratorDecision(
        decision_id=decision_id,
        session_id=state.session_id,
        tick_id=state.tick_id,
        timestamp=t,
        action="stay_silent",
        target_participant_id=None,
        source="auto",
        triggering_rule=None,
        researcher_id=None,
        researcher_hint=None,
        reason_codes=[],
        reason_human="",
        confidence=0.0,
        suppressed_by=suppressed_by,
        was_executed=False,
        llm_prompt=None,
        llm_output=None,
        tts_audio_url=None,
        spoken_at=None,
        # stay_silent has no cooldown — silence doesn't lock out future ticks.
        cooldown_until=t,
    )


def _from_winner(
    decision_id: UUID,
    state: SessionState,
    t: datetime,
    rule: Rule,
    result: RulePredicateResult,
) -> ModeratorDecision:
    """Build the ModeratorDecision for the winning rule.

    `reason_human` and the mouth-layer fields stay empty here — Phase 4
    populates them after the LLM call. The resolver's job is to commit
    "we will speak, and this is why we picked this rule" to the audit
    trail; phrasing happens downstream.
    """
    return ModeratorDecision(
        decision_id=decision_id,
        session_id=state.session_id,
        tick_id=state.tick_id,
        timestamp=t,
        action=result.proposed_action,
        target_participant_id=result.target_participant_id,
        source="auto",
        triggering_rule=rule.name,
        researcher_id=None,
        researcher_hint=None,
        reason_codes=list(result.reason_codes),
        reason_human="",
        confidence=result.confidence,
        suppressed_by=[],
        was_executed=False,
        llm_prompt=None,
        llm_output=None,
        tts_audio_url=None,
        spoken_at=None,
        cooldown_until=t + timedelta(seconds=rule.default_cooldown_sec),
    )


def _evaluation(
    *,
    decision_id: UUID,
    rule: Rule,
    result: RulePredicateResult,
    cooldown_locked: bool,
    budget_blocked: bool,
    won: bool,
) -> RuleEvaluation:
    """Build the per-rule audit row for this tick.

    Suppression-reason precedence (matches the resolver's filter order):
      1. cooldown — checked first, regardless of budget
      2. quietness_budget — global block on this tick
      3. lower_priority_won — fired and eligible but a higher rule won
      4. None — either won the tick, or didn't fire
    """
    suppressed_reason: str | None
    if not result.fired:
        suppressed_reason = None
    elif cooldown_locked:
        suppressed_reason = "cooldown"
    elif budget_blocked:
        suppressed_reason = "quietness_budget"
    elif won:
        suppressed_reason = None
    else:
        suppressed_reason = "lower_priority_won"

    return RuleEvaluation(
        evaluation_id=uuid4(),
        decision_id=decision_id,
        rule_name=rule.name,
        rule_version=rule.version,
        fired=result.fired,
        suppressed_reason=suppressed_reason,
        predicate_inputs=result.inputs_snapshot,
        confidence=result.confidence,
    )
