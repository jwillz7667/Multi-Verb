"""`speaker_imbalance` rule — table-driven tests.

Brief §7.2 #2: one participant ≥ 2.0x fair share AND another ≤ 0.4x
fair share over last 5min → `prompt_participant` targeting the
under-share person.

Test focus:
  * Both halves of the AND must hold to fire.
  * Target selection picks the most under-share person, deterministic ties.
  * Never-spoken participants are excluded from the under-share pool.
  * ≥ 3-participant gate suppresses the rule for dyads.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.rules.fixtures import NOW, make_participant, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.rules.speaker_imbalance import (
    SpeakerImbalanceConfig,
    SpeakerImbalanceRule,
)


def test_rule_metadata() -> None:
    rule = SpeakerImbalanceRule()
    assert rule.name == "speaker_imbalance"
    assert rule.version == "v1.0"
    assert rule.default_cooldown_sec == 90.0
    # Sits between cross_talk_pattern (40) and silence_gap (50).
    assert 40 < rule.priority < 50


class TestSpeakerImbalanceFiringConditions:
    def test_fires_with_clear_over_and_under_sharer(self) -> None:
        rule = SpeakerImbalanceRule()
        # 4 participants, fair share 25%. p-1 dominates at 60% (2.4x),
        # p-2 ghosted at 5% (0.2x), others at 17.5% each.
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=30),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=60.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=45),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=17.5,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=17.5,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-2"
        assert result.proposed_action == "prompt_participant"
        assert result.reason_codes == ["speaker_imbalance_p-2_under_p-1_over"]
        assert result.inputs_snapshot["over_sharer_id"] == "p-1"
        assert result.inputs_snapshot["under_sharer_ids"] == ["p-2"]

    def test_does_not_fire_when_no_over_sharer(self) -> None:
        # Someone under-shares but nobody is hogging — natural quiet
        # for that participant, not a moderation failure.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=30),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=30.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=4),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=30.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=35.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["over_sharer_ids"] == []
        assert result.inputs_snapshot["under_sharer_ids"] == ["p-2"]

    def test_does_not_fire_when_no_under_sharer(self) -> None:
        # Someone dominates but nobody is freezing out — could just
        # be an enthusiastic discussion. Cross_talk_pattern or other
        # rules cover the "too much talking" failure mode.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=60.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(seconds=30),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=15.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=12.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=13.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False

    def test_does_not_fire_below_min_participants(self) -> None:
        # Dyad: one person dominating is just a one-sided conversation,
        # not necessarily a moderation problem.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=50.0,
                    actual_share_last_5min_pct=95.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=50.0,
                    actual_share_last_5min_pct=5.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["participant_count"] == 2

    def test_excludes_never_spoken_from_under_share_pool(self) -> None:
        # p-3 has zero actual share AND no last_spoke_at — that's
        # silence_gap's problem, not ours. Only p-2 (under-share AND
        # has spoken before) is a valid target.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=33.3,
                    actual_share_last_5min_pct=80.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=33.3,
                    actual_share_last_5min_pct=10.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=None,
                    fair_share_pct=33.3,
                    actual_share_last_5min_pct=0.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-2"
        assert "p-3" not in result.inputs_snapshot["under_sharer_ids"]

    def test_skips_participant_with_zero_fair_share(self) -> None:
        # Fair share not yet computed for a participant — we can't
        # reason about over/under without a baseline, so skip them
        # silently in both pools.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=33.3,
                    actual_share_last_5min_pct=80.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=33.3,
                    actual_share_last_5min_pct=10.0,
                ),
                "p-orphan": make_participant(
                    participant_id="p-orphan",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=0.0,
                    actual_share_last_5min_pct=0.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-2"

    def test_does_not_fire_with_empty_session(self) -> None:
        rule = SpeakerImbalanceRule()
        result = rule.predicate(make_session_state(participants={}), NOW)
        assert result.fired is False
        assert result.inputs_snapshot["participant_count"] == 0


class TestSpeakerImbalanceTargetSelection:
    def test_picks_most_under_share_among_candidates(self) -> None:
        # p-2 at 0.4x ratio, p-4 at 0.2x ratio — p-4 is more under-share.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=20.0,
                    actual_share_last_5min_pct=60.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=20.0,
                    actual_share_last_5min_pct=8.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=20.0,
                    actual_share_last_5min_pct=20.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    fair_share_pct=20.0,
                    actual_share_last_5min_pct=4.0,
                ),
                "p-5": make_participant(
                    participant_id="p-5",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=20.0,
                    actual_share_last_5min_pct=8.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-4"

    def test_tie_breaks_under_sharers_alphabetically(self) -> None:
        # Two participants both at 0.2x ratio — pick by participant_id ascending.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-bob": make_participant(
                    participant_id="p-bob",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-amy": make_participant(
                    participant_id="p-amy",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-zoe": make_participant(
                    participant_id="p-zoe",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=70.0,
                ),
                "p-eve": make_participant(
                    participant_id="p-eve",
                    last_spoke_at=NOW - timedelta(seconds=30),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=20.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-amy"

    def test_picks_worst_over_sharer_for_audit(self) -> None:
        # Two over-sharers — audit records the worst one for the
        # reason_code and confidence calculation.
        rule = SpeakerImbalanceRule()
        state = make_session_state(
            participants={
                "p-loud": make_participant(
                    participant_id="p-loud",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=70.0,
                ),
                "p-loud2": make_participant(
                    participant_id="p-loud2",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=55.0,
                ),
                "p-quiet": make_participant(
                    participant_id="p-quiet",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-mid": make_participant(
                    participant_id="p-mid",
                    last_spoke_at=NOW - timedelta(seconds=30),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=15.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.inputs_snapshot["over_sharer_id"] == "p-loud"
        assert result.reason_codes == [
            "speaker_imbalance_p-quiet_under_p-loud_over",
        ]


class TestSpeakerImbalanceConfidence:
    def test_more_extreme_imbalance_yields_higher_confidence(self) -> None:
        rule = SpeakerImbalanceRule()
        mild = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=55.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=8.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=18.5,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=25),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=18.5,
                ),
            },
        )
        extreme = make_session_state(
            participants={
                # 100% / 25% = 4.0x ratio — past 2x over_share_factor,
                # so confidence saturates.
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=100.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=0.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=20),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=0.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=25),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=0.0,
                ),
            },
        )
        mild_result = rule.predicate(mild, NOW)
        extreme_result = rule.predicate(extreme, NOW)
        assert mild_result.fired is True
        assert extreme_result.fired is True
        assert extreme_result.confidence > mild_result.confidence
        assert extreme_result.confidence == 1.0

    def test_confidence_floors_above_zero_at_threshold(self) -> None:
        rule = SpeakerImbalanceRule()
        # Over-sharer exactly at 2.0x, under-sharer at 0.4x —
        # excess = 0, confidence would compute as 0.0 without the floor.
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=50.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=10.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=20.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=20.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence >= 0.15


class TestSpeakerImbalanceConfig:
    def test_default_config_matches_brief(self) -> None:
        cfg = SpeakerImbalanceConfig()
        assert cfg.over_share_factor == 2.0
        assert cfg.under_share_factor == 0.4
        assert cfg.min_participants == 3

    def test_stricter_factors_suppress_marginal_firings(self) -> None:
        # Same state as the firing test, but raise the over_share
        # threshold to 3.0 so p-1 at 2.4x no longer qualifies.
        rule = SpeakerImbalanceRule(SpeakerImbalanceConfig(over_share_factor=3.0))
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=60.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=5.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=17.5,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=17.5,
                ),
            },
        )
        assert rule.predicate(state, NOW).fired is False

    def test_lower_min_participants_allows_dyad_firing(self) -> None:
        # In a dyad with fair_share 50/50, ratio 2.0 is only reachable
        # when one participant has 100% — the mathematical edge that
        # the default min_participants=3 was designed to avoid.
        rule = SpeakerImbalanceRule(SpeakerImbalanceConfig(min_participants=2))
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=50.0,
                    actual_share_last_5min_pct=100.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    fair_share_pct=50.0,
                    actual_share_last_5min_pct=0.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-2"

    def test_zero_over_share_factor_rejected(self) -> None:
        with pytest.raises(Exception, match="greater than 0"):
            SpeakerImbalanceConfig(over_share_factor=0.0)

    def test_zero_under_share_factor_rejected(self) -> None:
        with pytest.raises(Exception, match="greater than 0"):
            SpeakerImbalanceConfig(under_share_factor=0.0)

    def test_from_rules_config_applies_override(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"speaker_imbalance": {"over_share_factor": 1.5}},
        )
        rule = SpeakerImbalanceRule.from_rules_config(cfg)
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=40.0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=8.0,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=26.0,
                ),
                "p-4": make_participant(
                    participant_id="p-4",
                    last_spoke_at=NOW - timedelta(seconds=15),
                    fair_share_pct=25.0,
                    actual_share_last_5min_pct=26.0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True

    def test_from_rules_config_rejects_unknown_keys(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"speaker_imbalance": {"hog_factor": 2.5}},
        )
        with pytest.raises(Exception, match="Extra inputs"):
            SpeakerImbalanceRule.from_rules_config(cfg)
