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
from verbio_engine.persistence import Decision, RuleEvaluation
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
    """One observable event during a listener invocation."""

    kind: str  # "decision_add" | "evals_add" | "flush" | "publish"
    payload: object


@dataclass(slots=True)
class _FakeAsyncSession:
    """Stand-in for SQLAlchemy AsyncSession that records add / add_all / flush."""

    actions: list[_RecordingAction]
    fail_on_decision_flush: bool = False
    _exited: bool = False

    def add(self, instance: Decision) -> None:
        self.actions.append(_RecordingAction("decision_add", instance))

    def add_all(self, instances: list[RuleEvaluation]) -> None:
        # Record a *copy* of the input list so subsequent mutations by
        # the SUT don't retroactively edit the assertion view.
        self.actions.append(_RecordingAction("evals_add", list(instances)))

    async def flush(self, instances: list[Decision | RuleEvaluation] | None = None) -> None:
        if (
            self.fail_on_decision_flush
            and instances is not None
            and any(isinstance(i, Decision) for i in instances)
        ):
            msg = "simulated DB outage"
            raise RuntimeError(msg)
        self.actions.append(_RecordingAction("flush", instances or []))

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
    )


def _registry(*rules: Rule) -> RulesRegistry:
    return RulesRegistry(rules, rules_version="v1.0")


def _build(
    *,
    rules: RulesRegistry,
    identity_resolver: object = None,
    fail_on_decision_flush: bool = False,
) -> tuple[DecisionTickListener, list[_RecordingAction], _FakeSessionFactory, _FakePublisher]:
    actions: list[_RecordingAction] = []
    factory = _FakeSessionFactory(actions=actions, fail_on_decision_flush=fail_on_decision_flush)
    publisher = _FakePublisher(actions=actions)
    resolver = identity_resolver or (lambda _identity: None)
    listener = DecisionTickListener(
        session_factory=factory,  # type: ignore[arg-type]
        publisher=publisher,  # type: ignore[arg-type]
        rules=rules,
        identity_resolver=resolver,  # type: ignore[arg-type]
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
    listener._session_factory = new_factory  # type: ignore[attr-defined,assignment]
    listener._publisher = new_publisher  # type: ignore[attr-defined,assignment]

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
