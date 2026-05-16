"""Rule-evaluations repository — N inserts per tick (Phase 3 L9).

Every tick writes one `decisions` row plus one `rule_evaluations` row
per rule in the registry, regardless of whether the rule fired. The
"unfired rules log too" rule is the load-bearing guarantee behind the
dashboard's "Why quiet now?" panel — a researcher must be able to see
which rules considered the moment and what they read.

API:
  - `insert_many(records)` : batch insert keyed by `decision_id`.

A single-row `insert` isn't exposed because the call-site always has
the full batch in hand (the resolver returns `len(rules)` evaluations
per call). Adding a singleton would invite drift from the brief's
invariant.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from verbio_engine.persistence.models import RuleEvaluation

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class RuleEvaluationInsert:
    """Input record for `RuleEvaluationRepo.insert_many`.

    `evaluation_id` and `decision_id` are caller-supplied UUIDs. The
    resolver already mints the decision's id up front so this batch's
    `decision_id` matches the matching `DecisionInsert.decision_id`.
    """

    evaluation_id: uuid.UUID
    decision_id: uuid.UUID
    rule_name: str
    rule_version: str
    fired: bool
    confidence: float = 0.0
    suppressed_reason: str | None = None
    predicate_inputs: dict[str, object] = field(default_factory=dict)


class RuleEvaluationRepo:
    """Async repository for the `rule_evaluations` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_many(
        self,
        records: Iterable[RuleEvaluationInsert],
    ) -> Sequence[RuleEvaluation]:
        """Persist the per-rule evaluations for one decision.

        Returns rows in input order. The caller must have already
        persisted the parent decision (this method does not insert it)
        so the FK constraint resolves at flush time.
        """
        rows = [
            RuleEvaluation(
                id=r.evaluation_id,
                decision_id=r.decision_id,
                rule_name=r.rule_name,
                rule_version=r.rule_version,
                fired=r.fired,
                suppressed_reason=r.suppressed_reason,
                predicate_inputs=dict(r.predicate_inputs),
                confidence=r.confidence,
            )
            for r in records
        ]
        if not rows:
            return rows
        self._session.add_all(rows)
        await self._session.flush(rows)
        return rows
