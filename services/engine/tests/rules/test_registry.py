"""`RulesRegistry` behaviour tests.

The registry is small but load-bearing: every tick goes through it, and
its iteration order shapes the audit log. These tests pin the contract:

  * Construction rejects bad input (duplicate names, blank version).
  * Iteration is deterministic and name-sorted.
  * Lookup raises typed errors, not bare KeyError.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from verbio_engine.domain.decision import DecisionAction
from verbio_engine.domain.session_state import SessionState
from verbio_engine.rules import (
    DuplicateRuleError,
    Rule,
    RulePredicateResult,
    RulesRegistry,
    UnknownRuleError,
)


class StubRule:
    """Minimal `Rule`-conforming stub for registry tests.

    Carries the four protocol attributes as instance attributes (the
    Protocol allows either class- or instance-level). The predicate
    is fixed (never fires) since these tests cover the registry, not
    evaluation.
    """

    def __init__(
        self,
        name: str,
        *,
        version: str = "v1.0",
        priority: int = 1,
        default_cooldown_sec: float = 45.0,
        proposed_action: DecisionAction = "stay_silent",
    ) -> None:
        self.name = name
        self.version = version
        self.priority = priority
        self.default_cooldown_sec = default_cooldown_sec
        self._proposed_action = proposed_action

    def predicate(
        self,
        state: SessionState,
        t: datetime,
    ) -> RulePredicateResult:
        return RulePredicateResult(
            fired=False,
            confidence=0.0,
            target_participant_id=None,
            reason_codes=[],
            inputs_snapshot={},
            proposed_action=self._proposed_action,
        )


def make_rule(name: str) -> Rule:
    """Type-checker convenience: return the stub typed as the protocol."""
    return StubRule(name)


class TestRulesRegistryConstruction:
    def test_accepts_distinct_rule_names(self) -> None:
        registry = RulesRegistry(
            [make_rule("a"), make_rule("b"), make_rule("c")],
            rules_version="v1.0",
        )
        assert len(registry) == 3
        assert registry.rules_version == "v1.0"

    def test_rejects_duplicate_names(self) -> None:
        with pytest.raises(DuplicateRuleError, match="duplicate rule name 'silence_gap'"):
            RulesRegistry(
                [make_rule("silence_gap"), make_rule("silence_gap")],
                rules_version="v1.0",
            )

    def test_rejects_blank_rules_version(self) -> None:
        with pytest.raises(ValueError, match="rules_version must be a non-empty string"):
            RulesRegistry([make_rule("only")], rules_version="")

    def test_accepts_empty_rule_set(self) -> None:
        # A registry with zero rules is valid — used by tests that want
        # to assert "no rules ⇒ no firings" without registering anything.
        registry = RulesRegistry([], rules_version="v1.0")
        assert len(registry) == 0
        assert registry.all() == ()


class TestRulesRegistryIteration:
    def test_iteration_is_name_sorted_regardless_of_input_order(self) -> None:
        registry = RulesRegistry(
            [make_rule("c"), make_rule("a"), make_rule("b")],
            rules_version="v1.0",
        )
        assert [r.name for r in registry] == ["a", "b", "c"]
        assert registry.names() == ("a", "b", "c")

    def test_all_returns_a_tuple_snapshot(self) -> None:
        registry = RulesRegistry([make_rule("a"), make_rule("b")], rules_version="v1.0")
        snapshot = registry.all()
        assert isinstance(snapshot, tuple)
        assert [r.name for r in snapshot] == ["a", "b"]


class TestRulesRegistryLookup:
    def test_contains_returns_true_for_registered_name(self) -> None:
        registry = RulesRegistry([make_rule("silence_gap")], rules_version="v1.0")
        assert "silence_gap" in registry
        assert "speaker_imbalance" not in registry

    def test_contains_is_false_for_non_string_inputs(self) -> None:
        # Defensive: `name in registry` shouldn't crash on a non-string;
        # `__contains__` returns False so callers don't have to pre-check.
        registry = RulesRegistry([make_rule("a")], rules_version="v1.0")
        assert (42 in registry) is False
        assert (None in registry) is False

    def test_get_returns_registered_rule(self) -> None:
        target = make_rule("silence_gap")
        registry = RulesRegistry([target], rules_version="v1.0")
        assert registry.get("silence_gap") is target

    def test_get_raises_unknown_rule_error(self) -> None:
        registry = RulesRegistry([make_rule("a")], rules_version="v1.0")
        with pytest.raises(UnknownRuleError, match="no rule named 'missing'"):
            registry.get("missing")
