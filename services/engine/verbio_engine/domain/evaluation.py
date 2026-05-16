"""`RuleEvaluation` — logged for every rule, every tick (brief §5.3).

Fired *and* unfired rules persist a row. This is the table that lets
researchers ask "why didn't the moderator intervene at minute 14?".
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# `dict[str, Any]` matches the brief; the snapshot is heterogeneous because
# rule predicates read whatever subset of state they need.
PredicateInputs = dict[str, Any]


class RuleEvaluation(BaseModel):
    """One rule's evaluation result on one tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_id: UUID
    decision_id: UUID = Field(..., description="The decision this evaluation belongs to.")
    rule_name: str
    rule_version: str = Field(
        ...,
        description="Snapshotted at session start; replay must use this version.",
    )
    fired: bool
    suppressed_reason: str | None = Field(
        default=None,
        description="'cooldown' | 'lower_priority_won' | 'disabled' | 'quietness_budget'.",
    )
    predicate_inputs: PredicateInputs = Field(
        default_factory=dict,
        description="Snapshot of the values the predicate read for this tick.",
    )
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
