"""`RulesConfig` — the per-study, per-rule configuration bundle (brief §7.5).

Pinned at session start (`sessions.config_snapshot`) and immutable for
the rest of the session's life. Replay reads this snapshot back to
reconstruct decisions faithfully; live config changes only affect
sessions that *start* after the change. Never let a config tweak
retroactively alter a historical session's interpretation.

The per-rule shape is intentionally `dict[str, Any]` here. Each rule
defines and validates its own typed config locally (see
`verbio_engine.rules.<rule_name>`) and reads the corresponding entry
from this map. That keeps the shared-types surface small and stable:
adding a new rule in a later version doesn't churn the wire schema.

`rules_version` is a free-form string the registry asserts against —
e.g. ``"v1.0"`` or ``"v1.1-experimental"``. The registry refuses to
load a session whose `rules_version` it does not recognise (brief §7.5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RulesConfig(BaseModel):
    """Frozen bundle of rule configurations for one session.

    Stored verbatim inside `sessions.config_snapshot` (alongside the
    persona, retention policy, etc.) so a replay can reconstitute the
    rule set without re-reading the study row, which may have been
    edited since.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    rules_version: str = Field(
        ...,
        description=(
            "Stable identifier for the rule-set release this session uses "
            "(e.g. 'v1.0'). The registry refuses sessions whose version "
            "it does not know about."
        ),
        min_length=1,
    )
    rules: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description=(
            "Per-rule overrides keyed by `rule.name`. Each value is a "
            "plain dict that the corresponding rule class parses into "
            "its own typed config. Missing entries mean 'use rule defaults'."
        ),
    )
