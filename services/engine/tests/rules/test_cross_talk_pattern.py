"""`cross_talk_pattern` rule — table-driven tests.

Brief §7.2 #4: ≥ 3 interruption events (cumulative across participants)
in the last 2 minutes → `suggest_turn_taking`. The intervention is
group-wide; no specific target.
"""

from __future__ import annotations

import pytest

from tests.rules.fixtures import NOW, make_participant, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.rules.cross_talk_pattern import (
    CrossTalkPatternConfig,
    CrossTalkPatternRule,
)


def test_rule_metadata() -> None:
    rule = CrossTalkPatternRule()
    assert rule.name == "cross_talk_pattern"
    assert rule.version == "v1.0"
    assert rule.default_cooldown_sec == 180.0
    # Lower priority than silence_gap (50) / unheard_participant (60).
    assert rule.priority < 50


class TestCrossTalkPatternFiringConditions:
    def test_does_not_fire_below_threshold(self) -> None:
        rule = CrossTalkPatternRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=1),
                "p-2": make_participant(participant_id="p-2", interruption_count=1),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["total_interruptions_last_2min"] == 2

    def test_fires_at_threshold(self) -> None:
        rule = CrossTalkPatternRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=2),
                "p-2": make_participant(participant_id="p-2", interruption_count=1),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id is None
        assert result.proposed_action == "suggest_turn_taking"
        assert result.reason_codes == ["cross_talk_3_in_2min"]

    def test_fires_with_high_confidence_above_threshold(self) -> None:
        rule = CrossTalkPatternRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=4),
                "p-2": make_participant(participant_id="p-2", interruption_count=4),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        # 8 total, threshold 3 → excess 5, 5/3 > 1.0 → saturate at 1.0
        assert result.confidence == 1.0

    def test_confidence_floors_above_zero_at_threshold(self) -> None:
        rule = CrossTalkPatternRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=3),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        # 0 excess but the rule floors at 0.2 so the dashboard's bar
        # isn't deceptively empty when the rule has just fired.
        assert result.confidence >= 0.2

    def test_does_not_fire_with_no_participants(self) -> None:
        rule = CrossTalkPatternRule()
        result = rule.predicate(make_session_state(participants={}), NOW)
        assert result.fired is False
        assert result.inputs_snapshot["total_interruptions_last_2min"] == 0

    def test_records_per_participant_counts_in_audit(self) -> None:
        # The audit snapshot should preserve enough detail that a
        # researcher can answer 'who was doing the interrupting?'.
        rule = CrossTalkPatternRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=2),
                "p-2": make_participant(participant_id="p-2", interruption_count=2),
            },
        )
        result = rule.predicate(state, NOW)
        per = result.inputs_snapshot["per_participant_interruption_counts"]
        assert per == {"p-1": 2, "p-2": 2}


class TestCrossTalkPatternConfig:
    def test_default_threshold_matches_brief(self) -> None:
        assert CrossTalkPatternConfig().min_interruptions == 3

    def test_custom_threshold_suppresses_firing(self) -> None:
        rule = CrossTalkPatternRule(CrossTalkPatternConfig(min_interruptions=10))
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=4),
            },
        )
        assert rule.predicate(state, NOW).fired is False

    def test_lower_threshold_fires_more_eagerly(self) -> None:
        rule = CrossTalkPatternRule(CrossTalkPatternConfig(min_interruptions=1))
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=1),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.reason_codes == ["cross_talk_1_in_2min"]

    def test_zero_threshold_rejected_by_pydantic(self) -> None:
        # PositiveInt — zero would mean "any tick fires", nonsensical
        # for a rate-based rule.
        with pytest.raises(Exception, match="greater than 0"):
            CrossTalkPatternConfig(min_interruptions=0)

    def test_from_rules_config_picks_up_override(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"cross_talk_pattern": {"min_interruptions": 5}},
        )
        rule = CrossTalkPatternRule.from_rules_config(cfg)
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=3),
            },
        )
        assert rule.predicate(state, NOW).fired is False

    def test_from_rules_config_uses_defaults_when_no_override(self) -> None:
        cfg = RulesConfig(rules_version="v1.0", rules={})
        rule = CrossTalkPatternRule.from_rules_config(cfg)
        state = make_session_state(
            participants={
                "p-1": make_participant(participant_id="p-1", interruption_count=3),
            },
        )
        assert rule.predicate(state, NOW).fired is True

    def test_from_rules_config_rejects_unknown_keys(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"cross_talk_pattern": {"interruption_threshold": 5}},
        )
        with pytest.raises(Exception, match="Extra inputs"):
            CrossTalkPatternRule.from_rules_config(cfg)
