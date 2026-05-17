"""Unit tests for `DecisionTickListener` (Phase 3 L10).

Exercises the listener with fakes so the contract is observable without
booting Postgres or Redis:

  * Resolver is called with the in-process cooldown map and state.
  * Decision row + per-rule evaluation rows are both written under the
    same transaction (so a crash mid-tick never leaves orphaned audit
    rows in either direction).
  * The published envelope's `id` matches the persisted decision id.
  * Cooldown updates AFTER persist commits — a persist failure must not
    leak cooldown state and silence the rule next tick.
  * LiveKit identity → DB UUID translation happens on the listener path,
    not at the resolver layer.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from verbio_engine.decisions import DecisionTickListener
from verbio_engine.domain import ParticipantState, QuietnessBudget, SessionState
from verbio_engine.domain.command import ResearcherCommand
from verbio_engine.persistence import Decision, ResearcherAction, RuleEvaluation
from verbio_engine.realtime import DecisionEventEnvelope
from verbio_engine.rules import RulePredicateResult, RulesRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from verbio_engine.rules import Rule


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _RecordingAction:
    """One observable event during a listener invocation.

    Kinds observed: "decision_add", "researcher_action_add",
    "researcher_action_fk_update" (P5 L3 FK stamping), "evals_add",
    "flush", "publish".
    """

    kind: str
    payload: object


@dataclass(slots=True)
class _FakeResult:
    """Stand-in for the SQLAlchemy `Result` returned by AsyncSession.execute.

    Two execute paths in the listener:
      * SELECT (researcher_actions idempotency check): caller uses
        `.scalar_one_or_none()`. The fake reports "no row exists yet"
        so the repo proceeds to INSERT.
      * UPDATE (set_resulting_decision_id, P5 L3): caller reads
        `.rowcount`. The fake reports a single row touched so the
        listener's stamping logic considers the update successful.
    """

    rowcount: int = 1

    def scalar_one_or_none(self) -> object | None:
        return None


@dataclass(slots=True)
class _FakeAsyncSession:
    """Stand-in for SQLAlchemy AsyncSession that records add / add_all / flush."""

    actions: list[_RecordingAction]
    fail_on_decision_flush: bool = False
    _exited: bool = False

    def add(self, instance: object) -> None:
        if isinstance(instance, Decision):
            self.actions.append(_RecordingAction("decision_add", instance))
        elif isinstance(instance, ResearcherAction):
            self.actions.append(_RecordingAction("researcher_action_add", instance))
        else:
            self.actions.append(_RecordingAction("other_add", instance))

    def add_all(self, instances: list[RuleEvaluation]) -> None:
        # Record a *copy* of the input list so subsequent mutations by
        # the SUT don't retroactively edit the assertion view.
        self.actions.append(_RecordingAction("evals_add", list(instances)))

    async def flush(self, instances: list[object] | None = None) -> None:
        if (
            self.fail_on_decision_flush
            and instances is not None
            and any(isinstance(i, Decision) for i in instances)
        ):
            msg = "simulated DB outage"
            raise RuntimeError(msg)
        self.actions.append(_RecordingAction("flush", instances or []))

    async def execute(self, stmt: object) -> _FakeResult:
        # The researcher_actions repo issues either a SELECT
        # (idempotency check before INSERT) or an UPDATE (P5 L3
        # FK stamping). We sniff which by walking SQLAlchemy's
        # statement attributes; matching the SUT's repo internals
        # rather than the SQL class lets the fake stay decoupled
        # from the import surface.
        is_update = type(stmt).__name__ == "Update"
        if is_update:
            self.actions.append(_RecordingAction("researcher_action_fk_update", stmt))
        return _FakeResult()

    def begin(self) -> object:
        outer = self

        class _Tx:
            async def __aenter__(self) -> _FakeAsyncSession:
                return outer

            async def __aexit__(self, *_exc: object) -> None:
                return None

        return _Tx()


@dataclass(slots=True)
class _FakeSessionFactory:
    actions: list[_RecordingAction]
    fail_on_decision_flush: bool = False
    spawned: list[_FakeAsyncSession] = field(default_factory=list)

    def __call__(self) -> object:
        sess = _FakeAsyncSession(
            actions=self.actions,
            fail_on_decision_flush=self.fail_on_decision_flush,
        )
        self.spawned.append(sess)

        @asynccontextmanager
        async def _cm() -> AsyncIterator[_FakeAsyncSession]:
            try:
                yield sess
            finally:
                sess._exited = True

        return _cm()


@dataclass(slots=True)
class _FakePublisher:
    actions: list[_RecordingAction]
    last_event: object | None = None

    async def publish(self, event: object) -> int:
        self.last_event = event
        self.actions.append(_RecordingAction("publish", event))
        return 1

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class _StubRule:
    """Minimal `Rule` Protocol impl that emits a fixed `RulePredicateResult`.

    Captures every invocation so tests can assert the cooldown map the
    resolver passed down (`fires_after` simulates a rule that only fires
    on or after a configured tick_id).
    """

    name: str
    version: str = "v1.0"
    priority: int = 50
    default_cooldown_sec: float = 60.0
    proposed_action: str = "prompt_participant"
    target: str | None = None
    confidence: float = 0.8
    fires_after_tick: int = 0
    seen_ticks: list[int] = field(default_factory=list)

    def predicate(self, state: SessionState, t: datetime) -> RulePredicateResult:
        _ = t  # unused; predicates may consult `t`, this stub does not.
        self.seen_ticks.append(state.tick_id)
        return RulePredicateResult(
            fired=state.tick_id >= self.fires_after_tick,
            confidence=self.confidence,
            target_participant_id=self.target,
            reason_codes=[f"{self.name}_stub"],
            inputs_snapshot={"tick_id": state.tick_id},
            proposed_action=self.proposed_action,  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SESSION_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TICK_ZERO = datetime(2026, 5, 16, 12, 30, 0, tzinfo=UTC)


def _state(
    *,
    tick_id: int = 0,
    t: datetime | None = None,
    participants: dict[str, ParticipantState] | None = None,
    moderator_muted: bool = False,
    is_paused: bool = False,
) -> SessionState:
    when = t if t is not None else TICK_ZERO + timedelta(milliseconds=500 * tick_id)
    return SessionState(
        session_id=SESSION_ID,
        tick_id=tick_id,
        t=when,
        started_at=TICK_ZERO,
        elapsed_sec=float(tick_id) * 0.5,
        participants=participants or {},
        currently_speaking_count=0,
        silence_run_sec=0.0,
        quietness_budget=QuietnessBudget(),
        moderator_muted=moderator_muted,
        is_paused=is_paused,
    )


def _registry(*rules: Rule) -> RulesRegistry:
    return RulesRegistry(rules, rules_version="v1.0")


@dataclass(slots=True)
class _RecordingDispatcher:
    """Stand-in for ExecutionDispatcher — captures every dispatch() call."""

    dispatched: list[tuple[object, object]] = field(default_factory=list)

    def dispatch(self, decision: object, state: object) -> None:
        self.dispatched.append((decision, state))


@dataclass(slots=True)
class _FakeCommandBus:
    """Stand-in for `CommandBus` — yields a scripted batch per session per drain.

    `queues[session_id]` is a list of batches; each `drain()` call pops the
    front batch and returns it. When the queue is empty the bus reports
    "no commands" so a multi-tick test exhausts gracefully.
    """

    queues: dict[uuid.UUID, list[list[ResearcherCommand]]] = field(default_factory=dict)
    drain_calls: list[uuid.UUID] = field(default_factory=list)

    async def drain(self, session_id: uuid.UUID) -> list[ResearcherCommand]:
        self.drain_calls.append(session_id)
        queue = self.queues.get(session_id)
        if not queue:
            return []
        return queue.pop(0)

    async def aclose(self) -> None:
        return None


def _researcher_command(
    *,
    session_id: uuid.UUID,
    command_type: str = "set_quietness_budget",
    researcher_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> ResearcherCommand:
    return ResearcherCommand(
        command_id=uuid.uuid4(),
        session_id=session_id,
        researcher_id=researcher_id or str(uuid.uuid4()),
        issued_at=datetime.now(UTC),
        command_type=command_type,  # type: ignore[arg-type]
        payload=payload if payload is not None else {"max_utterances_per_10min": 2},
    )


def _build(
    *,
    rules: RulesRegistry,
    identity_resolver: object = None,
    fail_on_decision_flush: bool = False,
    executor_dispatcher: _RecordingDispatcher | None = None,
    command_bus: _FakeCommandBus | None = None,
    runtime_control: object = None,
) -> tuple[DecisionTickListener, list[_RecordingAction], _FakeSessionFactory, _FakePublisher]:
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions, fail_on_decision_flush=fail_on_decision_flush)
    publisher = _FakePublisher(actions=actions)
    resolver = identity_resolver or (lambda _identity: None)
    listener = DecisionTickListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,
        rules=rules,
        identity_resolver=resolver,  # type: ignore[arg-type]
        command_bus=command_bus,
        executor_dispatcher=executor_dispatcher,  # type: ignore[arg-type]
        runtime_control=runtime_control,  # type: ignore[arg-type]
    )
    return listener, actions, factory, publisher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_quiet_tick_persists_stay_silent_and_logs_all_rules() -> None:
    """Even when no rule fires, every rule's evaluation is persisted.

    This is the brief's load-bearing 'why didn't it speak?' guarantee:
    each rule's verdict lands in `rule_evaluations` every tick.
    """
    quiet_rule = _StubRule(name="a_quiet", fires_after_tick=999)
    other_rule = _StubRule(name="z_other", fires_after_tick=999)
    listener, actions, _, publisher = _build(rules=_registry(quiet_rule, other_rule))

    await listener(_state(tick_id=0))

    decision_adds = [a for a in actions if a.kind == "decision_add"]
    eval_adds = [a for a in actions if a.kind == "evals_add"]
    publishes = [a for a in actions if a.kind == "publish"]

    assert len(decision_adds) == 1
    assert len(eval_adds) == 1
    assert len(publishes) == 1

    persisted_decision = decision_adds[0].payload
    assert isinstance(persisted_decision, Decision)
    assert persisted_decision.action == "stay_silent"
    assert persisted_decision.triggering_rule is None
    assert persisted_decision.was_executed is False

    eval_rows = eval_adds[0].payload
    assert isinstance(eval_rows, list)
    assert {r.rule_name for r in eval_rows} == {"a_quiet", "z_other"}
    assert all(r.decision_id == persisted_decision.id for r in eval_rows)

    envelope = publishes[0].payload
    assert isinstance(envelope, DecisionEventEnvelope)
    assert envelope.id == str(persisted_decision.id)


async def test_firing_rule_persists_decision_and_updates_cooldown() -> None:
    """A firing rule wins resolution and its cooldown is bumped post-commit."""
    firing = _StubRule(
        name="firing_rule",
        fires_after_tick=0,
        proposed_action="prompt_participant",
        confidence=0.9,
    )
    listener, actions, _, _ = _build(rules=_registry(firing))

    await listener(_state(tick_id=0))

    persisted = next(a.payload for a in actions if a.kind == "decision_add")
    assert isinstance(persisted, Decision)
    assert persisted.action == "prompt_participant"
    assert persisted.triggering_rule == "firing_rule"
    assert persisted.confidence == pytest.approx(0.9)
    assert persisted.was_executed is False

    # Cooldown was bumped — the second tick at the same `t` must result
    # in stay_silent because the resolver sees the rule as cooldown-locked.
    actions.clear()
    await listener(_state(tick_id=1, t=TICK_ZERO + timedelta(seconds=10)))
    second = next(a.payload for a in actions if a.kind == "decision_add")
    assert isinstance(second, Decision)
    assert second.action == "stay_silent"
    assert second.triggering_rule is None
    # The cooldown reason is surfaced so the dashboard can explain the silence.
    assert "cooldown" in second.suppressed_by


async def test_persist_failure_propagates_and_no_publish_or_cooldown_update() -> None:
    """Audit invariant: a failed persist must not publish nor advance cooldowns."""
    firing = _StubRule(name="firing_rule", fires_after_tick=0)
    listener, actions, _, publisher = _build(
        rules=_registry(firing),
        fail_on_decision_flush=True,
    )

    with pytest.raises(RuntimeError, match="simulated DB outage"):
        await listener(_state(tick_id=0))

    assert all(a.kind != "publish" for a in actions)
    assert publisher.last_event is None

    # Cooldown stays empty — the next tick should still see the rule as fireable.
    # We assert by running a second tick (with a passing session this time)
    # and observing the firing rule wins (not blocked by cooldown).
    actions.clear()
    # Swap in a fresh non-failing factory by rebuilding the listener with
    # the existing rules registry — this proves cooldown isn't smuggled
    # through any out-of-band channel besides the listener's own state.
    fresh_actions: list[_RecordingAction] = []
    new_factory = _FakeSessionFactory(actions=fresh_actions)
    new_publisher = _FakePublisher(actions=fresh_actions)
    # Re-use the same listener instance to prove its _cooldowns map wasn't
    # mutated by the failed tick.
    listener._session_factory = new_factory  # type: ignore[assignment]
    listener._publisher = new_publisher

    await listener(_state(tick_id=1, t=TICK_ZERO + timedelta(seconds=10)))
    second = next(a.payload for a in fresh_actions if a.kind == "decision_add")
    assert isinstance(second, Decision)
    assert second.action == "prompt_participant"
    assert second.triggering_rule == "firing_rule"


async def test_identity_resolver_translates_target_to_db_uuid() -> None:
    """The resolver maps LiveKit identity (str) → participants.id (UUID)."""
    db_uuid = uuid.uuid4()

    def resolver(identity: str) -> uuid.UUID | None:
        return db_uuid if identity == "p-alice" else None

    firing = _StubRule(
        name="firing_rule",
        fires_after_tick=0,
        target="p-alice",
        proposed_action="prompt_participant",
    )
    listener, actions, _, _ = _build(
        rules=_registry(firing),
        identity_resolver=resolver,
    )

    participants = {
        "p-alice": ParticipantState(  # type: ignore[call-arg]
            participant_id="p-alice",
            display_name="Alice",
            joined_at=TICK_ZERO,
        ),
    }
    await listener(_state(tick_id=0, participants=participants))

    persisted = next(a.payload for a in actions if a.kind == "decision_add")
    assert isinstance(persisted, Decision)
    assert persisted.target_participant_id == db_uuid


async def test_unknown_identity_leaves_target_null_but_keeps_decision() -> None:
    """A target the runtime can't translate (purge or race) leaves the column null.

    The brief's audit invariant prefers preserving the moderator's intent
    (action + triggering_rule) over dropping the row entirely.
    """
    firing = _StubRule(
        name="firing_rule",
        fires_after_tick=0,
        target="p-unknown",
        proposed_action="prompt_participant",
    )
    listener, actions, _, _ = _build(
        rules=_registry(firing),
        identity_resolver=lambda _identity: None,
    )

    participants = {
        "p-unknown": ParticipantState(  # type: ignore[call-arg]
            participant_id="p-unknown",
            display_name="Ghost",
            joined_at=TICK_ZERO,
        ),
    }
    await listener(_state(tick_id=0, participants=participants))

    persisted = next(a.payload for a in actions if a.kind == "decision_add")
    assert isinstance(persisted, Decision)
    assert persisted.target_participant_id is None
    assert persisted.action == "prompt_participant"
    assert persisted.triggering_rule == "firing_rule"


async def test_each_tick_opens_its_own_transaction() -> None:
    """One session per tick — the listener must not multiplex across calls."""
    rule = _StubRule(name="dummy", fires_after_tick=999)
    listener, _, factory, _ = _build(rules=_registry(rule))

    for tick in range(4):
        await listener(_state(tick_id=tick, t=TICK_ZERO + timedelta(seconds=tick)))

    assert len(factory.spawned) == 4
    assert all(s._exited for s in factory.spawned)


async def test_publish_envelope_carries_full_moderator_decision() -> None:
    """SSE consumers receive the canonical `ModeratorDecision` payload."""
    firing = _StubRule(
        name="firing_rule",
        fires_after_tick=0,
        proposed_action="redirect_topic",
        confidence=0.6,
    )
    listener, actions, _, _ = _build(rules=_registry(firing))

    await listener(_state(tick_id=3))

    envelope = next(a.payload for a in actions if a.kind == "publish")
    assert isinstance(envelope, DecisionEventEnvelope)
    assert envelope.session_id == SESSION_ID
    assert envelope.payload.decision.action == "redirect_topic"
    assert envelope.payload.decision.triggering_rule == "firing_rule"
    assert envelope.payload.decision.tick_id == 3
    assert envelope.payload.decision.confidence == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Execution dispatcher gating (P4 L8)
# ---------------------------------------------------------------------------


class TestDispatcherGating:
    """The dispatcher is wired in P4 L8 only for non-silent auto decisions.

    Researcher commands get their own execution path in a later phase, and
    silent ticks have nothing to say. Shadow-mode runs (no dispatcher
    injected) must remain identical to P3 L10.
    """

    async def test_non_silent_auto_decision_is_dispatched(self) -> None:
        firing = _StubRule(
            name="firing_rule",
            fires_after_tick=0,
            proposed_action="prompt_participant",
        )
        dispatcher = _RecordingDispatcher()
        listener, _actions, _factory, _pub = _build(
            rules=_registry(firing),
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0))

        assert len(dispatcher.dispatched) == 1
        dispatched_decision, dispatched_state = dispatcher.dispatched[0]
        # Decision pulled straight from the resolver; state is the tick input.
        assert dispatched_decision.action == "prompt_participant"  # type: ignore[attr-defined]
        assert dispatched_decision.source == "auto"  # type: ignore[attr-defined]
        assert dispatched_state.tick_id == 0  # type: ignore[attr-defined]

    async def test_stay_silent_is_not_dispatched(self) -> None:
        # No rule fires → stay_silent default → no audio to render.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        dispatcher = _RecordingDispatcher()
        listener, _actions, _factory, _pub = _build(
            rules=_registry(quiet),
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0))

        assert dispatcher.dispatched == []

    async def test_shadow_mode_skips_dispatch_when_unwired(self) -> None:
        # No dispatcher injected — pre-P4 behaviour: rows persist + SSE
        # publishes, but no audio path runs.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        listener, actions, _factory, _pub = _build(rules=_registry(firing))

        await listener(_state(tick_id=0))

        # Decision + evals + publish all happen, but no dispatch can be
        # observed since we didn't inject one. The persisted row stays
        # `was_executed=False`, which is the shadow-mode invariant.
        decision_payload = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(decision_payload, Decision)
        assert decision_payload.was_executed is False

    async def test_persist_failure_skips_dispatch(self) -> None:
        # If the DB write blows up the listener raises before reaching
        # the dispatcher hand-off — no audio for an un-audited decision.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        dispatcher = _RecordingDispatcher()
        listener, _actions, _factory, _pub = _build(
            rules=_registry(firing),
            fail_on_decision_flush=True,
            executor_dispatcher=dispatcher,
        )

        with pytest.raises(RuntimeError, match="simulated DB outage"):
            await listener(_state(tick_id=0))

        assert dispatcher.dispatched == []


# ---------------------------------------------------------------------------
# Command drain (P5 L2)
# ---------------------------------------------------------------------------


class TestCommandDrain:
    """Researcher commands drain at the top of each tick and persist to
    `researcher_actions` regardless of whether the rules path fires.

    L2 still keeps the rules path authoritative — translation of commands
    into a `ModeratorDecision` is P5 L3. The L2 contract is: the audit
    row exists by the time the tick finishes, idempotently and even if
    the rest of the tick raises.
    """

    async def test_no_bus_runs_zero_drains(self) -> None:
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        listener, actions, factory, _ = _build(rules=_registry(quiet))

        await listener(_state(tick_id=0))

        # Only the decision/eval session was opened — no separate commands tx.
        assert len(factory.spawned) == 1
        assert all(a.kind != "researcher_action_add" for a in actions)

    async def test_empty_drain_skips_extra_transaction(self) -> None:
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        bus = _FakeCommandBus()
        listener, _actions, factory, _ = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        assert bus.drain_calls == [SESSION_ID]
        # No commands -> no second session opened for the commands path.
        assert len(factory.spawned) == 1

    async def test_drained_commands_persist_to_researcher_actions(self) -> None:
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        commands = [
            _researcher_command(
                session_id=SESSION_ID,
                command_type="flag_moment",
                payload={"note": "important"},
            ),
            _researcher_command(
                session_id=SESSION_ID,
                command_type="set_quietness_budget",
                payload={"max_utterances_per_10min": 1},
            ),
        ]
        bus = _FakeCommandBus(queues={SESSION_ID: [commands]})
        listener, actions, factory, _ = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        # First session opens for command persistence, second for the decision tx.
        assert len(factory.spawned) == 2
        persisted = [a.payload for a in actions if a.kind == "researcher_action_add"]
        assert len(persisted) == 2
        assert all(isinstance(p, ResearcherAction) for p in persisted)
        rows = [p for p in persisted if isinstance(p, ResearcherAction)]
        assert {p.command_type for p in rows} == {"flag_moment", "set_quietness_budget"}
        # Each row's PK is the wire-shape command_id (idempotency anchor).
        assert {p.id for p in rows} == {c.command_id for c in commands}
        # Payload survives the dict() copy and lands on the row verbatim.
        assert any(p.payload == {"note": "important"} for p in rows)

    async def test_invalid_researcher_id_is_skipped_not_raised(self) -> None:
        """A non-UUID researcher_id can't violate the typed DB column; skip the row."""
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        bad = _researcher_command(
            session_id=SESSION_ID,
            command_type="flag_moment",
            researcher_id="not-a-uuid",
            payload={},
        )
        good = _researcher_command(
            session_id=SESSION_ID,
            command_type="flag_moment",
            payload={"note": "good one"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[bad, good]]})
        listener, actions, _factory, _ = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = [a.payload for a in actions if a.kind == "researcher_action_add"]
        # Only the valid researcher_id command lands.
        assert len(persisted) == 1
        row = persisted[0]
        assert isinstance(row, ResearcherAction)
        assert row.id == good.command_id

    async def test_drain_runs_before_rule_resolution(self) -> None:
        """The bus must be drained before the rules path opens its DB tx.

        This matters for §6 step 2 — if a tick later short-circuits (e.g.
        the rules path raises), the audit row for the command still landed
        first.
        """
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="flag_moment",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, factory, _ = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        # Order of session opens: commands first, then the decision tx.
        kinds = [a.kind for a in actions]
        researcher_idx = kinds.index("researcher_action_add")
        decision_idx = kinds.index("decision_add")
        assert researcher_idx < decision_idx
        assert len(factory.spawned) == 2


# ---------------------------------------------------------------------------
# Manual override (P5 L3)
# ---------------------------------------------------------------------------


class TestManualOverride:
    """Spoken researcher commands (force_*) override the resolver decision.

    The drained spoken command becomes the moderator's decision for this
    tick — bypassing cooldowns and the quietness budget — while the
    resolver still runs and writes its rule_evaluations so the dashboard
    can show what the rules would have done in parallel. The audit row
    for the spoken command gets its `resulting_decision_id` stamped in
    the same transaction as the decision insert.
    """

    async def test_force_prompt_overrides_resolver_decision(self) -> None:
        # The resolver would have chosen `redirect_topic` from a firing
        # rule, but the researcher pressed force_prompt — that wins.
        firing = _StubRule(
            name="firing_rule",
            fires_after_tick=0,
            proposed_action="redirect_topic",
        )
        target = uuid.uuid4()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={
                "prompt": "ask Maria what she meant by too expensive",
                "target_participant_id": str(target),
            },
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "prompt_participant"
        assert persisted.source == "researcher_manual"
        assert persisted.triggering_rule is None
        assert persisted.researcher_id == uuid.UUID(cmd.researcher_id)
        assert persisted.researcher_hint == "ask Maria what she meant by too expensive"
        assert persisted.reason_codes == ["researcher_command:force_prompt"]
        assert persisted.confidence == pytest.approx(1.0)
        assert persisted.suppressed_by == []

    async def test_force_redirect_overrides_with_topic_as_hint(self) -> None:
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_redirect",
            payload={"topic": "let's talk about the price point"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "redirect_topic"
        assert persisted.source == "researcher_manual"
        assert persisted.researcher_hint == "let's talk about the price point"

    async def test_force_summary_with_no_focus_translates_to_null_hint(self) -> None:
        # `focus` is optional on force_summary; absence means
        # "summarise the full thread", surfaced as a null hint.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_summary",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "summarize_thread"
        assert persisted.source == "researcher_manual"
        assert persisted.researcher_hint is None

    async def test_override_reuses_resolver_decision_id_for_eval_fk_consistency(self) -> None:
        # The brief's "Why quiet now?" panel filters rule_evaluations by
        # decision_id. The override must keep the resolver's decision_id
        # so the eval rows stay linked to the decision researchers see.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_redirect",
            payload={"topic": "price"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        decision = next(a.payload for a in actions if a.kind == "decision_add")
        eval_rows = next(a.payload for a in actions if a.kind == "evals_add")
        assert isinstance(decision, Decision)
        assert isinstance(eval_rows, list)
        # Every eval row points at the same decision id the researcher sees.
        assert all(r.decision_id == decision.id for r in eval_rows)

    async def test_override_stamps_resulting_decision_id_on_audit_row(self) -> None:
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={"prompt": "ask the quiet participant for input"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        # Exactly one FK update — for the spoken command.
        fk_updates = [a for a in actions if a.kind == "researcher_action_fk_update"]
        assert len(fk_updates) == 1
        # Ordering: insert audit row first (T1), then decision + FK
        # stamp (T2). The stamp must come *after* the decision insert
        # within T2 so the FK can resolve.
        kinds = [a.kind for a in actions]
        assert kinds.index("researcher_action_add") < kinds.index("decision_add")
        assert kinds.index("decision_add") < kinds.index("researcher_action_fk_update")

    async def test_non_spoken_command_does_not_override_resolver(self) -> None:
        # A flag_moment audits as a researcher_actions row but the
        # resolver's decision stands. No FK update either — flag has no
        # decision linkage.
        firing = _StubRule(
            name="firing_rule",
            fires_after_tick=0,
            proposed_action="prompt_participant",
        )
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="flag_moment",
            payload={"note": "interesting"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.source == "auto"
        assert persisted.triggering_rule == "firing_rule"
        # No FK update — flag_moment doesn't link to a decision.
        assert not any(a.kind == "researcher_action_fk_update" for a in actions)

    async def test_first_spoken_wins_under_fifo_when_multiple_drained(self) -> None:
        # Brief §11.3: the command bus is ordered. If two force_*
        # arrive in one tick, the earlier one is the decision; the
        # later one is logged in researcher_actions with no linkage.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        first = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_redirect",
            payload={"topic": "earlier"},
        )
        second = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={"prompt": "later"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[first, second]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "redirect_topic"
        assert persisted.researcher_hint == "earlier"
        # Only the first spoken's audit row gets the FK stamp.
        fk_updates = [a for a in actions if a.kind == "researcher_action_fk_update"]
        assert len(fk_updates) == 1

    async def test_manual_decision_is_dispatched_to_executor(self) -> None:
        # The P4 L8 gate was `source == "auto"`. P5 L3 broadens it to
        # `{"auto", "researcher_manual"}` so the force_* path also runs
        # through the mouth/TTS pipeline (the mouth uses
        # researcher_hint to phrase what researchers asked for).
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={"prompt": "ask alice for her take"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        dispatcher = _RecordingDispatcher()
        listener, _actions, _factory, _pub = _build(
            rules=_registry(quiet),
            command_bus=bus,
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0))

        assert len(dispatcher.dispatched) == 1
        dispatched_decision, _dispatched_state = dispatcher.dispatched[0]
        assert dispatched_decision.action == "prompt_participant"  # type: ignore[attr-defined]
        assert dispatched_decision.source == "researcher_manual"  # type: ignore[attr-defined]

    async def test_manual_override_does_not_update_cooldown_map(self) -> None:
        # Manual decisions have no `triggering_rule`, so they don't
        # debit the auto-rule cooldown budget. The next auto tick
        # should still see the firing rule as fireable.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_redirect",
            payload={"topic": "anything"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, _actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))
        # Without a manual override on the next tick, the auto rule
        # should win — proving its cooldown wasn't bumped.
        # Drain second tick with no commands queued; the same bus
        # returns empty for the second drain call.
        next_actions: list[_RecordingAction] = []
        # Swap actions list to isolate second-tick observations.
        listener._publisher.actions = next_actions  # type: ignore[attr-defined]
        # Reuse fakes for the second tick by re-binding the listener's
        # session_factory to a fresh one.
        new_factory = _FakeSessionFactory(actions=next_actions)
        listener._session_factory = new_factory  # type: ignore[assignment]

        await listener(_state(tick_id=1, t=TICK_ZERO + timedelta(seconds=10)))

        second = next(a.payload for a in next_actions if a.kind == "decision_add")
        assert isinstance(second, Decision)
        assert second.source == "auto"
        assert second.triggering_rule == "firing_rule"


# ---------------------------------------------------------------------------
# Whisper override (P5 L4)
# ---------------------------------------------------------------------------


class TestWhisperOverride:
    """Whisper commands override the resolver with `source="researcher_whisper"`.

    Same FK-stamping + decision-id reuse semantics as the force_* path,
    but the persisted decision carries the verbatim text on
    `researcher_hint` and the dispatcher must hand off so the executor's
    whisper branch can skip the mouth and pipe the text straight to TTS.
    """

    async def test_whisper_overrides_resolver_with_verbatim_text(self) -> None:
        # Even with a firing auto-rule, the whisper wins this tick.
        firing = _StubRule(
            name="firing_rule",
            fires_after_tick=0,
            proposed_action="prompt_participant",
        )
        target = uuid.uuid4()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={
                "text": "Maria, your thoughts on the price?",
                "target_participant_id": str(target),
            },
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        # A whisper is a prompt with verbatim words; action stays the
        # standard prompt_participant so downstream consumers (audit
        # log, dashboard filters) treat it the same shape-wise.
        assert persisted.action == "prompt_participant"
        assert persisted.source == "researcher_whisper"
        assert persisted.triggering_rule is None
        assert persisted.researcher_id == uuid.UUID(cmd.researcher_id)
        # researcher_hint carries the literal text the executor will
        # send to TTS — this is the load-bearing invariant for L4.
        assert persisted.researcher_hint == "Maria, your thoughts on the price?"
        assert persisted.reason_codes == ["researcher_command:whisper"]
        assert persisted.confidence == pytest.approx(1.0)
        assert persisted.suppressed_by == []

    async def test_whisper_without_target_persists_null(self) -> None:
        # Group whisper — no target_participant_id in the payload.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "everyone hold on one moment"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.target_participant_id is None

    async def test_whisper_reuses_resolver_decision_id_for_eval_fk(self) -> None:
        # Same FK-consistency contract as force_*: the resolver's
        # rule_evaluations stay linked to the row researchers see.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "exactly these words"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        decision = next(a.payload for a in actions if a.kind == "decision_add")
        eval_rows = next(a.payload for a in actions if a.kind == "evals_add")
        assert isinstance(decision, Decision)
        assert isinstance(eval_rows, list)
        assert all(r.decision_id == decision.id for r in eval_rows)

    async def test_whisper_stamps_resulting_decision_id_on_audit_row(self) -> None:
        # Whisper audit row's FK back to the decision must land inside
        # the same transaction as the decision insert — same audit-first
        # contract the force_* path uses.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "go ahead Alice"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        fk_updates = [a for a in actions if a.kind == "researcher_action_fk_update"]
        assert len(fk_updates) == 1
        # Ordering: audit insert (T1) → decision insert (T2 start) →
        # FK stamp (still in T2). Without that order the FK can't resolve.
        kinds = [a.kind for a in actions]
        assert kinds.index("researcher_action_add") < kinds.index("decision_add")
        assert kinds.index("decision_add") < kinds.index("researcher_action_fk_update")

    async def test_whisper_decision_is_dispatched_to_executor(self) -> None:
        # The P5 L4 broadening: the dispatcher gate now also accepts
        # researcher_whisper so the executor can run its bypass-mouth
        # branch.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "alice please go first"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        dispatcher = _RecordingDispatcher()
        listener, _actions, _factory, _pub = _build(
            rules=_registry(quiet),
            command_bus=bus,
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0))

        assert len(dispatcher.dispatched) == 1
        dispatched_decision, _dispatched_state = dispatcher.dispatched[0]
        assert dispatched_decision.action == "prompt_participant"  # type: ignore[attr-defined]
        assert dispatched_decision.source == "researcher_whisper"  # type: ignore[attr-defined]
        assert dispatched_decision.researcher_hint == "alice please go first"  # type: ignore[attr-defined]

    async def test_whisper_does_not_update_cooldown_map(self) -> None:
        # Same rationale as force_*: whisper has no triggering_rule, so
        # it never debits any auto-rule cooldown budget.
        firing = _StubRule(name="firing_rule", fires_after_tick=0)
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "verbatim"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, _actions, _factory, _pub = _build(rules=_registry(firing), command_bus=bus)

        await listener(_state(tick_id=0))

        next_actions: list[_RecordingAction] = []
        listener._publisher.actions = next_actions  # type: ignore[attr-defined]
        new_factory = _FakeSessionFactory(actions=next_actions)
        listener._session_factory = new_factory  # type: ignore[assignment]

        await listener(_state(tick_id=1, t=TICK_ZERO + timedelta(seconds=10)))

        second = next(a.payload for a in next_actions if a.kind == "decision_add")
        assert isinstance(second, Decision)
        assert second.source == "auto"
        assert second.triggering_rule == "firing_rule"

    async def test_whisper_before_force_in_same_batch_wins(self) -> None:
        # FIFO is uniform across force_* and whisper. The earlier
        # command becomes the decision, the later one logs as an audit
        # row with no FK linkage.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        first = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "spoken word for word"},
        )
        second = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_redirect",
            payload={"topic": "loses-fifo"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[first, second]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.source == "researcher_whisper"
        assert persisted.researcher_hint == "spoken word for word"
        # Only the first override's audit row gets the FK stamp.
        fk_updates = [a for a in actions if a.kind == "researcher_action_fk_update"]
        assert len(fk_updates) == 1

    async def test_force_before_whisper_in_same_batch_wins(self) -> None:
        # Symmetric to the previous case — proves the FIFO is bidirectional.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        first = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={"prompt": "mouth-phrased intent"},
        )
        second = _researcher_command(
            session_id=SESSION_ID,
            command_type="whisper",
            payload={"text": "loses-fifo verbatim"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[first, second]]})
        listener, actions, _factory, _pub = _build(rules=_registry(quiet), command_bus=bus)

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.source == "researcher_manual"
        assert persisted.researcher_hint == "mouth-phrased intent"


@dataclass(slots=True)
class _FakeRuntimeControl:
    """RuntimeControl stand-in — records each call so tests can assert order."""

    calls: list[tuple[str, object]] = field(default_factory=list)

    def set_muted(self, *, muted: bool) -> None:
        self.calls.append(("set_muted", muted))

    def set_pause(self, *, paused: bool) -> None:
        self.calls.append(("set_pause", paused))

    async def request_end_session(self, *, reason: str | None) -> None:
        self.calls.append(("request_end_session", reason))


class TestControlCommands:
    """Non-spoken control commands (mute/pause/end_session) — P5 L5.

    Asserts the three places control commands matter in the listener:

      * The runtime control is called in batch order.
      * Non-silent decisions get `suppressed_by` extended with the
        matching reason so the audit row tells the truth.
      * The executor is not dispatched when the post-batch effective
        state has mute / pause / end_session in force.

    Mute / pause from the snapshot (set out-of-band on the runtime,
    not via the bus) is also covered — the gate must respect them
    regardless of how they got there.
    """

    async def test_mute_command_flips_state_and_suppresses_dispatch(self) -> None:
        # A firing rule wants the moderator to prompt, but a mute
        # command lands in the same batch. Expected: decision still
        # persists (audit intact), but the dispatcher is not invoked.
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="mute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        # Runtime received the mute toggle.
        assert ("set_muted", True) in control.calls

        # Decision was persisted with `muted` in suppressed_by.
        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "prompt_participant"
        assert "muted" in persisted.suppressed_by
        assert persisted.was_executed is False

        # Dispatcher was not invoked — the gate held.
        assert dispatcher.dispatched == []

    async def test_unmute_command_clears_state_allowing_dispatch(self) -> None:
        # Start the tick with the snapshot already muted; an unmute
        # in the batch should lift the gate for this tick.
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="unmute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0, moderator_muted=True))

        assert ("set_muted", False) in control.calls
        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        # No 'muted' in suppressed_by — the unmute lifted the gate.
        assert "muted" not in persisted.suppressed_by
        # Dispatcher invoked — utterance can flow.
        assert len(dispatcher.dispatched) == 1

    async def test_pause_command_suppresses_dispatch_with_paused_code(self) -> None:
        # Pause has the same dispatch-gating effect as mute but uses a
        # distinct suppressed_by code so researchers can tell which
        # action stopped the moderator from speaking.
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="pause_session",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        assert ("set_pause", True) in control.calls
        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "paused" in persisted.suppressed_by
        assert dispatcher.dispatched == []

    async def test_resume_command_lifts_pause_for_this_tick(self) -> None:
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="resume_session",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, _actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0, is_paused=True))

        assert ("set_pause", False) in control.calls
        assert len(dispatcher.dispatched) == 1

    async def test_end_session_marks_decision_and_blocks_dispatch(self) -> None:
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="end_session",
            payload={"reason": "wrapping up"},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        # Runtime was asked to end the session with the supplied reason.
        assert ("request_end_session", "wrapping up") in control.calls

        # The would-be utterance is recorded with session_ended in
        # suppressed_by so the audit shows the end aborted it.
        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "session_ended" in persisted.suppressed_by
        assert dispatcher.dispatched == []

    async def test_snapshot_muted_state_gates_dispatch_without_any_commands(
        self,
    ) -> None:
        # The runtime may have been muted out-of-band (e.g., via a
        # supervisor call before the bus came up). The snapshot is the
        # source of truth, and the gate must respect it even with an
        # empty command batch.
        firing = _StubRule(name="firing", fires_after_tick=0)
        dispatcher = _RecordingDispatcher()
        # No command bus, no runtime control — pure snapshot state.
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0, moderator_muted=True))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "muted" in persisted.suppressed_by
        assert dispatcher.dispatched == []

    async def test_snapshot_paused_state_gates_dispatch_without_any_commands(
        self,
    ) -> None:
        firing = _StubRule(name="firing", fires_after_tick=0)
        dispatcher = _RecordingDispatcher()
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            executor_dispatcher=dispatcher,
        )

        await listener(_state(tick_id=0, is_paused=True))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "paused" in persisted.suppressed_by
        assert dispatcher.dispatched == []

    async def test_stay_silent_decisions_not_marked_when_muted(self) -> None:
        # If the resolver chose stay_silent anyway, muted/paused
        # shouldn't be added to suppressed_by — it would be noise on
        # a row that wasn't going to speak in the first place.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        control = _FakeRuntimeControl()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="mute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(quiet),
            command_bus=bus,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.action == "stay_silent"
        assert "muted" not in persisted.suppressed_by

    async def test_force_prompt_with_mute_in_same_batch_is_suppressed(self) -> None:
        # The researcher issued a force_prompt AND a mute in the same
        # batch. The override fires (so the decision row carries
        # source=researcher_manual + the hint), but the dispatcher
        # gate honours the mute — no audio plays.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        force = _researcher_command(
            session_id=SESSION_ID,
            command_type="force_prompt",
            payload={"prompt": "ask the quiet one"},
        )
        mute = _researcher_command(
            session_id=SESSION_ID,
            command_type="mute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[force, mute]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(quiet),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert persisted.source == "researcher_manual"
        assert persisted.researcher_hint == "ask the quiet one"
        assert "muted" in persisted.suppressed_by
        assert dispatcher.dispatched == []

    async def test_mute_then_unmute_in_same_batch_allows_dispatch(self) -> None:
        # Researcher double-click: mute → unmute in the same drain.
        # Final state is unmuted; the firing rule's decision should
        # dispatch normally.
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        mute = _researcher_command(
            session_id=SESSION_ID,
            command_type="mute_moderator",
            payload={},
        )
        unmute = _researcher_command(
            session_id=SESSION_ID,
            command_type="unmute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[mute, unmute]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        # Both control calls fire (so the runtime sees each event,
        # not just the net change).
        assert control.calls == [("set_muted", True), ("set_muted", False)]

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "muted" not in persisted.suppressed_by
        assert len(dispatcher.dispatched) == 1

    async def test_audit_row_persisted_for_each_control_command(self) -> None:
        # The audit-first invariant — every command in the batch lands
        # in researcher_actions, even pure control ones with no
        # resulting decision. Verifies the listener still calls the
        # audit-write path before applying control effects.
        quiet = _StubRule(name="quiet", fires_after_tick=999)
        control = _FakeRuntimeControl()
        commands = [
            _researcher_command(
                session_id=SESSION_ID,
                command_type="mute_moderator",
                payload={},
            ),
            _researcher_command(
                session_id=SESSION_ID,
                command_type="pause_session",
                payload={},
            ),
        ]
        bus = _FakeCommandBus(queues={SESSION_ID: [commands]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(quiet),
            command_bus=bus,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        audit_rows = [a for a in actions if a.kind == "researcher_action_add"]
        assert len(audit_rows) == 2

    async def test_runtime_control_not_called_when_unwired(self) -> None:
        # If runtime_control is None, control commands are skipped at
        # the apply step — but they still land in the audit table.
        # The dispatch gate falls back to the snapshot's mute/pause
        # flags (False by default → dispatch fires).
        firing = _StubRule(name="firing", fires_after_tick=0)
        dispatcher = _RecordingDispatcher()
        cmd = _researcher_command(
            session_id=SESSION_ID,
            command_type="mute_moderator",
            payload={},
        )
        bus = _FakeCommandBus(queues={SESSION_ID: [[cmd]]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=None,
        )

        await listener(_state(tick_id=0))

        # Audit row is still there.
        audit_rows = [a for a in actions if a.kind == "researcher_action_add"]
        assert len(audit_rows) == 1

        # No mute in suppressed_by — the snapshot defaulted to False
        # and the command went un-applied.
        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "muted" not in persisted.suppressed_by
        # Dispatcher fired — the gate was open.
        assert len(dispatcher.dispatched) == 1

    async def test_mute_and_pause_both_recorded_when_in_same_batch(self) -> None:
        # Both reason codes land on the same row when both gates are
        # active. Order matches the apply order (mute then pause).
        firing = _StubRule(name="firing", fires_after_tick=0)
        control = _FakeRuntimeControl()
        dispatcher = _RecordingDispatcher()
        commands = [
            _researcher_command(
                session_id=SESSION_ID,
                command_type="mute_moderator",
                payload={},
            ),
            _researcher_command(
                session_id=SESSION_ID,
                command_type="pause_session",
                payload={},
            ),
        ]
        bus = _FakeCommandBus(queues={SESSION_ID: [commands]})
        listener, actions, _factory, _pub = _build(
            rules=_registry(firing),
            command_bus=bus,
            executor_dispatcher=dispatcher,
            runtime_control=control,
        )

        await listener(_state(tick_id=0))

        persisted = next(a.payload for a in actions if a.kind == "decision_add")
        assert isinstance(persisted, Decision)
        assert "muted" in persisted.suppressed_by
        assert "paused" in persisted.suppressed_by
        assert dispatcher.dispatched == []
