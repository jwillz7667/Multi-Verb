"""`verbio_engine.rules` — the rules engine.

Public surface (brief §7):
  * `Rule` Protocol + `RulePredicateResult` — every rule's shape.
  * `RulesRegistry` — the lookup table the runtime evaluates against.

Concrete rules (silence_gap, speaker_imbalance, …) land in their own
sibling modules (Phase 3 layers 2-4). Importers always go through this
barrel — direct cross-module imports between rules are forbidden so
the registry remains the single composition point.
"""

from verbio_engine.rules.cross_talk_pattern import (
    CrossTalkPatternConfig,
    CrossTalkPatternRule,
)
from verbio_engine.rules.protocol import Rule, RulePredicateResult
from verbio_engine.rules.registry import (
    DuplicateRuleError,
    RulesRegistry,
    UnknownRuleError,
)
from verbio_engine.rules.resolver import ResolverOutput, resolve
from verbio_engine.rules.silence_gap import SilenceGapConfig, SilenceGapRule
from verbio_engine.rules.speaker_imbalance import (
    SpeakerImbalanceConfig,
    SpeakerImbalanceRule,
)
from verbio_engine.rules.time_remaining_pressure import (
    TimeRemainingPressureConfig,
    TimeRemainingPressureRule,
)
from verbio_engine.rules.topic_drift import TopicDriftConfig, TopicDriftRule
from verbio_engine.rules.unheard_participant import (
    UnheardParticipantConfig,
    UnheardParticipantRule,
)
from verbio_engine.rules.v1 import V1_RULES_VERSION, build_v1_registry

__all__ = [
    "V1_RULES_VERSION",
    "CrossTalkPatternConfig",
    "CrossTalkPatternRule",
    "DuplicateRuleError",
    "ResolverOutput",
    "Rule",
    "RulePredicateResult",
    "RulesRegistry",
    "SilenceGapConfig",
    "SilenceGapRule",
    "SpeakerImbalanceConfig",
    "SpeakerImbalanceRule",
    "TimeRemainingPressureConfig",
    "TimeRemainingPressureRule",
    "TopicDriftConfig",
    "TopicDriftRule",
    "UnheardParticipantConfig",
    "UnheardParticipantRule",
    "UnknownRuleError",
    "build_v1_registry",
    "resolve",
]
