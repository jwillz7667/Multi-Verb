"""Decision tick-listener — drain → resolve → maybe-override → persist → dispatch.

Phase touchpoints: P3 L10 (resolver wired), P4 L8 (executor dispatch),
P5 L2 (drain commands → audit rows), P5 L3 (manual override path),
P5 L4 (whisper override path), P5 L5 (non-spoken control plane).

Plugged into the tick loop alongside `PersistAndPublishListener`. Each
tick this listener:

  1. (P5 L2) Drains any pending researcher commands from the
     `CommandBus` and persists each as a `researcher_actions` row in
     its own short transaction. Audit-first: the row exists even if a
     later step in this tick raises. The repo is idempotent on
     `command_id` so a restart-replay produces no duplicates.
  2. (P5 L5) Applies non-spoken control commands (mute/unmute,
     pause/resume, end_session) via `apply_control_commands`. Effects
     land on the live runtime — the store mute/pause flags flip
     immediately (next-tick snapshot reflects them), and `end_session`
     is awaited so the runtime's mark-ended + tick-stop observe each
     other before this tick completes. The returned `ControlEffects`
     are consumed by the dispatch gate below to honour the
     researcher's intent *this* tick, not just the next one.
  3. Runs `verbio_engine.rules.resolve` against the current
     `SessionState` and the in-process cooldown map. The resolver
     always runs even when a manual override is present — its
     evaluations are still written so the dashboard's "Why quiet now?"
     panel can show what the rules would have done in parallel.
  4. (P5 L3 + L4) If an overriding researcher command is in the
     drained batch (force_* or whisper, FIFO from the bus), replace
     the resolver's decision with the manual / whisper one, keeping
     the resolver's decision_id so the per-rule evaluations stay
     FK-linked. Override decisions bypass cooldowns + the quietness
     budget; the researcher's call is final for this tick. Force
     commands land as `source="researcher_manual"` (mouth phrases the
     hint); whisper commands land as `source="researcher_whisper"`
     (executor sends the hint verbatim, skipping the mouth).
  5. (P5 L5) If the post-batch control effects say the moderator is
     muted, paused, or the session is ending, extend the decision's
     `suppressed_by` list with the matching reason code(s) — but only
     when the decision was non-silent in the first place; suppressing
     a `stay_silent` decision is information-free. This keeps the
     audit trail honest: researchers see "the moderator wanted to
     speak but my mute stopped it" rather than the gate being silent.
  6. Opens the decision transaction and writes the `decisions` row +
     `rule_evaluations` rows together. If an overriding command was
     processed in step 4, the matching `researcher_actions.resulting_decision_id`
     is stamped in the same transaction so the linkage is atomic with
     the decision it points at. Failure of this transaction leaves the
     audit row (written in step 1) with a null FK — the dashboard can
     show "command issued, no decision produced" for the operator.
  7. Publishes the `decision` SSE envelope on Redis. Persist-before-
     publish is the brief's §6 invariant (audit truth first, wire
     second); failures of the Redis side are swallowed by the
     publisher itself.
  8. Updates the cooldown map for the rule that won, so the next
     tick's resolver call sees the fresh `last_won_at`. Override
     decisions don't update cooldowns (no `triggering_rule`) —
     researcher overrides never debit the auto-rule budget.
  9. (P4 L8 / P5 L3 / P5 L4 / P5 L5) When an `ExecutionDispatcher` is
     wired AND the decision is non-silent AND source is `auto`,
     `researcher_manual`, or `researcher_whisper` AND no control
     effect (mute / pause / session_ended) is in force, hands the
     decision off to the dispatcher — the actual mouth/TTS/publisher
     run happens in a background task so the tick loop never blocks
     (brief §6 step 6). The executor itself branches on source to
     skip the mouth for whisper decisions.

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

from verbio_engine.commands import (
    SPOKEN_COMMAND_TYPES,
    WHISPER_COMMAND_TYPE,
    BudgetEffects,
    ControlEffects,
    apply_budget_commands,
    apply_control_commands,
    build_manual_decision,
    build_whisper_decision,
    first_overriding_command,
)
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

    from verbio_engine.commands import BudgetControl, CommandBus, RuntimeControl
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
        "_budget_control",
        "_command_bus",
        "_cooldowns",
        "_executor_dispatcher",
        "_identity_resolver",
        "_publisher",
        "_rules",
        "_runtime_control",
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
        runtime_control: RuntimeControl | None = None,
        budget_control: BudgetControl | None = None,
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
        # Optional execution path. When wired, non-silent auto,
        # researcher_manual, and researcher_whisper decisions are
        # handed to the dispatcher after persist+publish; the tick
        # loop continues immediately (brief §6 step 6).
        self._executor_dispatcher = executor_dispatcher
        # P5 L5: optional runtime control surface. When wired AND a
        # control command is in the drained batch, the listener flips
        # the runtime's mute/pause flags and (for `end_session`) marks
        # the session ended + stops the tick loop. Left None when the
        # runtime is not driving the listener (most unit tests, and
        # any setup that doesn't accept control commands).
        self._runtime_control = runtime_control
        # P5 L6: optional budget control surface. When wired AND a
        # `set_quietness_budget` command is in the drained batch, the
        # listener merges the patches FIFO and replaces the active
        # budget on the state store. Production wires the same
        # SessionRuntime for both `runtime_control` and `budget_control`;
        # the surfaces are separate Protocols so tests can fake either
        # without the other.
        self._budget_control = budget_control

    async def __call__(self, state: SessionState) -> None:  # noqa: PLR0912, PLR0915
        # PLR0912/PLR0915: this method is the brief's §6 tick pipeline
        # written top-to-bottom. Splitting it into helpers would scatter
        # the order across the file without reducing total complexity —
        # the order itself (audit → control → budget → resolve →
        # override → gate → persist → publish → dispatch) is the
        # contract.
        # P5 L2/L3/L4/L5: drain researcher commands at the top of the
        # tick so the audit row exists even if a later step in this
        # tick fails. Override commands (force_*, whisper) replace the
        # resolver's decision below; control commands (mute/pause/end)
        # mutate the runtime via `apply_control_commands`; the
        # remaining types (set_quietness_budget, flag_moment) ride
        # along as audit rows only — their semantics land in L6 / L7.
        commands: list[ResearcherCommand] = []
        if self._command_bus is not None:
            commands = await self._command_bus.drain(state.session_id)
            if commands:
                await self._persist_commands(commands)

        # P5 L5: apply non-spoken control commands. Effects land on the
        # live runtime; we also compute the post-batch effective state
        # so the dispatch gate honours the researcher's intent in *this*
        # tick (the snapshot we received was projected before commands
        # were drained). When no runtime control is wired or no control
        # commands landed, fall back to the snapshot's mute/pause flags
        # so a pre-existing mute (set out-of-band on the runtime) still
        # gates dispatch — the snapshot is always the source of truth
        # for state observed at projection time.
        if commands and self._runtime_control is not None:
            effects = await apply_control_commands(
                commands=commands,
                control=self._runtime_control,
                initial_muted=state.moderator_muted,
                initial_paused=state.is_paused,
            )
            if effects.counts:
                log.info(
                    "decisions.control_commands_applied",
                    session_id=str(state.session_id),
                    tick_id=state.tick_id,
                    muted_after=effects.muted_after,
                    paused_after=effects.paused_after,
                    end_requested=effects.end_requested,
                    counts=dict(effects.counts),
                )
        else:
            effects = ControlEffects(
                muted_after=state.moderator_muted,
                paused_after=state.is_paused,
                end_requested=False,
            )

        # P5 L6: merge any `set_quietness_budget` patches onto the
        # active budget. The new budget applies to tick T+1 — the
        # resolver at tick T already consumed the snapshot projected
        # before commands were drained, so we cannot retroactively
        # gate THIS tick on the new cap. Researchers wanting an
        # immediate clamp pair the patch with `mute_moderator`, which
        # IS gated this tick via `effects.muted_after` above.
        if commands and self._budget_control is not None:
            budget_effects = apply_budget_commands(
                commands=commands,
                control=self._budget_control,
                initial_budget=state.quietness_budget,
            )
            if budget_effects.applied:
                log.info(
                    "decisions.budget_commands_applied",
                    session_id=str(state.session_id),
                    tick_id=state.tick_id,
                    applied_count=budget_effects.applied_count,
                    max_utterances_per_10min=(budget_effects.budget_after.max_utterances_per_10min),
                    min_seconds_between_utterances=(
                        budget_effects.budget_after.min_seconds_between_utterances
                    ),
                    counts=dict(budget_effects.counts),
                )
        else:
            budget_effects = BudgetEffects(budget_after=state.quietness_budget)

        override = first_overriding_command(commands)

        output = resolve(
            state=state,
            t=state.t,
            rules=self._rules.all(),
            cooldowns=self._cooldowns,
        )
        decision = output.decision
        if override is not None:
            # Override the resolver's decision but REUSE its
            # decision_id so the parallel `rule_evaluations` remain
            # FK-linked to the row researchers actually see. Source
            # becomes `researcher_manual` (force_*) or
            # `researcher_whisper` (whisper); cooldown + budget don't
            # apply in either case.
            if override.command_type in SPOKEN_COMMAND_TYPES:
                decision = build_manual_decision(
                    command=override,
                    state=state,
                    t=state.t,
                    decision_id=output.decision.decision_id,
                )
            else:
                # Translator's `first_overriding_command` only returns
                # types in SPOKEN_COMMAND_TYPES or {WHISPER_COMMAND_TYPE},
                # so the else branch is exhaustively whisper.
                assert override.command_type == WHISPER_COMMAND_TYPE
                decision = build_whisper_decision(
                    command=override,
                    state=state,
                    t=state.t,
                    decision_id=output.decision.decision_id,
                )

        # P5 L5: stamp the control-plane gate onto non-silent decisions
        # so the audit row carries the researcher's intent ("would have
        # spoken, but I muted") rather than silently dropping the
        # would-be utterance. stay_silent decisions are left alone —
        # adding "muted" to a row the moderator wasn't going to speak
        # anyway is information-free noise on the dashboard.
        if decision.action != "stay_silent":
            extra: list[str] = []
            if effects.muted_after:
                extra.append("muted")
            if effects.paused_after:
                extra.append("paused")
            if effects.end_requested:
                extra.append("session_ended")
            if extra:
                decision = decision.model_copy(
                    update={"suppressed_by": [*decision.suppressed_by, *extra]},
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
            if override is not None:
                # Stamp the FK back onto the audit row inside the
                # decision tx so the link lands atomically with the
                # decision it points at. If this tx aborts, the audit
                # row keeps a null FK — operator sees "command issued,
                # no decision produced" and investigates the DB error.
                action_repo = ResearcherActionRepo(db)
                await action_repo.set_resulting_decision_id(
                    command_id=override.command_id,
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

        # P4 L8 / P5 L3 / P5 L4 / P5 L5: dispatch non-silent decisions
        # to the executor. `auto` is the rules engine; `researcher_manual`
        # is a force_* override (mouth phrases the hint);
        # `researcher_whisper` is verbatim text (executor's whisper
        # branch skips the mouth). All three share the dispatcher path
        # because §6 step 6's "tick loop never blocks on TTS" applies
        # to any audible output, regardless of how the text was chosen.
        # P5 L5: the post-batch effective control state gates dispatch.
        # If mute/pause/end is in force at the end of the drained
        # batch, we skip the dispatcher — the decision row already
        # carries the matching suppressed_by reason for the audit.
        if (
            self._executor_dispatcher is not None
            and decision.action != "stay_silent"
            and decision.source in {"auto", "researcher_manual", "researcher_whisper"}
            and not effects.muted_after
            and not effects.paused_after
            and not effects.end_requested
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
