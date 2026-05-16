"""`topic_drift` rule — brief §7.2 rule #3.

Trigger: cosine similarity between the rolling 30-second transcript
embedding and the study prompt embedding falls below `min_similarity`.
Action: `redirect_topic` (a global action — no target participant,
because the drift is collective, not anyone's fault individually).

This is the first rule that depends on the embeddings module. The
predicate stays pure: it reads two `list[float] | None` fields off
`SessionState` (the state store populates them via the
`EmbeddingProvider`) and calls `cosine_similarity`. No I/O.

Guarded states — all map to "don't fire":

  * No study prompt configured. A study without a framing prompt
    has nothing to drift from; the rule is silent.
  * Either embedding is None. State store hasn't populated yet
    (cold start, transcript still warming up) or the provider just
    failed and the field was cleared. We refuse to invent a
    drift signal in those cases — false positives in shadow mode
    tank the 70% agreement gate (brief §14 Phase 3 done-when).
  * Embedding length mismatch. A safety net for the cross-model
    case — the state store *should* refuse to write mismatched
    vectors, but if it ever does, we log and stay silent rather
    than crashing the tick loop.

Confidence: how far below the threshold the similarity sits, scaled.
Lower similarity → higher confidence we're off topic. Saturates at
0 similarity (perfect orthogonality from the prompt). Floored at
0.15 so a freshly-fired rule isn't dashboard-invisible.

Priority 35 — below the "people problems" cluster (silence_gap=50,
unheard=60, speaker_imbalance=45, cross_talk=40). Drifting onto a
tangent is annoying but rarely as acute as someone being talked
over. Long cooldown (180s) because steering back to topic is a
heavyweight social move; researchers grimace when the moderator
does it twice inside a minute.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from verbio_engine.embeddings import cosine_similarity
from verbio_engine.rules.protocol import RulePredicateResult

if TYPE_CHECKING:
    from verbio_engine.domain.rules_config import RulesConfig
    from verbio_engine.domain.session_state import SessionState


class TopicDriftConfig(BaseModel):
    """Per-study tunable for `topic_drift`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_similarity: float = Field(
        default=0.55,
        ge=-1.0,
        le=1.0,
        description=(
            "Cosine similarity threshold. If the rolling 30s transcript "
            "embedding scores below this against the study prompt "
            "embedding, the rule fires. Brief default 0.55 — well above "
            "random (cosine of two unit vectors averages 0), well below "
            "near-identical."
        ),
    )


class TopicDriftRule:
    """Fires when group discussion has drifted away from the study prompt."""

    name = "topic_drift"
    version = "v1.0"
    # Below the people-acuteness rules. Drift matters but is less
    # urgent than turn-taking failures.
    priority = 35
    default_cooldown_sec = 180.0

    def __init__(self, config: TopicDriftConfig | None = None) -> None:
        self._config = config if config is not None else TopicDriftConfig()

    @classmethod
    def from_rules_config(cls, rules_config: RulesConfig) -> TopicDriftRule:
        raw = rules_config.rules.get(cls.name, {})
        return cls(TopicDriftConfig.model_validate(raw))

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        cfg = self._config
        prompt_vec = state.study_prompt_embedding
        transcript_vec = state.rolling_transcript_30s_embedding

        inputs: dict[str, object] = {
            "min_similarity": cfg.min_similarity,
            "has_study_prompt": bool(state.study_prompt),
            "has_prompt_embedding": prompt_vec is not None,
            "has_transcript_embedding": transcript_vec is not None,
        }

        if not state.study_prompt:
            return _stay(inputs)
        if prompt_vec is None or transcript_vec is None:
            return _stay(inputs)
        if len(prompt_vec) != len(transcript_vec):
            # State-store invariant violation; refuse to fabricate a signal.
            inputs["length_mismatch"] = True
            return _stay(inputs)

        similarity = cosine_similarity(prompt_vec, transcript_vec)
        inputs["similarity"] = similarity

        if similarity >= cfg.min_similarity:
            return _stay(inputs)

        # How far below the threshold are we? At similarity = threshold,
        # gap = 0 and confidence = 0; at similarity = 0 (orthogonal),
        # confidence ~= 1.0 if threshold ~= 0.5. Allow the threshold's
        # full range as the denominator so very low thresholds still
        # produce a calibrated curve.
        gap = cfg.min_similarity - similarity
        denom = cfg.min_similarity + 1.0  # always positive; threshold ∈ [-1, 1]
        confidence = min(1.0, gap / denom) if denom > 0 else 1.0
        confidence = max(confidence, 0.15)  # floor so the dashboard shows it

        reason_code = f"topic_drift_sim_{int(similarity * 100):d}pct"

        return RulePredicateResult(
            fired=True,
            confidence=confidence,
            target_participant_id=None,  # global redirect, not aimed at anyone
            reason_codes=[reason_code],
            inputs_snapshot=inputs,
            proposed_action="redirect_topic",
        )


def _stay(inputs: dict[str, object]) -> RulePredicateResult:
    return RulePredicateResult(
        fired=False,
        confidence=0.0,
        target_participant_id=None,
        reason_codes=[],
        inputs_snapshot=inputs,
        proposed_action="redirect_topic",
    )
