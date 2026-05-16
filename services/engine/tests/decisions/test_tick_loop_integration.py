"""End-to-end decision tick-loop integration (Phase 3 L10).

Spins up a real Postgres via testcontainers and drives a `SessionRuntime`
with all v1 rules wired in. Validates the brief's §6 + §10.1 invariants
the unit suite can only mock around:

  * Every tick writes exactly ONE `decisions` row and N `rule_evaluations`
    rows (N = registry size). The cardinality invariant is what powers
    the "why didn't it speak at minute 14?" panel.
  * The persisted decision id matches the SSE envelope id (so the
    dashboard can follow the link from the decision log to the audit
    detail without an extra join).
  * Persist-before-publish holds across the runtime composition: the
    DB row exists before the publisher sees the envelope (we assert by
    verifying the row is queryable for every envelope ever published).
  * Identity translation: a decision targeting a known LiveKit identity
    persists the participants.id UUID, not the identity string.

We deliberately stay in the "all-quiet" regime (no real utterances,
small clock advance) so every tick produces stay_silent and we can
make exact cardinality assertions. Cooldown + firing semantics are
covered by the unit suite — exercising them here would just couple
the test to the rule defaults without adding integration coverage.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.agent import ParticipantSnapshot, SessionRuntime
from verbio_engine.domain.rules_config import RulesConfig
from verbio_engine.persistence import (
    Decision,
    RuleEvaluation,
    Session,
    create_session_factory,
)
from verbio_engine.realtime import (
    DecisionEventEnvelope,
    StateSnapshotEventEnvelope,
    TranscriptEvent,
)
from verbio_engine.rules import (
    CrossTalkPatternRule,
    RulesRegistry,
    SilenceGapRule,
    SpeakerImbalanceRule,
    TimeRemainingPressureRule,
    TopicDriftRule,
    UnheardParticipantRule,
)
from verbio_engine.tick_loop import FakeClock

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# Number of rules wired by `_v1_registry`. Asserted explicitly so a
# future rule addition surfaces here (the cardinality math is the
# whole point of these tests).
_V1_RULE_COUNT = 6


class _RecordingPublisher:
    """In-memory EventPublisher double; mirrors the runtime suite's helper."""

    def __init__(self) -> None:
        self.events: list[TranscriptEvent] = []

    async def publish(self, event: TranscriptEvent) -> int:
        self.events.append(event)
        return 1

    async def aclose(self) -> None:
        return None


def _v1_registry() -> RulesRegistry:
    """Build the v1.0 registry from defaults.

    `RulesConfig` carries empty per-rule overrides, so each rule reads
    its baked-in defaults. Phase 3 L11 (studies + session-attached
    config) will introduce the dynamic-config path; this helper stays
    minimal for now.
    """
    config = RulesConfig(rules_version="v1.0")
    return RulesRegistry(
        [
            SilenceGapRule.from_rules_config(config),
            SpeakerImbalanceRule.from_rules_config(config),
            CrossTalkPatternRule.from_rules_config(config),
            UnheardParticipantRule.from_rules_config(config),
            TopicDriftRule.from_rules_config(config),
            TimeRemainingPressureRule.from_rules_config(config),
        ],
        rules_version="v1.0",
    )


async def _seed_session(factory: object, room_name: str) -> None:
    """Insert the `sessions` row the runtime expects on `on_room_connected`."""
    async with factory() as db, db.begin():  # type: ignore[operator]
        db.add(Session(livekit_room_name=room_name, status="scheduled"))


@pytest.mark.integration
async def test_one_decision_and_n_evaluations_per_tick(engine: AsyncEngine) -> None:
    """The cardinality invariant: decisions == ticks, evals == ticks * registry size."""
    assert len(_v1_registry()) == _V1_RULE_COUNT, "registry helper drift"

    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    publisher = _RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=publisher,
        rules_registry=_v1_registry(),
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None

    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        # Advance the clock just enough to let the loop run a few ticks
        # without crossing the silence_gap threshold (8s default). A
        # 2.0s window admits ~4 ticks and keeps every decision silent,
        # so the cardinality math doesn't depend on which rule fired.
        clock.advance(2.0)
        await asyncio.sleep(0.1)
    finally:
        await runtime.stop_tick_loop()

    # Quiesce: the tick loop has stopped, so its `stats.ticks_completed`
    # is the canonical row-count expectation. We re-read via the persisted
    # rows + envelopes directly because the loop object is gone.
    assert runtime._tick_loop is None

    async with factory() as db:
        decision_rows = (
            (
                await db.execute(
                    select(Decision).where(Decision.session_id == runtime.session_id),
                )
            )
            .scalars()
            .all()
        )
        eval_rows = (
            (
                await db.execute(
                    select(RuleEvaluation).where(
                        RuleEvaluation.decision_id.in_([d.id for d in decision_rows]),
                    ),
                )
            )
            .scalars()
            .all()
        )

    assert len(decision_rows) > 0, "tick loop produced no decisions"
    assert len(eval_rows) == len(decision_rows) * _V1_RULE_COUNT, (
        f"expected {len(decision_rows)} * {_V1_RULE_COUNT} eval rows, " f"got {len(eval_rows)}"
    )

    # Cardinality holds per-decision too — no decision is missing any rule.
    by_decision: dict[uuid.UUID, list[RuleEvaluation]] = {}
    for row in eval_rows:
        by_decision.setdefault(row.decision_id, []).append(row)
    assert all(len(v) == _V1_RULE_COUNT for v in by_decision.values())

    # Every decision is stay_silent in the quiet regime — the test's
    # premise. If this flips a tester knows they crossed a rule threshold.
    assert all(d.action == "stay_silent" for d in decision_rows)
    assert all(d.was_executed is False for d in decision_rows)
    assert all(d.source == "auto" for d in decision_rows)

    # Each evaluation row carries the snapshotted version so replay
    # tooling reads the right rule set later.
    assert {e.rule_version for e in eval_rows} == {"v1.0"}


@pytest.mark.integration
async def test_decision_envelope_published_per_tick(engine: AsyncEngine) -> None:
    """Persist-before-publish: every published envelope has a matching DB row.

    The SSE envelope id is the decision UUID; a researcher clicking the
    dashboard's decision log must be able to fetch the row directly. We
    verify by asserting every envelope's id resolves to a persisted row.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    publisher = _RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=publisher,
        rules_registry=_v1_registry(),
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None

    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)

    try:
        clock.advance(2.0)
        await asyncio.sleep(0.1)
    finally:
        await runtime.stop_tick_loop()

    decision_envelopes = [e for e in publisher.events if isinstance(e, DecisionEventEnvelope)]
    snapshot_envelopes = [e for e in publisher.events if isinstance(e, StateSnapshotEventEnvelope)]
    assert decision_envelopes, "no decision envelopes were published"
    # Same tick count for both listeners — the snapshot listener runs
    # first then decisions, so the counts should match within one
    # (the loop could in theory exit mid-tick, but stop_tick_loop awaits
    # the current iteration to completion).
    assert abs(len(decision_envelopes) - len(snapshot_envelopes)) <= 1

    async with factory() as db:
        decision_rows = (
            (
                await db.execute(
                    select(Decision).where(Decision.session_id == runtime.session_id),
                )
            )
            .scalars()
            .all()
        )
    row_ids = {d.id for d in decision_rows}

    # Every published envelope corresponds to a persisted row (envelope
    # id is the str() form of the decision UUID).
    for env in decision_envelopes:
        assert uuid.UUID(env.id) in row_ids, f"envelope {env.id} has no matching decision row"
        assert env.session_id == runtime.session_id

    # Envelope payload carries the full ModeratorDecision — researchers
    # can read action/reason/confidence without an extra DB hit.
    sample = decision_envelopes[0]
    assert sample.payload.decision.action == "stay_silent"
    assert sample.payload.decision.source == "auto"


@pytest.mark.integration
async def test_decision_target_resolves_to_participant_uuid(engine: AsyncEngine) -> None:
    """LiveKit identity → DB UUID translation runs at the listener.

    Forces a rule fire by crossing `silence_gap`'s 8s threshold with a
    real participant in the room. The rule targets the least-recently
    active participant (the only one), and the listener should write
    the DB `participants.id` UUID into `target_participant_id`, not
    the identity string the predicate returned.

    Drives the tick loop via `tick_once()` rather than starting the
    async runner so the test is deterministic: we know exactly when
    the join event drains into the store and when silence crosses the
    threshold. The async `run()` version is exercised by the cardinality
    test above.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    await _seed_session(factory, room_name)

    publisher = _RecordingPublisher()
    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        publisher=publisher,
        rules_registry=_v1_registry(),
    )
    session_row = await runtime.on_room_connected()
    started = session_row.actual_start
    assert started is not None

    clock = FakeClock(start=started)
    runtime.start_tick_loop(started_at=started, clock=clock, interval_sec=0.5)
    # Cancel the background task — we'll drive `tick_once()` ourselves
    # for deterministic control over when ticks fire vs the clock.
    tick_loop = runtime._tick_loop
    assert tick_loop is not None
    bg_task = runtime._tick_task
    assert bg_task is not None
    bg_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await bg_task

    # Join AFTER start_tick_loop so the state store exists — `_record_
    # state_event` no-ops while the store is None. The runtime tests
    # follow the same ordering.
    participant_row = await runtime.on_participant_joined(
        ParticipantSnapshot(
            identity="id-quietone",
            display_name="Quiet One",
            role="participant",
        )
    )

    try:
        # Tick 0 at `started`: silence_run=0, join event hasn't drained
        # yet (its ts is wall-clock, > started). Persists stay_silent.
        await tick_loop.tick_once()
        # Advance past the silence_gap threshold (8s default). The next
        # tick sees silence_run >= 8 AND has the participant in the
        # projection (join event was queued at wall-clock now, which is
        # still well below started+10s).
        clock.advance(10.0)
        await tick_loop.tick_once()
    finally:
        # stop_tick_loop expects the loop bookkeeping it manages — wipe
        # the references the runtime is holding so its shutdown path
        # treats this as "loop never started".
        runtime._tick_loop = None
        runtime._tick_task = None
        await runtime.stop_tick_loop()

    async with factory() as db:
        firing_rows = (
            (
                await db.execute(
                    select(Decision).where(
                        Decision.session_id == runtime.session_id,
                        Decision.triggering_rule == "silence_gap",
                    ),
                )
            )
            .scalars()
            .all()
        )

    assert firing_rows, "silence_gap never fired despite 10s of silence with one participant"

    # Every silence_gap row must carry the translated UUID — never the
    # LiveKit identity string. (Cooldown caps this at one row per window,
    # but we assert on all that did land.)
    for row in firing_rows:
        assert (
            row.target_participant_id == participant_row.id
        ), "listener did not translate LiveKit identity → participants.id UUID"
        assert row.action == "prompt_participant"
        assert row.triggering_rule == "silence_gap"
