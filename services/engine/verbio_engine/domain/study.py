"""`SessionConfigSnapshot` — the frozen bundle in `sessions.config_snapshot`.

At session start the runtime captures the study's `rules_config`,
`rules_version`, persona, and retention policy verbatim and writes
them into `sessions.config_snapshot` (brief §7.5). Replay reads this
snapshot back, never the live study row, so a researcher who edits a
study tomorrow doesn't retroactively change how yesterday's session is
interpreted.

`extra="allow"` is deliberate here — unlike most domain models we want
forward-compatibility when a later schema version adds a field. Old
snapshots stay parseable; new fields fall through into the model's
__pydantic_extra__ without breaking the read path. The trade-off is
no typo guard at the snapshot boundary; the per-rule configs still
have their own `extra="forbid"` so a rule misconfig surfaces at
session start exactly where it would today.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from verbio_engine.domain.rules_config import RulesConfig


class SessionConfigSnapshot(BaseModel):
    """Frozen study config at session start; round-trips through JSONB."""

    model_config = ConfigDict(extra="allow", frozen=True)

    rules_config: RulesConfig = Field(
        ...,
        description=(
            "The per-rule overrides + rules_version this session is pinned to. "
            "Replay rebuilds the registry from this exact bundle."
        ),
    )
    moderator_persona: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Opaque persona blob (voice, formality, etc.). The engine "
            "doesn't read it today; Phase 4 mouth layer will."
        ),
    )
    retention_policy: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Opaque retention blob (downsample policy, recording TTL, etc.). "
            "The engine doesn't read it today; Phase 6 will."
        ),
    )
