"""Decision tick-listener — drain → resolve → maybe-override → persist → dispatch.

Phase touchpoints: P3 L10 (resolver wired), P4 L8 (executor dispatch),
P5 L2 (drain commands → audit rows), P5 L3 (manual override path).

Plugged into the tick loop alongside `PersistAndPublishListener`. Each
tick this listener:

  1. (P5 L2) Drains any pending researcher commands from the
     `CommandBus` and persists each as a `researcher_actions` row in
     its own short transaction. Audit-first: the row exists even if a
     later step in this tick raises. The repo is idempotent on
     `command_id` so a restart-replay produces no duplicates.
  2. Runs `verbio_engine.rules.resolve` against the current
     `SessionState` and the in-process cooldown map. The resolver
     always runs even when a manual override is present — its
     evaluations are still written so the dashboard's "Why quiet now?"
     panel can show what the rules would have done in parallel.
  3. (P5 L3) If a spoken researcher command is in the drained batch,
     replace the resolver's decision with the manual one, keeping the
     resolver's decision_id so the per-rule evaluations stay FK-linked.
     Manual decisions bypass cooldowns + the quietness budget; the
     researcher's call is final for this tick.
  4. Opens the decision transaction and writes the `decisions` row +
     `rule_evaluations` rows together. If a spoken command was
     processed in step 3, the matching `researcher_actions.resulting_decision_id`
     is stamped in the same transaction so the linkage is atomic with
     the decision it points at. Failure of this transaction leaves the
     audit row (written in step 1) with a null FK — the dashboard can
     show "command issued, no decision produced" for the operator.
  5. Publishes the `decision` SSE envelope on Redis. Persist-before-
     publish is the brief's §6 invariant (audit truth first, wire
     second); failures of the Redis side are swallowed by the
     publisher itself.
  6. Updates the cooldown map for the rule that won, so the next
     tick's resolver call sees the fresh `last_won_at`. Manual
     decisions don't update cooldowns (no `triggering_rule`) —
     researcher overrides never debit the auto-rule budget.
  7. (P4 L8 / P5 L3) When an `ExecutionDispatcher` is wired AND the
     decision is non-silent AND source is `auto` or `researcher_manual`,
     hands the decision off to the dispatcher — the actual
     mouth/TTS/publisher run happens in a background task so the tick
     loop never blocks (brief §6 step 6). Whisper commands take their
     own dispatch path in P5 L4.

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

from verbio_engine.commands import build_manual_decision, first_spoken_command
from verbio_engine.logging import get_logger
from verbio_engine.persistence import (
    DecisionInsert,
    DecisionRepo,
    ResearcherActionInsert,
    ResearcherActionRepo,
    RuleEvaluationInsert,
    RuleEvaluationRepo,
)
from verbio_engine.realtime import decision_event
from verbio_engine.rules import resolve

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from verbio_engine.commands import CommandBus
    from verbio_engine.decisions.dispatcher import ExecutionDispatcher
    from verbio_engine.domain.command import ResearcherCommand
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
    """`SnapshotListener` impl: drain commands → resolve → persist + publish."""

    __slots__ = (
        "_command_bus",
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
        command_bus: CommandBus | None = None,
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
        # P5 L2: optional command bus. When wired, drained at the top of
        # each tick and persisted to `researcher_actions`. None = run the
        # auto path only (shadow-mode for researcher commands).
        self._command_bus = command_bus
        # Optional execution path. When wired, non-silent auto +
        # researcher_manual decisions are handed to the dispatcher
        # after persist+publish; the tick loop continues immediately
        # (brief §6 step 6).
        self._executor_dispatcher = executor_dispatcher

    async def __call__(self, state: SessionState) -> None:
        # P5 L2/L3: drain researcher commands at the top of the tick so
        # the audit row exists even if a later step in this tick fails.
        # Spoken commands (force_*) override the resolver's decision
        # below; non-spoken ones (mute, budget, flag, …) ride along as
        # audit rows only — their semantics land in later layers (P5 L5+).
        commands: list[ResearcherCommand] = []
        if self._command_bus is not None:
            commands = await self._command_bus.drain(state.session_id)
            if commands:
                await self._persist_commands(commands)

        spoken = first_spoken_command(commands)

        output = resolve(
            state=state,
            t=state.t,
            rules=self._rules.all(),
            cooldowns=self._cooldowns,
        )
        decision = output.decision
        if spoken is not None:
            # Override the resolver's decision but REUSE its
            # decision_id so the parallel `rule_evaluations` remain
            # FK-linked to the row researchers actually see. Source
            # becomes `researcher_manual`; cooldown + budget don't apply.
            decision = build_manual_decision(
                command=spoken,
                state=state,
                t=state.t,
                decision_id=output.decision.decision_id,
            )

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
            if spoken is not None:
                # Stamp the FK back onto the audit row inside the
                # decision tx so the link lands atomically with the
                # decision it points at. If this tx aborts, the audit
                # row keeps a null FK — operator sees "command issued,
                # no decision produced" and investigates the DB error.
                action_repo = ResearcherActionRepo(db)
                await action_repo.set_resulting_decision_id(
                    command_id=spoken.command_id,
                    decision_id=decision.decision_id,
                )

        # Persist-before-publish: only reach here after the transaction
        # committed. Publisher swallows its own errors so a Redis blip
        # does not abort the tick loop.
        await self._publisher.publish(decision_event(decision=decision))

        # Update cooldown only after the row is durable. If persist
        # raises before this line we leave the cooldown untouched, so
        # the next tick will re-evaluate as if the winning rule never
        # spoke — symmetric with the "no audit row" outcome. Manual
        # decisions have no triggering_rule, so this branch is skipped
        # automatically.
        if decision.triggering_rule is not None:
            self._cooldowns[decision.triggering_rule] = state.t

        # P4 L8 / P5 L3: dispatch non-silent decisions whose source
        # goes through the standard mouth → TTS path. `auto` is the
        # rules engine; `researcher_manual` is a force_* override
        # (still phrased by the mouth using the researcher_hint as
        # guidance). `researcher_whisper` (P5 L4) bypasses the mouth
        # and has its own dispatch path — not handled here.
        if (
            self._executor_dispatcher is not None
            and decision.action != "stay_silent"
            and decision.source in {"auto", "researcher_manual"}
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

    async def _persist_commands(self, commands: list[ResearcherCommand]) -> None:
        """Write each drained command to `researcher_actions` (audit-first).

        One transaction for the whole batch — a batch is typically 0-3
        commands so the open-tx duration is negligible. The repo is
        idempotent on `command_id`, so a re-drain after restart is a
        safe no-op rather than producing duplicate audit rows. All rows
        land with `resulting_decision_id=null`; if a spoken command in
        the batch overrides the resolver, the FK is stamped in the
        decision transaction so the link is atomic with the decision.

        Malformed `researcher_id` (not a UUID) gets logged and skipped;
        the bus's typed validation should have already caught this, so
        hitting the warning branch indicates the wire shape evolved.

        Persistence failures are logged but not re-raised — the
        moderator must keep running even if Postgres has hiccuped on
        the audit write. A follow-up tick will re-drain and the
        idempotency check on the next attempt will avoid a duplicate
        row when Postgres recovers.
        """
        records: list[ResearcherActionInsert] = []
        for cmd in commands:
            try:
                researcher_uuid = uuid.UUID(cmd.researcher_id)
            except ValueError:
                log.warning(
                    "commands.invalid_researcher_id",
                    session_id=str(cmd.session_id),
                    command_id=str(cmd.command_id),
                    researcher_id=cmd.researcher_id,
                )
                continue
            records.append(
                ResearcherActionInsert(
                    command_id=cmd.command_id,
                    session_id=cmd.session_id,
                    researcher_id=researcher_uuid,
                    ts=cmd.issued_at,
                    command_type=cmd.command_type,
                    # `payload` is a `dict[str, Any]` on the wire; copy
                    # so the audit row is decoupled from the producer's
                    # in-memory object.
                    payload=dict(cmd.payload) if cmd.payload else None,
                ),
            )
        if not records:
            return

        try:
            async with self._session_factory() as db, db.begin():
                repo = ResearcherActionRepo(db)
                for record in records:
                    await repo.insert(record)
        except Exception as exc:
            log.exception(
                "commands.persist_failed",
                count=len(records),
                error=str(exc),
            )


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
