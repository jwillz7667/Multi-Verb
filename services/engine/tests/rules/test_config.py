"""`RulesConfig` validation tests.

Two contracts pinned here:

  * The wire shape (frozen, extra=forbid, non-empty rules_version) so
    a malformed `sessions.config_snapshot` is caught before it reaches
    the registry.
  * The per-rule `dict[str, Any]` map is preserved verbatim — rules
    parse their own slice locally, so the top-level model must not
    silently coerce or filter sub-keys.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from verbio_engine.domain import RulesConfig


def test_accepts_minimal_config_with_no_rule_overrides() -> None:
    cfg = RulesConfig(rules_version="v1.0")
    assert cfg.rules_version == "v1.0"
    assert cfg.rules == {}


def test_preserves_per_rule_config_verbatim() -> None:
    cfg = RulesConfig(
        rules_version="v1.0",
        rules={
            "silence_gap": {"threshold_sec": 12.0, "cooldown_sec": 60.0},
            "speaker_imbalance": {"dominance_factor": 2.5, "underspeaking_factor": 0.3},
        },
    )
    assert cfg.rules["silence_gap"]["threshold_sec"] == 12.0
    assert cfg.rules["speaker_imbalance"]["underspeaking_factor"] == 0.3


def test_rejects_blank_rules_version() -> None:
    with pytest.raises(ValidationError, match="rules_version"):
        RulesConfig(rules_version="")


def test_rejects_unknown_top_level_keys() -> None:
    # extra='forbid' — typos in stored snapshots should surface as
    # validation errors rather than silently dropped fields.
    with pytest.raises(ValidationError, match="Extra inputs"):
        RulesConfig.model_validate(
            {
                "rules_version": "v1.0",
                "rules": {},
                "typo_field": "oops",
            },
        )


def test_is_frozen() -> None:
    cfg = RulesConfig(rules_version="v1.0")
    with pytest.raises(ValidationError):
        cfg.rules_version = "v2.0"  # type: ignore[misc]


def test_round_trips_through_model_dump() -> None:
    # The snapshot is stored as JSONB in Postgres via `model_dump(mode="json")`;
    # we round-trip to verify nothing about the per-rule dicts changes shape.
    original = RulesConfig(
        rules_version="v1.0",
        rules={
            "silence_gap": {"threshold_sec": 8.0},
            "topic_drift": {"similarity_threshold": 0.55, "nested": {"k": [1, 2, 3]}},
        },
    )
    dumped = original.model_dump(mode="json")
    restored = RulesConfig.model_validate(dumped)
    assert restored == original
    assert restored.rules["topic_drift"]["nested"] == {"k": [1, 2, 3]}
