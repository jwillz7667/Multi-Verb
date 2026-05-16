"""`RulePredicateResult` + `Rule` Protocol shape tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from verbio_engine.rules import Rule, RulePredicateResult


class TestRulePredicateResult:
    def test_minimal_valid_result(self) -> None:
        result = RulePredicateResult(
            fired=False,
            confidence=0.0,
            proposed_action="stay_silent",
        )
        assert result.fired is False
        assert result.target_participant_id is None
        assert result.reason_codes == []
        assert result.inputs_snapshot == {}

    def test_carries_full_audit_payload(self) -> None:
        result = RulePredicateResult(
            fired=True,
            confidence=0.92,
            target_participant_id="p-3",
            reason_codes=["silence_gap_9s", "last_speaker_p-1"],
            inputs_snapshot={
                "silence_run_sec": 9.1,
                "least_recent_participant": "p-3",
            },
            proposed_action="prompt_participant",
        )
        assert result.target_participant_id == "p-3"
        assert "silence_gap_9s" in result.reason_codes
        assert result.inputs_snapshot["silence_run_sec"] == 9.1

    def test_rejects_confidence_above_one(self) -> None:
        with pytest.raises(ValidationError):
            RulePredicateResult(
                fired=True,
                confidence=1.5,
                proposed_action="prompt_participant",
            )

    def test_rejects_negative_confidence(self) -> None:
        with pytest.raises(ValidationError):
            RulePredicateResult(
                fired=False,
                confidence=-0.1,
                proposed_action="stay_silent",
            )

    def test_rejects_unknown_action(self) -> None:
        with pytest.raises(ValidationError):
            RulePredicateResult.model_validate(
                {
                    "fired": False,
                    "confidence": 0.0,
                    "proposed_action": "do_a_dance",
                },
            )

    def test_rejects_unknown_top_level_keys(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs"):
            RulePredicateResult.model_validate(
                {
                    "fired": False,
                    "confidence": 0.0,
                    "proposed_action": "stay_silent",
                    "scratch": "field",
                },
            )

    def test_is_frozen(self) -> None:
        result = RulePredicateResult(
            fired=False,
            confidence=0.0,
            proposed_action="stay_silent",
        )
        with pytest.raises(ValidationError):
            result.fired = True  # type: ignore[misc]


class TestRuleProtocolRuntimeCheck:
    def test_class_with_required_attributes_satisfies_protocol(self) -> None:
        class GoodRule:
            name = "good"
            version = "v1.0"
            priority = 1
            default_cooldown_sec = 30.0

            def predicate(
                self,
                state: object,
                t: datetime,
            ) -> RulePredicateResult:
                return RulePredicateResult(
                    fired=False,
                    confidence=0.0,
                    proposed_action="stay_silent",
                )

        assert isinstance(GoodRule(), Rule)

    def test_missing_attribute_fails_protocol_check(self) -> None:
        class BadRule:
            # Missing `priority`, `default_cooldown_sec`, and `predicate`.
            name = "bad"
            version = "v1.0"

        assert not isinstance(BadRule(), Rule)
