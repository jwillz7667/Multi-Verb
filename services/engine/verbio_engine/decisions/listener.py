"""Decision tick-listener — resolver → persist → publish → dispatch (P3 L10 / P4 L8).

Plugged into the tick loop alongside `PersistAndPublishListener`. Each
tick this listener:

  1. Runs `verbio_engine.rules.resolve` against the current `SessionState`
     and the in-process cooldown map.
  2. Opens one short transaction and writes the parent `decisions` row
     followed by the per-rule `rule_evaluations` rows. The two writes
     commit together so a snapshot can never reference an orphaned
     evaluations row (and vice-versa).
  3. Publishes the `decision` SSE envelope on Redis. Persist-before-
     publish is the brief's §6 invariant (audit truth first, wire
     second); failures of the Redis side are swallowed by the
     publisher itself.
  4. Updates the cooldown map for the rule that won, so the next tick's
     resolver call sees the fresh `last_won_at`.
  5. (P4 L8) When an `ExecutionDispatcher` is wired AND the decision is
     a non-silent auto decision, hands the decision off to the
     dispatcher — the actual mouth/TTS/publisher run happens in a
     background task so the tick loop never blocks (brief §6 step 6).

Shadow-mode behaviour (Phase 3): when no dispatcher is wired, the
listener is identical to its P3 L10 form — every decision lands as
`was_executed=False`. The dashboard's decision log / "Why quiet now?"
panel renders the silent-decision stream during the shadow review.

Cooldown state is *in-process*. A worker restart resets cooldowns; the
brief explicitly accepts this because mid-session restarts only happen
on incident response and the audit log records the actual ticks anyway.
A future Phase could rehydrate cooldowns from the most-recent winning
decision in Postgres if restart amnesia ever bites.

The `IdentityResolver` callable bridges the rules engine — whose
`target_participant_id` is the LiveKit identity string carried on
`ParticipantState` — to the DB column, which is a `participants.id`
UUID. Misses (resolver returns None) leave the row's target column
null so the audit trail keeps the moderator's intent even when the
DB participant row was purged for retention (the L9 SET NULL cascade
covers the retroactive case).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Protocol

from verbio_engine.logging import get_logger
from verbio_engine.persistence import (
    DecisionInsert,
    DecisionRepo,
    RuleEvaluationInsert,
    RuleEvaluationRepo,
)
from verbio_engine.realtime import decision_event
from verbio_engine.rules import resolve

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.decisions.dispatcher import ExecutionDispatcher
    from verbio_engine.domain.session_state import SessionState
    from verbio_engine.realtime import EventPublisher
    from verbio_engine.rules import RulesRegistry

log = get_logger(__name__)


class IdentityResolver(Protocol):
    """Lookup from LiveKit identity → DB participant UUID.

    Implemented by `SessionRuntime.participant_id_for`. Returning `None`
    means the runtime doesn't know this identity yet (race with the
    participant-joined upsert) or the participant has been purged — the
    DB column is then left null.
    """

    def __call__(self, identity: str) -> uuid.UUID | None: ...


class DecisionTickListener:
    """`SnapshotListener` impl: resolve → persist decision + evaluations → publish."""

    __slots__ = (
        "_cooldowns",
        "_executor_dispatcher",
        "_identity_resolver",
        "_publisher",
        "_rules",
        "_session_factory",
    )

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        publisher: EventPublisher,
        rules: RulesRegistry,
        identity_resolver: IdentityResolver,
        executor_dispatcher: ExecutionDispatcher | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._publisher = publisher
        self._rules = rules
        self._identity_resolver = identity_resolver
        # Per-session cooldown map: rule_name → wall-clock of the tick
        # the rule last *won* on. The resolver consumes this read-only;
        # we mutate it after the winner is known.
        self._cooldowns: dict[str, datetime] = {}
        # Optional execution path. When wired, non-silent auto decisions
        # are handed to the dispatcher after persist+publish; the tick
        # loop continues immediately (brief §6 step 6).
        self._executor_dispatcher = executor_dispatcher

    async def __call__(self, state: SessionState) -> None:
        output = resolve(
            state=state,
            t=state.t,
            rules=self._rules.all(),
            cooldowns=self._cooldowns,
        )
        decision = output.decision

        target_pid = self._resolve_target(decision.target_participant_id)

        decision_record = DecisionInsert(
            decision_id=decision.decision_id,
            session_id=decision.session_id,
            tick_id=decision.tick_id,
            ts=decision.timestamp,
            action=decision.action,
            source=decision.source,
            was_executed=decision.was_executed,
            cooldown_until=decision.cooldown_until,
            target_participant_id=target_pid,
            triggering_rule=decision.triggering_rule,
            researcher_id=_uuid_or_none(decision.researcher_id),
            researcher_hint=decision.researcher_hint,
            reason_codes=list(decision.reason_codes),
            reason_human=decision.reason_human,
            # The domain ModeratorDecision pins `confidence` to [0,1]
            # with default 0.0; persisting 0.0 verbatim for stay_silent
            # rows is intentional (it differentiates "no winner" from
            # NULL=unknown in the DB column).
            confidence=decision.confidence,
            suppressed_by=list(decision.suppressed_by),
            llm_prompt=decision.llm_prompt,
            llm_output=decision.llm_output,
            tts_audio_url=decision.tts_audio_url,
            spoken_at=decision.spoken_at,
        )

        eval_records = [
            RuleEvaluationInsert(
                evaluation_id=ev.evaluation_id,
                decision_id=ev.decision_id,
                rule_name=ev.rule_name,
                rule_version=ev.rule_version,
                fired=ev.fired,
                confidence=ev.confidence,
                suppressed_reason=ev.suppressed_reason,
                predicate_inputs=dict(ev.predicate_inputs),
            )
            for ev in output.rule_evaluations
        ]

        async with self._session_factory() as db, db.begin():
            decision_repo = DecisionRepo(db)
            await decision_repo.insert(decision_record)
            if eval_records:
                eval_repo = RuleEvaluationRepo(db)
                await eval_repo.insert_many(eval_records)

        # Persist-before-publish: only reach here after the transaction
        # committed. Publisher swallows its own errors so a Redis blip
        # does not abort the tick loop.
        await self._publisher.publish(decision_event(decision=decision))

        # Update cooldown only after the row is durable. If persist
        # raises before this line we leave the cooldown untouched, so
        # the next tick will re-evaluate as if the winning rule never
        # spoke — symmetric with the "no audit row" outcome.
        if decision.triggering_rule is not None:
            self._cooldowns[decision.triggering_rule] = state.t

        # P4 L8: hand non-silent auto decisions to the execution
        # dispatcher. Researcher-sourced decisions get their own path
        # in Phase 5; for now the gate is auto + non-silent.
        if (
            self._executor_dispatcher is not None
            and decision.action != "stay_silent"
            and decision.source == "auto"
        ):
            self._executor_dispatcher.dispatch(decision, state)

    def _resolve_target(self, identity: str | None) -> uuid.UUID | None:
        """LiveKit identity (str) → DB participant UUID, or None if unknown."""
        if identity is None:
            return None
        pid = self._identity_resolver(identity)
        if pid is None:
            # Either a participant the runtime hasn't upserted yet (rare
            # race) or one already purged. Persist the row with null
            # target so the rest of the audit trail survives.
            log.warning(
                "decisions.target_identity_unknown",
                identity=identity,
            )
        return pid


def _uuid_or_none(value: str | None) -> uuid.UUID | None:
    """Cast a stringly-typed researcher_id from the domain model to a real UUID.

    Phase 3 has no researcher path yet, so this is exercised only via
    tests today. Kept narrow on purpose — the domain model uses `str`
    because the type sits across the wire schema and matches the web
    side, but the DB column is a typed UUID.
    """
    if value is None:
        return None
    return uuid.UUID(value)
