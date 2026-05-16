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
from verbio_engine.rules.silence_gap import SilenceGapConfig, SilenceGapRule
from verbio_engine.rules.unheard_participant import (
    UnheardParticipantConfig,
    UnheardParticipantRule,
)

__all__ = [
    "CrossTalkPatternConfig",
    "CrossTalkPatternRule",
    "DuplicateRuleError",
    "Rule",
    "RulePredicateResult",
    "RulesRegistry",
    "SilenceGapConfig",
    "SilenceGapRule",
    "UnheardParticipantConfig",
    "UnheardParticipantRule",
    "UnknownRuleError",
]
