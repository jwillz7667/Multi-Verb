"""`silence_gap` rule predicate — table-driven fire / no-fire tests.

The rule's contract (brief §7.2 #1):
  * Fire iff `silence_run_sec >= threshold` AND no participant has VAD.
  * Target the least-recently-active participant; tie-break stably.
  * Carry an audit snapshot so 'why didn't it fire?' is answerable.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.rules.fixtures import NOW, make_participant, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.rules.silence_gap import SilenceGapConfig, SilenceGapRule


def test_rule_metadata() -> None:
    rule = SilenceGapRule()
    assert rule.name == "silence_gap"
    assert rule.version == "v1.0"
    assert rule.default_cooldown_sec == 45.0
    assert rule.priority == 50


class TestSilenceGapFiringConditions:
    def test_does_not_fire_below_threshold(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=5.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.confidence == 0.0
        assert result.target_participant_id is None
        assert result.inputs_snapshot["silence_run_sec"] == 5.0

    def test_does_not_fire_when_vad_is_active(self) -> None:
        # Someone is mid-speech (Deepgram hasn't emitted yet but VAD has);
        # the rule must stay quiet — silence_run_sec might be stale.
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=12.0,
            participants={
                "p-1": make_participant(participant_id="p-1", vad_active=True),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["any_vad_active"] is True

    def test_fires_at_threshold(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=8.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-1"
        assert result.proposed_action == "prompt_participant"
        assert result.reason_codes == ["silence_gap_8s"]

    def test_fires_well_above_threshold_with_high_confidence(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=20.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == 1.0  # saturated at 2x threshold
        assert result.reason_codes == ["silence_gap_20s"]

    def test_fires_just_above_threshold_with_minimum_confidence(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=8.05,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        # Floor of 0.05 — never zero-confidence when firing.
        assert result.confidence >= 0.05

    def test_does_not_fire_when_no_participants(self) -> None:
        # Edge case: session opens with nobody yet. silence_run_sec
        # can grow but there's nobody to prompt.
        rule = SilenceGapRule()
        state = make_session_state(silence_run_sec=99.0, participants={})
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.target_participant_id is None
        assert result.inputs_snapshot["least_recently_active_participant_id"] is None


class TestSilenceGapTargetSelection:
    def test_prefers_never_spoken_over_recently_spoken(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=10.0,
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(seconds=10),
                ),
                "p-2": make_participant(participant_id="p-2", last_spoke_at=None),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-2"

    def test_picks_earliest_last_spoke_at_when_all_have_spoken(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=10.0,
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=2),
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=5),
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(seconds=30),
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-2"

    def test_tie_breaks_never_spoken_by_participant_id_ascending(self) -> None:
        rule = SilenceGapRule()
        state = make_session_state(
            silence_run_sec=10.0,
            participants={
                "p-zeta": make_participant(participant_id="p-zeta", last_spoke_at=None),
                "p-alpha": make_participant(participant_id="p-alpha", last_spoke_at=None),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-alpha"

    def test_tie_breaks_equal_last_spoke_at_by_participant_id_ascending(self) -> None:
        rule = SilenceGapRule()
        same_ts = NOW - timedelta(minutes=4)
        state = make_session_state(
            silence_run_sec=10.0,
            participants={
                "p-beta": make_participant(participant_id="p-beta", last_spoke_at=same_ts),
                "p-alpha": make_participant(participant_id="p-alpha", last_spoke_at=same_ts),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-alpha"


class TestSilenceGapConfig:
    def test_default_config_has_brief_default_threshold(self) -> None:
        assert SilenceGapConfig().threshold_sec == 8.0

    def test_custom_threshold_changes_firing(self) -> None:
        rule = SilenceGapRule(SilenceGapConfig(threshold_sec=15.0))
        state = make_session_state(
            silence_run_sec=10.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        assert rule.predicate(state, NOW).fired is False

    def test_from_rules_config_picks_up_per_rule_override(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"silence_gap": {"threshold_sec": 4.0}},
        )
        rule = SilenceGapRule.from_rules_config(cfg)
        state = make_session_state(
            silence_run_sec=5.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True

    def test_from_rules_config_uses_defaults_when_no_override(self) -> None:
        cfg = RulesConfig(rules_version="v1.0", rules={})
        rule = SilenceGapRule.from_rules_config(cfg)
        # Same state as test_does_not_fire_below_threshold — defaults
        # threshold of 8.0 should still be active.
        state = make_session_state(
            silence_run_sec=5.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        assert rule.predicate(state, NOW).fired is False

    def test_from_rules_config_rejects_unknown_keys(self) -> None:
        # Extra=forbid on the config catches typos early — better than
        # silently ignoring `thrshold_sec` for the whole session.
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"silence_gap": {"thrshold_sec": 4.0}},
        )
        with pytest.raises(Exception, match="Extra inputs"):
            SilenceGapRule.from_rules_config(cfg)

    def test_zero_threshold_treats_silence_as_immediate_fire(self) -> None:
        # Boundary: threshold=0 means any silence_run_sec >= 0 fires.
        # The confidence formula special-cases this to avoid div/0.
        rule = SilenceGapRule(SilenceGapConfig(threshold_sec=0.0))
        state = make_session_state(
            silence_run_sec=0.0,
            participants={"p-1": make_participant(participant_id="p-1")},
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == 1.0
