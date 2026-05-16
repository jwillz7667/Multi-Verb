"""`verbio_engine.rules` — the rules engine.

Public surface (brief §7):
  * `Rule` Protocol + `RulePredicateResult` — every rule's shape.
  * `RulesRegistry` — the lookup table the runtime evaluates against.

Concrete rules (silence_gap, speaker_imbalance, …) land in their own
sibling modules (Phase 3 layers 2-4). Importers always go through this
barrel — direct cross-module imports between rules are forbidden so
the registry remains the single composition point.
"""

from verbio_engine.rules.protocol import Rule, RulePredicateResult
from verbio_engine.rules.registry import (
    DuplicateRuleError,
    RulesRegistry,
    UnknownRuleError,
)

__all__ = [
    "DuplicateRuleError",
    "Rule",
    "RulePredicateResult",
    "RulesRegistry",
    "UnknownRuleError",
]
