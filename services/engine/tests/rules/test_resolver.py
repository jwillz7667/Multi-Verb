"""Decision resolver — brief §7.3 + §7.4.

Resolver behaviour matrix:
  * No rule fires → stay_silent with empty suppressed_by.
  * One rule fires → that rule wins; decision carries its action / target.
  * Multiple fire → priority desc, then confidence desc, then name asc by virtue of input order.
  * Cooldown on the rule that would win → it loses; next eligible wins.
  * Cooldown on every firing rule → stay_silent with suppressed_by=['cooldown'].
  * QuietnessBudget exhausted → stay_silent with suppressed_by=['quietness_budget'].
  * Per-rule evaluations land for every rule in input order; decision_id matches across rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from tests.rules.fixtures import NOW, make_session_state
from verbio_engine.domain.budget import QuietnessBudget
from verbio_engine.rules.protocol import RulePredicateResult
from verbio_engine.rules.resolver import ResolverOutput, resolve

if TYPE_CHECKING:
    from verbio_engine.domain.session_state import SessionState


@dataclass
class StubRule:
    """Test double for `Rule`. Carries a pre-baked predicate result so
    tests can pin exactly what the resolver sees, without depending on
    the production rule predicates.
    """

    name: str
    priority: int
    default_cooldown_sec: float
    result: RulePredicateResult
    version: str = "v-test"
    predicate_calls: list[datetime] = field(default_factory=list)

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        self.predicate_calls.append(t)
        return self.result


def _fired(
    *,
    confidence: float = 0.7,
    action: str = "prompt_participant",
    target: str | None = "p-1",
    reason: str = "stub_fired",
) -> RulePredicateResult:
    return RulePredicateResult(
        fired=True,
        confidence=confidence,
        target_participant_id=target,
        reason_codes=[reason],
        inputs_snapshot={"reason": reason},
        proposed_action=action,  # type: ignore[arg-type]
    )


def _silent(
    *,
    action: str = "prompt_participant",
) -> RulePredicateResult:
    return RulePredicateResult(
        fired=False,
        confidence=0.0,
        target_participant_id=None,
        reason_codes=[],
        inputs_snapshot={},
        proposed_action=action,  # type: ignore[arg-type]
    )


def _assert_consistent(out: ResolverOutput) -> None:
    """Every evaluation row links back to the same decision."""
    decision_id = out.decision.decision_id
    for ev in out.rule_evaluations:
        assert ev.decision_id == decision_id


class TestResolverNoFireCase:
    def test_stays_silent_when_no_rule_fires(self) -> None:
        rule_a = StubRule(name="a", priority=50, default_cooldown_sec=30.0, result=_silent())
        rule_b = StubRule(name="b", priority=40, default_cooldown_sec=30.0, result=_silent())

        out = resolve(make_session_state(), NOW, [rule_a, rule_b], {})

        assert out.decision.action == "stay_silent"
        assert out.decision.suppressed_by == []
        assert out.decision.triggering_rule is None
        assert out.decision.confidence == 0.0
        # cooldown_until == t because silence doesn't lock future ticks.
        assert out.decision.cooldown_until == NOW
        assert len(out.rule_evaluations) == 2
        assert all(ev.fired is False for ev in out.rule_evaluations)
        assert all(ev.suppressed_reason is None for ev in out.rule_evaluations)
        _assert_consistent(out)


class TestResolverSingleWinner:
    def test_lone_firing_rule_wins(self) -> None:
        winner = StubRule(
            name="winner",
            priority=50,
            default_cooldown_sec=45.0,
            result=_fired(reason="winner_fired", target="p-2"),
        )
        silent = StubRule(name="quiet", priority=60, default_cooldown_sec=30.0, result=_silent())

        out = resolve(make_session_state(), NOW, [silent, winner], {})

        assert out.decision.action == "prompt_participant"
        assert out.decision.target_participant_id == "p-2"
        assert out.decision.triggering_rule == "winner"
        assert out.decision.reason_codes == ["winner_fired"]
        assert out.decision.suppressed_by == []
        assert out.decision.cooldown_until == NOW + timedelta(seconds=45.0)
        assert out.decision.confidence == 0.7
        _assert_consistent(out)

    def test_evaluations_preserve_input_order(self) -> None:
        # Evaluations must appear in the order rules were passed in, so
        # callers can zip them against registry.all() for indexed lookup.
        a = StubRule(name="a", priority=10, default_cooldown_sec=30.0, result=_silent())
        b = StubRule(
            name="b",
            priority=20,
            default_cooldown_sec=30.0,
            result=_fired(reason="b_fired"),
        )
        c = StubRule(name="c", priority=30, default_cooldown_sec=30.0, result=_silent())

        out = resolve(make_session_state(), NOW, [a, b, c], {})

        assert [ev.rule_name for ev in out.rule_evaluations] == ["a", "b", "c"]


class TestResolverPriority:
    def test_higher_priority_wins_over_higher_confidence(self) -> None:
        # b has lower confidence (0.5 < 0.9) but higher priority — wins.
        a = StubRule(
            name="a",
            priority=40,
            default_cooldown_sec=30.0,
            result=_fired(confidence=0.9, reason="a_fired"),
        )
        b = StubRule(
            name="b",
            priority=70,
            default_cooldown_sec=30.0,
            result=_fired(confidence=0.5, reason="b_fired"),
        )

        out = resolve(make_session_state(), NOW, [a, b], {})

        assert out.decision.triggering_rule == "b"
        a_ev = next(ev for ev in out.rule_evaluations if ev.rule_name == "a")
        b_ev = next(ev for ev in out.rule_evaluations if ev.rule_name == "b")
        assert b_ev.suppressed_reason is None
        assert a_ev.suppressed_reason == "lower_priority_won"

    def test_ties_on_priority_resolved_by_confidence(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(confidence=0.4, reason="a_fired"),
        )
        b = StubRule(
            name="b",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(confidence=0.8, reason="b_fired"),
        )

        out = resolve(make_session_state(), NOW, [a, b], {})

        assert out.decision.triggering_rule == "b"

    def test_losers_get_lower_priority_won_marker(self) -> None:
        a = StubRule(
            name="a",
            priority=30,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
        )
        b = StubRule(
            name="b",
            priority=70,
            default_cooldown_sec=30.0,
            result=_fired(reason="b_fired"),
        )

        out = resolve(make_session_state(), NOW, [a, b], {})

        a_ev = next(ev for ev in out.rule_evaluations if ev.rule_name == "a")
        assert a_ev.fired is True
        assert a_ev.suppressed_reason == "lower_priority_won"


class TestResolverCooldown:
    def test_cooldown_locked_rule_loses_to_eligible_lower_priority(self) -> None:
        # high_pri is fresh off a win 10s ago, its cooldown is 60s.
        # low_pri fired and has no cooldown — low_pri wins despite being
        # less important.
        high_pri = StubRule(
            name="high_pri",
            priority=70,
            default_cooldown_sec=60.0,
            result=_fired(reason="high_fired"),
        )
        low_pri = StubRule(
            name="low_pri",
            priority=30,
            default_cooldown_sec=30.0,
            result=_fired(reason="low_fired"),
        )

        cooldowns = {"high_pri": NOW - timedelta(seconds=10)}
        out = resolve(make_session_state(), NOW, [high_pri, low_pri], cooldowns)

        assert out.decision.triggering_rule == "low_pri"
        high_ev = next(ev for ev in out.rule_evaluations if ev.rule_name == "high_pri")
        low_ev = next(ev for ev in out.rule_evaluations if ev.rule_name == "low_pri")
        assert high_ev.suppressed_reason == "cooldown"
        assert low_ev.suppressed_reason is None

    def test_cooldown_expires_after_window(self) -> None:
        # Same setup, but last fire was 70s ago — cooldown (60s) has
        # elapsed, so high_pri is eligible again and wins.
        high_pri = StubRule(
            name="high_pri",
            priority=70,
            default_cooldown_sec=60.0,
            result=_fired(reason="high_fired"),
        )
        low_pri = StubRule(
            name="low_pri",
            priority=30,
            default_cooldown_sec=30.0,
            result=_fired(reason="low_fired"),
        )

        cooldowns = {"high_pri": NOW - timedelta(seconds=70)}
        out = resolve(make_session_state(), NOW, [high_pri, low_pri], cooldowns)

        assert out.decision.triggering_rule == "high_pri"

    def test_all_fired_rules_cooldown_yields_stay_silent_with_marker(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=60.0,
            result=_fired(reason="a_fired"),
        )
        b = StubRule(
            name="b",
            priority=40,
            default_cooldown_sec=60.0,
            result=_fired(reason="b_fired"),
        )
        cooldowns = {"a": NOW - timedelta(seconds=5), "b": NOW - timedelta(seconds=5)}

        out = resolve(make_session_state(), NOW, [a, b], cooldowns)

        assert out.decision.action == "stay_silent"
        assert out.decision.suppressed_by == ["cooldown"]
        for ev in out.rule_evaluations:
            assert ev.suppressed_reason == "cooldown"

    def test_no_suppression_marker_when_no_rule_fired_at_all(self) -> None:
        # All rules silent — clean stay_silent, no cooldowns involved.
        a = StubRule(name="a", priority=50, default_cooldown_sec=60.0, result=_silent())
        cooldowns = {"a": NOW - timedelta(seconds=5)}

        out = resolve(make_session_state(), NOW, [a], cooldowns)

        assert out.decision.suppressed_by == []

    def test_unfired_rule_with_active_cooldown_does_not_get_cooldown_marker(self) -> None:
        # Rule didn't fire AND is in cooldown — suppressed_reason should
        # still be None (we only attribute suppression to rules that
        # actually wanted to fire).
        a = StubRule(name="a", priority=50, default_cooldown_sec=60.0, result=_silent())
        cooldowns = {"a": NOW - timedelta(seconds=5)}

        out = resolve(make_session_state(), NOW, [a], cooldowns)

        a_ev = out.rule_evaluations[0]
        assert a_ev.fired is False
        assert a_ev.suppressed_reason is None


class TestResolverQuietnessBudget:
    def test_budget_count_cap_suppresses_firing_rule(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
        )
        budget = QuietnessBudget(
            max_utterances_per_10min=3,
            min_seconds_between_utterances=30.0,
            current_window_count=3,  # already at cap
            last_utterance_at=NOW - timedelta(minutes=2),
        )
        state = make_session_state(quietness_budget=budget)

        out = resolve(state, NOW, [a], {})

        assert out.decision.action == "stay_silent"
        assert out.decision.suppressed_by == ["quietness_budget"]
        assert out.rule_evaluations[0].suppressed_reason == "quietness_budget"

    def test_budget_min_gap_suppresses_firing_rule(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
        )
        budget = QuietnessBudget(
            max_utterances_per_10min=10,
            min_seconds_between_utterances=30.0,
            current_window_count=1,
            last_utterance_at=NOW - timedelta(seconds=10),  # only 10s ago
        )
        state = make_session_state(quietness_budget=budget)

        out = resolve(state, NOW, [a], {})

        assert out.decision.suppressed_by == ["quietness_budget"]

    def test_budget_allows_when_under_cap_and_past_gap(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
        )
        budget = QuietnessBudget(
            max_utterances_per_10min=10,
            min_seconds_between_utterances=30.0,
            current_window_count=2,
            last_utterance_at=NOW - timedelta(seconds=60),
        )
        state = make_session_state(quietness_budget=budget)

        out = resolve(state, NOW, [a], {})

        assert out.decision.triggering_rule == "a"
        assert out.decision.suppressed_by == []

    def test_shadow_mode_default_budget_never_blocks(self) -> None:
        # current_window_count=0 (default) and last_utterance_at=None —
        # the shadow-mode steady state. Rule should fire freely.
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
        )

        out = resolve(make_session_state(), NOW, [a], {})

        assert out.decision.triggering_rule == "a"


class TestResolverDecisionFields:
    def test_decision_carries_state_session_metadata(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=45.0,
            result=_fired(reason="a_fired"),
        )
        state = make_session_state(tick_id=42, t=NOW)

        out = resolve(state, NOW, [a], {})

        assert out.decision.session_id == state.session_id
        assert out.decision.tick_id == 42
        assert out.decision.timestamp == NOW
        assert out.decision.source == "auto"
        assert out.decision.was_executed is False
        assert out.decision.llm_prompt is None
        assert out.decision.spoken_at is None

    def test_evaluation_rows_carry_rule_metadata(self) -> None:
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=_fired(reason="a_fired"),
            version="v1.7",
        )

        out = resolve(make_session_state(), NOW, [a], {})

        ev = out.rule_evaluations[0]
        assert ev.rule_name == "a"
        assert ev.rule_version == "v1.7"
        assert ev.fired is True
        assert ev.predicate_inputs == {"reason": "a_fired"}
        assert ev.confidence == 0.7
        assert ev.evaluation_id != out.decision.decision_id

    def test_predicates_called_exactly_once_per_tick(self) -> None:
        # Even when a rule is cooldown-locked, the predicate runs (so
        # the audit trail captures "rule X would have fired"). It runs
        # exactly once, never twice.
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=60.0,
            result=_fired(reason="a_fired"),
        )
        cooldowns = {"a": NOW - timedelta(seconds=5)}

        resolve(make_session_state(), NOW, [a], cooldowns)

        assert len(a.predicate_calls) == 1

    def test_reason_codes_copied_not_aliased(self) -> None:
        # ModeratorDecision is frozen but defensive copy still matters
        # if a future caller tries to mutate the source list.
        original_codes = ["a_fired", "extra"]
        a = StubRule(
            name="a",
            priority=50,
            default_cooldown_sec=30.0,
            result=RulePredicateResult(
                fired=True,
                confidence=0.5,
                target_participant_id="p-1",
                reason_codes=original_codes,
                inputs_snapshot={},
                proposed_action="prompt_participant",
            ),
        )

        out = resolve(make_session_state(), NOW, [a], {})

        assert out.decision.reason_codes == ["a_fired", "extra"]
        assert out.decision.reason_codes is not original_codes
