"""`Rule` Protocol + `RulePredicateResult` — brief §7.1.

Rules are first-class versioned objects. The registry never reads them
as magic globals; concrete rules attach themselves to a registry at
import time via `RulesRegistry.register(...)` and are looked up by name
when the tick loop evaluates state.

`RulePredicateResult` is the inner return value of `Rule.predicate`.
It is NOT what the dashboard sees — that's `RuleEvaluation` (brief §5.3),
which the tick loop derives from the predicate result plus suppression
state (cooldown, quietness budget, lower-priority-won). Keeping the
two shapes separate prevents an audit-trail row from accidentally
exposing transient fields that aren't part of the persisted contract.

The result is `frozen=True` so a rule predicate cannot mutate its own
output after returning it — defence in depth against a future bug
where a downstream caller stashes a reference and edits it. Predicate
results flow through several pipeline stages (resolution, persistence,
publish); freezing the boundary saves us from a "who changed this?"
investigation.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat

from verbio_engine.domain.decision import DecisionAction

if TYPE_CHECKING:
    from verbio_engine.domain.session_state import SessionState


class RulePredicateResult(BaseModel):
    """Output of one rule's `predicate(state, t)` invocation.

    `inputs_snapshot` is the raw audit material: the values the
    predicate actually read from `SessionState` for this evaluation.
    The tick loop copies it into the persisted `RuleEvaluation` row
    so a researcher can later answer "what did the engine see at
    minute 14?".
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fired: bool = Field(
        ...,
        description="True iff the predicate decided its trigger condition holds.",
    )
    confidence: NonNegativeFloat = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Self-reported certainty in [0, 1]. Used as a tiebreaker when "
            "multiple rules fire on the same tick (brief §7.3)."
        ),
    )
    target_participant_id: str | None = Field(
        default=None,
        description=(
            "Optional participant the proposed action should address; "
            "for global actions (redirect_topic, suggest_turn_taking) "
            "this is null."
        ),
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description=(
            "Structured codes that explain why the rule fired, e.g. "
            "['silence_gap_8s', 'p3_unheard_4min']. Flow through to "
            "ModeratorDecision.reason_codes verbatim."
        ),
    )
    inputs_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Frozen snapshot of state values the predicate read. "
            "Persisted in RuleEvaluation.predicate_inputs for replay."
        ),
    )
    proposed_action: DecisionAction = Field(
        ...,
        description=(
            "Action the rule wants to take iff it wins resolution. "
            "Even non-firing rules report this (the would-be action) so "
            "the audit log explains what each rule was reaching for."
        ),
    )


@runtime_checkable
class Rule(Protocol):
    """The first-class shape every rule satisfies (brief §7.1).

    Concrete rules are typically classes with `name`/`version`/`priority`/
    `default_cooldown_sec` as class-level attributes (their values don't
    change across instances), but the protocol does not require that —
    it only checks attribute presence so test stubs can vary them per
    instance.

    `name` must be globally unique within a registry — the registry
    rejects duplicates at construction. The same rule across versions
    must change its `version` string and either keep the name (replacing
    the old rule) or pick a fresh one. The `rules_version` snapshotted
    on the session determines which set of rules the runtime loads.

    A rule is one logical decision generator; resolution (cooldowns,
    quietness budget, priority sort) lives in the orchestrator, not
    the predicate.
    """

    name: str
    """Stable identifier; persisted in `RuleEvaluation.rule_name` and used
    as the resolution key. Renaming a rule = new rule from a registry
    perspective.
    """

    version: str
    """Per-rule version, persisted in `RuleEvaluation.rule_version`. Bump
    this whenever the predicate or its config interpretation changes so
    replays of older sessions don't silently shift meaning.
    """

    priority: int
    """Higher wins on resolution conflict. Ties broken by `confidence`."""

    default_cooldown_sec: float
    """Per-rule floor for "don't fire this rule again for X seconds after a
    win". The orchestrator may extend this from a researcher command but
    never shorten it.
    """

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        """Evaluate the rule against the frozen state snapshot.

        Pure function: no I/O, no side effects, no reads of mutable
        state outside `state`/`t`. That's what makes the tick loop
        deterministic and replayable.
        """
        ...
