"""`topic_drift` rule predicate — fire / no-fire / guarded-state tests.

Contract:
  * Fires iff prompt + transcript embeddings exist AND their cosine
    similarity < `min_similarity`.
  * Action is global `redirect_topic`; never targets a participant.
  * All guarded states (no prompt, no embedding, length mismatch) map
    to `fired=False` with a populated `inputs_snapshot` so the audit
    log explains *why* the rule stayed quiet.
"""

from __future__ import annotations

import pytest

from tests.rules.fixtures import NOW, make_session_state
from verbio_engine.domain import RulesConfig
from verbio_engine.embeddings import MockEmbeddingProvider
from verbio_engine.rules.topic_drift import TopicDriftConfig, TopicDriftRule


def _make_embeddings(
    prompt: str, transcript: str, dim: int = 64
) -> tuple[list[float], list[float]]:
    """Build a (prompt_vec, transcript_vec) pair via the mock provider."""
    provider = MockEmbeddingProvider(dim=dim)
    return (provider._embed_sync(prompt), provider._embed_sync(transcript))


def test_rule_metadata() -> None:
    rule = TopicDriftRule()
    assert rule.name == "topic_drift"
    assert rule.version == "v1.0"
    assert rule.priority == 35
    assert rule.default_cooldown_sec == 180.0


class TestTopicDriftGuardedStates:
    """The rule must stay silent when inputs are missing or malformed."""

    def test_no_study_prompt_stays_silent(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[0.0, 1.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.confidence == 0.0
        assert result.proposed_action == "redirect_topic"
        assert result.inputs_snapshot["has_study_prompt"] is False

    def test_missing_prompt_embedding_stays_silent(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="What features matter most in a music app?",
            study_prompt_embedding=None,
            rolling_transcript_30s_embedding=[1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["has_prompt_embedding"] is False
        assert result.inputs_snapshot["has_transcript_embedding"] is True

    def test_missing_transcript_embedding_stays_silent(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="What features matter most in a music app?",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=None,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["has_transcript_embedding"] is False

    def test_both_embeddings_missing_stays_silent(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="Hello",
            study_prompt_embedding=None,
            rolling_transcript_30s_embedding=None,
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False

    def test_length_mismatch_stays_silent_with_audit_marker(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="Hello",
            study_prompt_embedding=[1.0, 0.0, 0.0],
            rolling_transcript_30s_embedding=[1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["length_mismatch"] is True


class TestTopicDriftFiring:
    def test_orthogonal_vectors_fire(self) -> None:
        # Cosine = 0, well below default 0.55.
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0, 0.0],
            rolling_transcript_30s_embedding=[0.0, 1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.proposed_action == "redirect_topic"
        assert result.target_participant_id is None
        assert result.confidence > 0
        assert result.inputs_snapshot["similarity"] == pytest.approx(0.0)

    def test_antiparallel_vectors_fire_with_max_confidence(self) -> None:
        # Cosine = -1, gap = 1.55 against default threshold 0.55.
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[-1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == pytest.approx(1.0)

    def test_identical_vectors_do_not_fire(self) -> None:
        # Cosine = 1.0, well above threshold.
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0, 0.0],
            rolling_transcript_30s_embedding=[1.0, 0.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["similarity"] == pytest.approx(1.0)

    def test_just_above_threshold_does_not_fire(self) -> None:
        rule = TopicDriftRule(TopicDriftConfig(min_similarity=0.5))
        # Construct vectors with cosine ~= 0.6 (above 0.5 threshold).
        # Use a known-good pair: angle 53.13° gives cosine 0.6.
        # cos(53.13°) ~= 0.6. Simpler: vectors (1, 0) and (0.6, 0.8)
        # have dot = 0.6, norms 1 and 1, cosine 0.6.
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[0.6, 0.8],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is False
        assert result.inputs_snapshot["similarity"] == pytest.approx(0.6)

    def test_just_below_threshold_fires(self) -> None:
        rule = TopicDriftRule(TopicDriftConfig(min_similarity=0.5))
        # Cosine 0.4 < 0.5; vectors (1, 0) and (0.4, sqrt(1 - 0.16)).
        import math

        b1 = 0.4
        b2 = math.sqrt(1.0 - b1 * b1)
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[b1, b2],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence >= 0.15  # floor

    def test_lower_similarity_yields_higher_confidence(self) -> None:
        rule = TopicDriftRule()
        prompt = [1.0, 0.0]

        # Pair A: cosine 0.3 (only modestly below 0.55 threshold).
        a_t = [0.3, (1.0 - 0.09) ** 0.5]
        state_a = make_session_state(
            study_prompt="x",
            study_prompt_embedding=prompt,
            rolling_transcript_30s_embedding=a_t,
        )

        # Pair B: cosine -0.5 (far below threshold).
        b_t = [-0.5, (1.0 - 0.25) ** 0.5]
        state_b = make_session_state(
            study_prompt="x",
            study_prompt_embedding=prompt,
            rolling_transcript_30s_embedding=b_t,
        )

        result_a = rule.predicate(state_a, NOW)
        result_b = rule.predicate(state_b, NOW)
        assert result_a.fired is True
        assert result_b.fired is True
        assert result_b.confidence > result_a.confidence

    def test_floored_confidence_when_just_below_threshold(self) -> None:
        rule = TopicDriftRule(TopicDriftConfig(min_similarity=0.55))
        # Cosine = 0.549, just below threshold; raw confidence ~0.0006
        # which would be invisible on the dashboard. The 0.15 floor
        # keeps a "barely fired" rule legible.
        import math

        b1 = 0.549
        b2 = math.sqrt(1.0 - b1 * b1)
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[b1, b2],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.confidence == pytest.approx(0.15)


class TestTopicDriftReasonCodes:
    def test_reason_code_includes_similarity_percentage(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0, 0.0],
            rolling_transcript_30s_embedding=[0.0, 1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.reason_codes == ["topic_drift_sim_0pct"]

    def test_reason_code_handles_negative_similarity(self) -> None:
        rule = TopicDriftRule()
        state = make_session_state(
            study_prompt="x",
            study_prompt_embedding=[1.0, 0.0],
            rolling_transcript_30s_embedding=[-1.0, 0.0],
        )
        result = rule.predicate(state, NOW)
        assert result.fired is True
        assert result.reason_codes == ["topic_drift_sim_-100pct"]


class TestTopicDriftWithMockProvider:
    """End-to-end with the mock embedder, exercising the realistic path."""

    async def test_on_topic_transcript_does_not_fire(self) -> None:
        provider = MockEmbeddingProvider(dim=32)
        prompt_text = "what features do you want in a music streaming app"
        # Mostly-overlapping tokens — the "on-topic" group is using
        # the same vocabulary as the prompt.
        transcript_text = "features in a music app"

        rule = TopicDriftRule(TopicDriftConfig(min_similarity=0.3))
        state = make_session_state(
            study_prompt=prompt_text,
            study_prompt_embedding=await provider.embed_one(prompt_text),
            rolling_transcript_30s_embedding=await provider.embed_one(transcript_text),
        )
        result = rule.predicate(state, NOW)
        # Heavy token overlap should clear the 0.3 threshold comfortably.
        assert result.inputs_snapshot["similarity"] > 0.3
        assert result.fired is False

    async def test_off_topic_transcript_fires(self) -> None:
        provider = MockEmbeddingProvider(dim=32)
        prompt_text = "what features do you want in a music streaming app"
        transcript_text = "yesterday i went hiking with my cousin near the lake"

        rule = TopicDriftRule(TopicDriftConfig(min_similarity=0.3))
        state = make_session_state(
            study_prompt=prompt_text,
            study_prompt_embedding=await provider.embed_one(prompt_text),
            rolling_transcript_30s_embedding=await provider.embed_one(transcript_text),
        )
        result = rule.predicate(state, NOW)
        # No shared tokens — mock vectors are roughly orthogonal,
        # similarity stays well under the 0.3 threshold.
        assert result.inputs_snapshot["similarity"] < 0.3
        assert result.fired is True


class TestTopicDriftConfig:
    def test_default_threshold_matches_brief(self) -> None:
        assert TopicDriftConfig().min_similarity == 0.55

    def test_threshold_clamped_to_valid_range(self) -> None:
        with pytest.raises(ValueError, match="less than or equal to 1"):
            TopicDriftConfig(min_similarity=1.5)
        with pytest.raises(ValueError, match="greater than or equal to -1"):
            TopicDriftConfig(min_similarity=-1.5)

    def test_from_rules_config_uses_overrides(self) -> None:
        cfg = RulesConfig(
            rules_version="test-v1",
            rules={"topic_drift": {"min_similarity": 0.3}},
        )
        rule = TopicDriftRule.from_rules_config(cfg)
        # Access the private config to confirm override took effect.
        assert rule._config.min_similarity == 0.3

    def test_from_rules_config_uses_defaults_when_missing(self) -> None:
        cfg = RulesConfig(rules_version="test-v1", rules={})
        rule = TopicDriftRule.from_rules_config(cfg)
        assert rule._config.min_similarity == 0.55
