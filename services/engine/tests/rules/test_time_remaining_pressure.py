"""`time_remaining_pressure` rule predicate — fire / no-fire / guarded-state tests.

Contract:
  * Fires iff `scheduled_end_at` is set, the schedule is valid, and the
    fraction of remaining time has fallen below `min_remaining_pct` of
    the originally-scheduled duration.
  * Action is global `redirect_topic`; never targets a participant.
  * Guarded states (no schedule, invalid schedule) map to `fired=False`
    with the inputs_snapshot annotating *why* the rule stayed quiet so
    the audit log explains itself in replay.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.rules.fixtures import NOW, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.rules.time_remaining_pressure import (
    TimeRemainingPressureConfig,
    TimeRemainingPressureRule,
)


def test_rule_metadata() -> None:
    rule = TimeRemainingPressureRule()
    assert rule.name == "time_remaining_pressure"
    assert rule.version == "v1.0"
    assert rule.priority == 70
    assert rule.default_cooldown_sec == 240.0


class TestTimeRemainingPressureGuardedStates:
    """The rule must stay silent when there is no usable schedule."""

    def test_no_scheduled_end_stays_silent(self) -> None:
        rule = TimeRemainingPressureRule()
        # Default fixture has no scheduled_end_at.
        state = make_session_state(
            started_at=NOW - timedelta(minutes=59),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.confidence == 0.0
        assert result.proposed_action == "redirect_topic"
        assert result.inputs_snapshot["has_scheduled_end"] is False

    def test_scheduled_end_equal_to_start_stays_silent(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=30)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started,
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["invalid_schedule"] is True
        assert result.inputs_snapshot["total_duration_sec"] == 0.0

    def test_scheduled_end_before_start_stays_silent(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=30)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started - timedelta(minutes=5),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["invalid_schedule"] is True
        assert result.inputs_snapshot["total_duration_sec"] < 0.0


class TestTimeRemainingPressureFiring:
    def test_session_just_started_does_not_fire(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(seconds=30)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        # ~99% remaining.
        assert result.inputs_snapshot["remaining_pct"] == pytest.approx(0.9917, abs=1e-3)
        assert result.inputs_snapshot["is_past_end"] is False

    def test_session_half_done_does_not_fire(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=30)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["remaining_pct"] == pytest.approx(0.5)

    def test_just_above_threshold_does_not_fire(self) -> None:
        # 11% remaining is above the default 10% threshold.
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=53, seconds=24)  # 89% elapsed
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["remaining_pct"] == pytest.approx(0.11, abs=1e-3)

    def test_just_below_threshold_fires(self) -> None:
        # 9% remaining is below the default 10% threshold.
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=54, seconds=36)  # 91% elapsed
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.proposed_action == "redirect_topic"
        assert result.target_participant_id is None
        assert result.inputs_snapshot["remaining_pct"] == pytest.approx(0.09, abs=1e-3)

    def test_exactly_at_threshold_does_not_fire(self) -> None:
        # Boundary check: predicate uses '>=' against the threshold to
        # stay silent — exact equality is "safe" territory.
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=54)  # 90% elapsed → 10% remaining
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["remaining_pct"] == pytest.approx(0.10)

    def test_at_session_end_fires_with_max_confidence(self) -> None:
        # 0% remaining → confidence 1.0.
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=60)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=NOW,
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == pytest.approx(1.0)
        assert result.inputs_snapshot["remaining_sec"] == pytest.approx(0.0)
        assert result.inputs_snapshot["is_past_end"] is False

    def test_past_session_end_saturates_confidence_and_flags_overrun(self) -> None:
        # Session ran 3 minutes past the schedule.
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=63)
        scheduled_end = started + timedelta(minutes=60)  # = NOW - 3min
        state = make_session_state(
            started_at=started,
            scheduled_end_at=scheduled_end,
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == pytest.approx(1.0)
        assert result.inputs_snapshot["is_past_end"] is True
        assert result.inputs_snapshot["remaining_sec"] < 0

    def test_lower_remaining_yields_higher_confidence(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=60)
        duration_sec = 66 * 60
        scheduled_end = started + timedelta(seconds=duration_sec)

        # State A: 8% remaining → raw confidence 0.2, floored to 0.2.
        state_a = make_session_state(
            started_at=started,
            scheduled_end_at=scheduled_end,
            t=started + timedelta(seconds=0.92 * duration_sec),  # 92% elapsed
        )

        # State B: 3% remaining → confidence 0.7.
        state_b = make_session_state(
            started_at=started,
            scheduled_end_at=scheduled_end,
            t=started + timedelta(seconds=0.97 * duration_sec),  # 97% elapsed
        )

        result_a = rule.predicate(state_a, state_a.t)
        result_b = rule.predicate(state_b, state_b.t)
        assert result_a.fired is True
        assert result_b.fired is True
        assert result_b.confidence > result_a.confidence

    def test_floored_confidence_when_just_below_threshold(self) -> None:
        rule = TimeRemainingPressureRule()
        # 9.9% remaining → raw confidence 0.01; floor to 0.20.
        started = NOW - timedelta(minutes=54, seconds=3, microseconds=600_000)
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == pytest.approx(0.20)


class TestTimeRemainingPressureReasonCodes:
    def test_reason_code_includes_remaining_percentage(self) -> None:
        rule = TimeRemainingPressureRule()
        started = NOW - timedelta(minutes=57)  # 95% elapsed, 5% remaining
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.reason_codes == ["time_remaining_pct_5"]

    def test_reason_code_preserves_negative_when_overrun(self) -> None:
        rule = TimeRemainingPressureRule()
        # 5% past end → remaining_pct = -0.05 → "time_remaining_pct_-5".
        started = NOW - timedelta(minutes=63)
        scheduled_end = started + timedelta(minutes=60)  # NOW - 3min
        state = make_session_state(
            started_at=started,
            scheduled_end_at=scheduled_end,
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        # -3min / 60min = -5%.
        assert result.reason_codes == ["time_remaining_pct_-5"]


class TestTimeRemainingPressureConfig:
    def test_default_threshold_matches_brief(self) -> None:
        assert TimeRemainingPressureConfig().min_remaining_pct == 0.10

    def test_threshold_rejects_zero(self) -> None:
        # A 0% threshold would never fire — disallow at the config layer
        # so a typo can't silently neuter the rule mid-session.
        with pytest.raises(ValueError, match="greater than 0"):
            TimeRemainingPressureConfig(min_remaining_pct=0.0)

    def test_threshold_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            TimeRemainingPressureConfig(min_remaining_pct=-0.1)

    def test_threshold_rejects_above_one(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 1"):
            TimeRemainingPressureConfig(min_remaining_pct=1.5)

    def test_custom_threshold_used(self) -> None:
        # 20% threshold: 15% remaining should now fire.
        rule = TimeRemainingPressureRule(
            TimeRemainingPressureConfig(min_remaining_pct=0.20),
        )
        started = NOW - timedelta(minutes=51)  # 85% elapsed, 15% remaining
        state = make_session_state(
            started_at=started,
            scheduled_end_at=started + timedelta(minutes=60),
            t=NOW,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.inputs_snapshot["min_remaining_pct"] == 0.20

    def test_from_rules_config_uses_overrides(self) -> None:
        cfg = RulesConfig(
            rules_version="test-v1",
            rules={"time_remaining_pressure": {"min_remaining_pct": 0.25}},
        )
        rule = TimeRemainingPressureRule.from_rules_config(cfg)
        assert rule._config.min_remaining_pct == 0.25

    def test_from_rules_config_uses_defaults_when_missing(self) -> None:
        cfg = RulesConfig(rules_version="test-v1", rules={})
        rule = TimeRemainingPressureRule.from_rules_config(cfg)
        assert rule._config.min_remaining_pct == 0.10

    def test_from_rules_config_rejects_unknown_keys(self) -> None:
        cfg = RulesConfig(
            rules_version="test-v1",
            rules={"time_remaining_pressure": {"min_remaining_pct": 0.1, "junk": 1}},
        )
        with pytest.raises(ValueError, match="Extra inputs are not permitted"):
            TimeRemainingPressureRule.from_rules_config(cfg)
