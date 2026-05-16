"""V1.0 rule-set composition.

One place to ask: "what's in v1?" The worker calls this at boot to
build the default registry; the runtime calls it again per-session
when a study's `RulesConfig` overrides the defaults. Tests share the
same factory so the composition stays in lockstep across boot, tick,
and replay.

`stalled_thread` is listed in brief §7.2 as v1.1-deferrable; it's not
in this registry yet (the topic-clustering work it depends on is
scheduled for Phase 5). Adding it later just means appending the
class here — no caller changes.
"""

from __future__ import annotations

from verbio_engine.domain.rules_config import RulesConfig
from verbio_engine.rules.cross_talk_pattern import CrossTalkPatternRule
from verbio_engine.rules.registry import RulesRegistry
from verbio_engine.rules.silence_gap import SilenceGapRule
from verbio_engine.rules.speaker_imbalance import SpeakerImbalanceRule
from verbio_engine.rules.time_remaining_pressure import TimeRemainingPressureRule
from verbio_engine.rules.topic_drift import TopicDriftRule
from verbio_engine.rules.unheard_participant import UnheardParticipantRule

V1_RULES_VERSION = "v1.0"


def build_v1_registry(rules_config: RulesConfig | None = None) -> RulesRegistry:
    """Build the v1.0 registry, honoring per-rule overrides from `rules_config`.

    Pass `None` to get the all-defaults registry (worker startup path),
    or a `RulesConfig` snapshotted from a study to get per-session
    tuning. The `rules_version` on the registry is always
    `V1_RULES_VERSION` — callers that need a different version should
    construct a different factory (and a different `RulesConfig`).
    """
    config = (
        rules_config if rules_config is not None else RulesConfig(rules_version=V1_RULES_VERSION)
    )
    return RulesRegistry(
        [
            SilenceGapRule.from_rules_config(config),
            SpeakerImbalanceRule.from_rules_config(config),
            CrossTalkPatternRule.from_rules_config(config),
            UnheardParticipantRule.from_rules_config(config),
            TopicDriftRule.from_rules_config(config),
            TimeRemainingPressureRule.from_rules_config(config),
        ],
        rules_version=V1_RULES_VERSION,
    )
