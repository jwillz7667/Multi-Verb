"""`unheard_participant` rule — table-driven tests.

Brief §7.2 #5: participant silent ≥ N min AND engaged (backchannels or
recently interrupted). Tests pin:
  * The engagement gate excludes truly disengaged participants.
  * Selection picks the longest-silent engaged participant deterministically.
  * Never-spoken participants are skipped (they're silence_gap's job).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.rules.fixtures import NOW, make_participant, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.rules.unheard_participant import (
    UnheardParticipantConfig,
    UnheardParticipantRule,
)


def test_rule_metadata() -> None:
    rule = UnheardParticipantRule()
    assert rule.name == "unheard_participant"
    assert rule.version == "v1.0"
    assert rule.default_cooldown_sec == 90.0
    # Priority above silence_gap so a single unheard person wins
    # over collective quiet on the same tick.
    assert rule.priority > 50


class TestUnheardParticipantFiringConditions:
    def test_fires_for_engaged_silent_participant(self) -> None:
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=5),
                    backchannel_count_last_2min=3,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.target_participant_id == "p-1"
        assert result.proposed_action == "prompt_participant"
        assert any("unheard" in code for code in result.reason_codes)

    def test_does_not_fire_below_threshold(self) -> None:
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=2),
                    backchannel_count_last_2min=3,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["candidate_count"] == 0

    def test_does_not_fire_for_disengaged_participant(self) -> None:
        # Silent long enough but zero engagement signals — could be a
        # passive observer; the rule explicitly skips them.
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=8),
                    backchannel_count_last_2min=0,
                    was_interrupted_count=0,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False

    def test_fires_via_was_interrupted_engagement_signal(self) -> None:
        # No backchannels but they got cut off — that's an engaged
        # participant trying to speak.
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=6),
                    backchannel_count_last_2min=0,
                    was_interrupted_count=2,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True

    def test_does_not_fire_for_never_spoken_participant(self) -> None:
        # `silence_gap` covers the 'nobody has spoken yet' case via
        # least_recently_active. This rule needs at least one prior
        # utterance so we know the person is part of the conversation.
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=None,
                    backchannel_count_last_2min=5,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False

    def test_does_not_fire_with_no_participants(self) -> None:
        rule = UnheardParticipantRule()
        result = rule.predicate(make_session_state(participants={}), NOW)
        assert result.fired is False


class TestUnheardParticipantTargetSelection:
    def test_picks_longest_silent_among_engaged(self) -> None:
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=5),
                    backchannel_count_last_2min=2,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=8),
                    backchannel_count_last_2min=2,
                ),
                "p-3": make_participant(
                    participant_id="p-3",
                    last_spoke_at=NOW - timedelta(minutes=6),
                    backchannel_count_last_2min=2,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-2"

    def test_skips_disengaged_to_pick_engaged_even_when_shorter_silence(self) -> None:
        # p-1 has been silent longer but is disengaged. p-2 is the
        # right target despite being silent for less time.
        rule = UnheardParticipantRule()
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=10),
                    backchannel_count_last_2min=0,
                ),
                "p-2": make_participant(
                    participant_id="p-2",
                    last_spoke_at=NOW - timedelta(minutes=5),
                    backchannel_count_last_2min=3,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-2"

    def test_tie_breaks_equal_silence_by_participant_id_ascending(self) -> None:
        rule = UnheardParticipantRule()
        same_ts = NOW - timedelta(minutes=6)
        state = make_session_state(
            participants={
                "p-beta": make_participant(
                    participant_id="p-beta",
                    last_spoke_at=same_ts,
                    backchannel_count_last_2min=2,
                ),
                "p-alpha": make_participant(
                    participant_id="p-alpha",
                    last_spoke_at=same_ts,
                    backchannel_count_last_2min=2,
                ),
            },
        )
        result = rule.predicate(state, NOW)
        assert result.target_participant_id == "p-alpha"


class TestUnheardParticipantConfig:
    def test_default_config_matches_brief(self) -> None:
        cfg = UnheardParticipantConfig()
        assert cfg.silence_threshold_min == 4.0
        assert cfg.min_backchannels == 1
        assert cfg.min_was_interrupted == 1

    def test_higher_threshold_suppresses_firing(self) -> None:
        rule = UnheardParticipantRule(
            UnheardParticipantConfig(silence_threshold_min=10.0),
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=6),
                    backchannel_count_last_2min=3,
                ),
            },
        )
        assert rule.predicate(state, NOW).fired is False

    def test_higher_backchannel_threshold_excludes_marginal_engagement(self) -> None:
        rule = UnheardParticipantRule(
            UnheardParticipantConfig(min_backchannels=5),
        )
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=5),
                    backchannel_count_last_2min=2,
                    was_interrupted_count=0,
                ),
            },
        )
        assert rule.predicate(state, NOW).fired is False

    def test_from_rules_config_applies_override(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"unheard_participant": {"silence_threshold_min": 2.0}},
        )
        rule = UnheardParticipantRule.from_rules_config(cfg)
        state = make_session_state(
            participants={
                "p-1": make_participant(
                    participant_id="p-1",
                    last_spoke_at=NOW - timedelta(minutes=3),
                    backchannel_count_last_2min=1,
                ),
            },
        )
        assert rule.predicate(state, NOW).fired is True

    def test_from_rules_config_rejects_unknown_keys(self) -> None:
        cfg = RulesConfig(
            rules_version="v1.0",
            rules={"unheard_participant": {"silence_threshold_minute": 5.0}},
        )
        with pytest.raises(Exception, match="Extra inputs"):
            UnheardParticipantRule.from_rules_config(cfg)
