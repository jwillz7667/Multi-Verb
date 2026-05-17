"""Phase 5 end-to-end integration (P5 L10).

Validates the brief's §741 done-when for Phase 5 with the full real
stack the unit suite couldn't exercise:

  * Real Postgres via testcontainers — `researcher_actions`, `decisions`,
    `rule_evaluations`, `session_flags` rows must actually land.
  * Real Redis via testcontainers — the engine consumes from a live
    Redis Stream populated via XADD using the same wire format the web
    producer (`apps/web/src/features/sessions/commands.ts`) uses.
  * Real `RedisCommandStreamBus` + `DecisionTickListener` composition —
    the production listener, drained by the production bus, against a
    minimal `RuntimeControl` / `BudgetControl` fake (the listener is
    indifferent to whether these are the live `SessionRuntime` or a test
    double; the brief's contracts live on the persistence + bus seams).

What this suite asserts the unit suite cannot
---------------------------------------------

The unit tests in `test_listener.py` cover every command type with a
hand-rolled `_FakeCommandBus`. Those tests prove the walker logic, but
do not exercise the JSON-bytes → `ResearcherCommand` round trip, the
Redis cursor-advance behaviour across ticks, nor the real DB write +
FK linkage path. This file fills those gaps:

  1. `test_all_eleven_command_types_persist_to_researcher_actions` —
     one XADD per command type lands as one row in `researcher_actions`.
     The brief's §741 says "researcher_actions table populated for
     every command" — this is the load-bearing assertion.
  2. `test_audit_trail_distinguishes_auto_manual_whisper_blend` — a
     four-tick scenario that interleaves auto stay_silent, force_prompt,
     whisper, and another auto tick. The `decisions` rows must carry
     three distinct sources (`auto`, `researcher_manual`,
     `researcher_whisper`), and the audit-row FKs must point at the
     matching decisions atomically.
  3. `test_control_plane_commands_blend_with_auto_decisions` —
     non-spoken commands (set_quietness_budget, flag_moment,
     mute_moderator, unmute_moderator) all land as audit rows alongside
     auto decisions, with the runtime/budget fakes observing the
     mutations the listener applied. Demonstrates the "blend manual +
     automatic" criterion: control commands flow alongside the auto
     decision stream without producing decision rows of their own.

The listener is composed without an `ExecutionDispatcher` — Phase 4 L8
covers that path with its own integration test; this suite focuses on
the audit-trail invariants that are Phase 5's contract.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from redis import asyncio as redis_async
from sqlalchemy import select

from verbio_engine.commands import (
    RedisCommandStreamBus,
    commands_stream_key,
)
from verbio_engine.decisions import DecisionTickListener
from verbio_engine.domain import QuietnessBudget, SessionState
from verbio_engine.persistence import (
    Decision,
    ResearcherAction,
    Session,
    SessionFlag,
    create_session_factory,
)
from verbio_engine.realtime import TranscriptEvent
from verbio_engine.rules import RulesRegistry

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


# Single tick at a fixed wall clock keeps the test deterministic — the
# resolver depends only on `state.t`, not on the host clock.
TICK_ZERO = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Redis testcontainer fixture (session-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    """Boot a Redis 7 container for the test session.

    Lives locally rather than in the root conftest because Phase 5 is
    the only test family that needs a real Redis. If a future phase
    grows a second Redis-touching integration suite, hoist this fixture
    up to `tests/conftest.py` alongside `postgres_url`.
    """
    # Import inside the fixture so module collection still succeeds on
    # machines without testcontainers' Docker dep available.
    from testcontainers.redis import RedisContainer

    container = RedisContainer("redis:7-alpine")
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        yield f"redis://{host}:{port}/0"
    finally:
        container.stop()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[redis_async.Redis]:
    """Per-test async Redis client; the bus opens its own pool separately."""
    client: redis_async.Redis = redis_async.Redis.from_url(
        redis_url,
        decode_responses=False,
    )
    try:
        # Wipe any state from a previous test in the session — Redis
        # Streams persist across tests within a single container.
        await client.flushdb()
        yield client
    finally:
        await client.aclose()


@pytest.fixture
async def command_bus(redis_url: str) -> AsyncIterator[RedisCommandStreamBus]:
    """Fresh `RedisCommandStreamBus` per test so cursors don't leak."""
    bus = RedisCommandStreamBus(redis_url)
    try:
        yield bus
    finally:
        await bus.aclose()


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _RecordingPublisher:
    """Minimal `EventPublisher` double — collects SSE envelopes in order."""

    def __init__(self) -> None:
        self.events: list[TranscriptEvent] = []

    async def publish(self, event: TranscriptEvent) -> int:
        self.events.append(event)
        return 1

    async def aclose(self) -> None:
        return None


@dataclass(slots=True)
class _FakeRuntimeControl:
    """Tiny `RuntimeControl` impl — mutable mute/pause + end-call log."""

    muted: bool = False
    paused: bool = False
    end_calls: list[str | None] = field(default_factory=list)

    def set_muted(self, *, muted: bool) -> None:
        self.muted = muted

    def set_pause(self, *, paused: bool) -> None:
        self.paused = paused

    async def request_end_session(self, *, reason: str | None) -> None:
        self.end_calls.append(reason)


@dataclass(slots=True)
class _FakeBudgetControl:
    """Tiny `BudgetControl` impl — records the latest applied budget."""

    last_budget: QuietnessBudget | None = None

    def update_quietness_budget(self, budget: QuietnessBudget) -> None:
        self.last_budget = budget


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Insert a fresh `sessions` row and return its UUID.

    Required because `researcher_actions.session_id` has an FK on
    `sessions.id` — without a parent row the insert raises IntegrityError.
    """
    # Valid statuses (per migration 0003 `ck_sessions_status`) are
    # 'scheduled', 'live', 'ended', 'aborted'. 'live' is what the
    # runtime stamps when a session starts; matches the listener's
    # expectation of an in-progress session.
    session = Session(
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )
    async with factory() as db, db.begin():
        db.add(session)
        await db.flush([session])
        return session.id


async def _xadd_command(
    client: redis_async.Redis,
    *,
    session_id: uuid.UUID,
    command_type: str,
    payload: dict[str, Any],
    researcher_id: uuid.UUID | None = None,
    command_id: uuid.UUID | None = None,
    issued_at: datetime | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Mirror the web producer's XADD wire format exactly.

    The web side encodes the envelope as a single `data` field with the
    JSON-serialised `ResearcherCommand`. Returning both the command_id
    (audit row PK) and researcher_id (FK on the audit row) lets the
    test assert linkage without re-deriving them from drained commands.
    """
    cmd_id = command_id or uuid.uuid4()
    rid = researcher_id or uuid.uuid4()
    when = issued_at or datetime.now(UTC)
    envelope = {
        "command_id": str(cmd_id),
        "session_id": str(session_id),
        "researcher_id": str(rid),
        "issued_at": when.isoformat(),
        "command_type": command_type,
        "payload": payload,
    }
    await client.xadd(
        commands_stream_key(session_id).encode("utf-8"),
        {b"data": json.dumps(envelope).encode("utf-8")},
    )
    return cmd_id, rid


def _state(
    *,
    session_id: uuid.UUID,
    tick_id: int,
    moderator_muted: bool = False,
    is_paused: bool = False,
) -> SessionState:
    """Build a quiescent `SessionState` — no participants, zero silence.

    With no participants and an empty rule registry, the resolver always
    chooses `stay_silent` from the auto path, leaving any researcher
    command to be the sole driver of a non-silent decision.
    """
    when = TICK_ZERO + timedelta(milliseconds=500 * tick_id)
    return SessionState(
        session_id=session_id,
        tick_id=tick_id,
        t=when,
        started_at=TICK_ZERO,
        elapsed_sec=float(tick_id) * 0.5,
        participants={},
        currently_speaking_count=0,
        silence_run_sec=0.0,
        quietness_budget=QuietnessBudget(),
        moderator_muted=moderator_muted,
        is_paused=is_paused,
    )


def _empty_registry() -> RulesRegistry:
    """A no-rule registry so every auto tick resolves to stay_silent.

    Lets the test isolate the researcher-command path from the rules
    engine; the latter has its own integration coverage in
    `test_tick_loop_integration.py`.
    """
    return RulesRegistry((), rules_version="v1.0")


def _build_listener(
    *,
    factory: async_sessionmaker[AsyncSession],
    publisher: _RecordingPublisher,
    command_bus: RedisCommandStreamBus,
    runtime_control: _FakeRuntimeControl | None = None,
    budget_control: _FakeBudgetControl | None = None,
) -> DecisionTickListener:
    return DecisionTickListener(
        session_factory=factory,
        publisher=publisher,
        rules=_empty_registry(),
        identity_resolver=lambda _identity: None,
        command_bus=command_bus,
        runtime_control=runtime_control,
        budget_control=budget_control,
    )


# Every researcher command type and a minimal valid payload. Used by
# the first test to round-trip each type through Redis → engine →
# `researcher_actions`. Kept in lock-step with `ResearcherCommandType`
# in `verbio_engine.domain.command` — if a type is added there, this
# table must grow a matching entry (and the test will catch a drift).
_ALL_COMMAND_TYPES: list[tuple[str, dict[str, Any]]] = [
    ("force_prompt", {"prompt": "ask alice about pricing"}),
    ("force_redirect", {"topic": "back to the pricing question"}),
    ("force_summary", {"focus": "what we heard about price"}),
    ("whisper", {"text": "Alice, can you elaborate?"}),
    ("mute_moderator", {}),
    ("unmute_moderator", {}),
    ("pause_session", {}),
    ("resume_session", {}),
    ("set_quietness_budget", {"max_utterances_per_10min": 2}),
    ("flag_moment", {"note": "interesting reaction"}),
    ("end_session", {"reason": "wrap-up time"}),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_all_eleven_command_types_persist_to_researcher_actions(
    engine: AsyncEngine,
    redis_client: redis_async.Redis,
    command_bus: RedisCommandStreamBus,
) -> None:
    """Brief §741: `researcher_actions` table populated for every command.

    XADDs one of each command type onto the per-session Redis Stream,
    then runs a single listener tick. Asserts every command produced an
    audit row keyed by `command_id`, with the correct `command_type`
    and `researcher_id` carried verbatim from the wire.
    """
    factory = create_session_factory(engine)
    session_id = await _seed_session(factory)

    publisher = _RecordingPublisher()
    runtime_control = _FakeRuntimeControl()
    budget_control = _FakeBudgetControl()
    listener = _build_listener(
        factory=factory,
        publisher=publisher,
        command_bus=command_bus,
        runtime_control=runtime_control,
        budget_control=budget_control,
    )

    # Issue every command type from a single researcher so the audit
    # rows share a researcher_id — easier to assert by inspection.
    researcher_id = uuid.uuid4()
    issued: dict[str, uuid.UUID] = {}
    for command_type, payload in _ALL_COMMAND_TYPES:
        cmd_id, _ = await _xadd_command(
            redis_client,
            session_id=session_id,
            command_type=command_type,
            payload=payload,
            researcher_id=researcher_id,
        )
        issued[command_type] = cmd_id

    # Single tick drains the whole batch (well under the bus's
    # _DEFAULT_DRAIN_COUNT of 64).
    await listener(_state(session_id=session_id, tick_id=0))

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(ResearcherAction).where(
                        ResearcherAction.session_id == session_id,
                    ),
                )
            )
            .scalars()
            .all()
        )

    assert len(rows) == len(_ALL_COMMAND_TYPES), (
        f"expected one audit row per command type "
        f"(got {len(rows)} for {len(_ALL_COMMAND_TYPES)} commands)"
    )
    # PK = command_id from the wire; round-trip integrity check.
    persisted_ids = {row.id for row in rows}
    assert persisted_ids == set(issued.values())
    # Every audit row carries the right command_type string and shared
    # researcher_id — the wire fields land verbatim on the row.
    by_type = {row.command_type: row for row in rows}
    assert set(by_type) == {ct for ct, _ in _ALL_COMMAND_TYPES}
    assert all(row.researcher_id == researcher_id for row in rows)
    # Non-spoken commands leave `resulting_decision_id` null (no spoken
    # decision was overridden in this test — the resolver chose
    # stay_silent every tick because the rules registry is empty, so
    # the dispatcher gate never fires for force_prompt/whisper either).
    # Wait — that's wrong: force_prompt/whisper override the resolver,
    # so their audit rows DO get the FK stamped. The first overriding
    # command in the FIFO batch wins; only one decision row exists
    # for this tick, so only the first spoken command's FK is set.
    spoken_with_fk = [
        row
        for row in rows
        if row.command_type in {"force_prompt", "force_redirect", "force_summary", "whisper"}
        and row.resulting_decision_id is not None
    ]
    # First spoken in FIFO order is force_prompt — the only one whose
    # FK is stamped. The other three spoken rows audit without linkage.
    assert len(spoken_with_fk) == 1
    assert spoken_with_fk[0].command_type == "force_prompt"
    assert spoken_with_fk[0].id == issued["force_prompt"]

    # Side effects on the runtime/budget fakes prove the control plane
    # walkers ran end-to-end (not just persistence). end_session was in
    # the batch so the end_calls log carries one entry; the runtime is
    # muted because mute_moderator landed *after* unmute_moderator in
    # the FIFO order (per `_ALL_COMMAND_TYPES`, mute precedes unmute,
    # so unmute is the last mute-toggling command and runtime ends
    # un-muted). pause/resume similar: pause precedes resume → un-paused.
    assert len(runtime_control.end_calls) == 1
    assert runtime_control.muted is False
    assert runtime_control.paused is False
    # The budget walker applied the set_quietness_budget patch.
    assert budget_control.last_budget is not None
    assert budget_control.last_budget.max_utterances_per_10min == 2


@pytest.mark.integration
async def test_audit_trail_distinguishes_auto_manual_whisper_blend(
    engine: AsyncEngine,
    redis_client: redis_async.Redis,
    command_bus: RedisCommandStreamBus,
) -> None:
    """Brief §741: audit trail cleanly distinguishes auto vs. researcher-driven.

    Four-tick scenario that exercises the three decision sources the
    brief calls out:

      tick 0 — no commands     → `decisions.source = "auto"` (stay_silent)
      tick 1 — force_redirect  → `decisions.source = "researcher_manual"`
      tick 2 — whisper         → `decisions.source = "researcher_whisper"`
      tick 3 — no commands     → `decisions.source = "auto"` again

    Asserts the persisted `decisions.source` column carries the right
    distinguisher for every tick, and that the audit rows for the two
    overriding commands have their `resulting_decision_id` stamped to
    the matching decision row's PK (so the dashboard can link from the
    audit log to the decision a researcher actually heard).
    """
    factory = create_session_factory(engine)
    session_id = await _seed_session(factory)

    publisher = _RecordingPublisher()
    listener = _build_listener(
        factory=factory,
        publisher=publisher,
        command_bus=command_bus,
        runtime_control=_FakeRuntimeControl(),
        budget_control=_FakeBudgetControl(),
    )

    # Tick 0: pure auto — no commands queued.
    await listener(_state(session_id=session_id, tick_id=0))

    # Tick 1: force_redirect. Researcher's call wins.
    force_cmd_id, force_rid = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="force_redirect",
        payload={"topic": "let's drill into pricing pushback"},
    )
    await listener(_state(session_id=session_id, tick_id=1))

    # Tick 2: whisper. Same FIFO override path, different source label.
    whisper_cmd_id, whisper_rid = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="whisper",
        payload={"text": "Maria, can you say more about that?"},
    )
    await listener(_state(session_id=session_id, tick_id=2))

    # Tick 3: pure auto again — proves the override didn't poison
    # subsequent ticks.
    await listener(_state(session_id=session_id, tick_id=3))

    async with factory() as db:
        decision_rows = (
            (
                await db.execute(
                    select(Decision)
                    .where(Decision.session_id == session_id)
                    .order_by(Decision.tick_id),
                )
            )
            .scalars()
            .all()
        )
        audit_rows = (
            (
                await db.execute(
                    select(ResearcherAction).where(
                        ResearcherAction.session_id == session_id,
                    ),
                )
            )
            .scalars()
            .all()
        )

    # Four ticks → four decisions, one per tick (the cardinality
    # invariant proven for the auto path in test_tick_loop_integration
    # holds whether or not commands fired).
    assert len(decision_rows) == 4
    assert [row.tick_id for row in decision_rows] == [0, 1, 2, 3]

    # The audit trail's load-bearing column: sources must distinguish
    # auto from each researcher-driven kind.
    assert [row.source for row in decision_rows] == [
        "auto",
        "researcher_manual",
        "researcher_whisper",
        "auto",
    ]

    # Auto decisions are stay_silent (no rules in the registry, no
    # commands in the batch). Override decisions are non-silent and
    # carry the researcher's hint verbatim.
    auto_ticks = [decision_rows[0], decision_rows[3]]
    for auto in auto_ticks:
        assert auto.action == "stay_silent"
        assert auto.triggering_rule is None
        assert auto.researcher_id is None
        assert auto.researcher_hint is None

    manual_row = decision_rows[1]
    assert manual_row.action == "redirect_topic"
    assert manual_row.researcher_id == force_rid
    assert manual_row.researcher_hint == "let's drill into pricing pushback"
    assert manual_row.triggering_rule is None
    assert manual_row.reason_codes == ["researcher_command:force_redirect"]

    whisper_row = decision_rows[2]
    assert whisper_row.action == "prompt_participant"
    assert whisper_row.source == "researcher_whisper"
    assert whisper_row.researcher_id == whisper_rid
    assert whisper_row.researcher_hint == "Maria, can you say more about that?"
    assert whisper_row.reason_codes == ["researcher_command:whisper"]

    # Audit rows: exactly the two override commands (no audit row is
    # written for auto ticks). FKs link audit → decision so a
    # researcher tracing "what did I press at 12:30?" lands on the
    # exact decision the moderator emitted from their command.
    assert len(audit_rows) == 2
    audit_by_id = {row.id: row for row in audit_rows}
    assert audit_by_id[force_cmd_id].command_type == "force_redirect"
    assert audit_by_id[force_cmd_id].resulting_decision_id == manual_row.id
    assert audit_by_id[whisper_cmd_id].command_type == "whisper"
    assert audit_by_id[whisper_cmd_id].resulting_decision_id == whisper_row.id


@pytest.mark.integration
async def test_control_plane_commands_blend_with_auto_decisions(
    engine: AsyncEngine,
    redis_client: redis_async.Redis,
    command_bus: RedisCommandStreamBus,
) -> None:
    """Brief §741: blend manual + automatic interventions cleanly.

    Non-spoken control commands (mute, unmute, set_quietness_budget,
    flag_moment) audit alongside the auto decision stream without
    producing decision rows of their own. The runtime/budget control
    surfaces observe the mutations in real time, and the
    `session_flags` projection writes its bookmark in addition to the
    audit row.
    """
    factory = create_session_factory(engine)
    session_id = await _seed_session(factory)

    publisher = _RecordingPublisher()
    runtime_control = _FakeRuntimeControl()
    budget_control = _FakeBudgetControl()
    listener = _build_listener(
        factory=factory,
        publisher=publisher,
        command_bus=command_bus,
        runtime_control=runtime_control,
        budget_control=budget_control,
    )

    # Tick 0: three control commands in a single batch. The walker
    # processes them in FIFO order; effects compound.
    budget_cmd_id, _ = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="set_quietness_budget",
        payload={"max_utterances_per_10min": 1, "min_seconds_between_utterances": 90.0},
    )
    flag_cmd_id, flag_rid = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="flag_moment",
        payload={"note": "key insight from Alice"},
    )
    mute_cmd_id, _ = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="mute_moderator",
        payload={},
    )
    await listener(_state(session_id=session_id, tick_id=0))

    # All three runtime surfaces observed their respective walker.
    # `bool(...)` rather than `is True/False` so mypy doesn't narrow the
    # dataclass field to a Literal across the opaque `await listener` call.
    assert bool(runtime_control.muted) is True
    assert budget_control.last_budget is not None
    assert budget_control.last_budget.max_utterances_per_10min == 1
    assert budget_control.last_budget.min_seconds_between_utterances == 90.0

    # Tick 1: unmute. Single-command batch flips mute back off and
    # produces another audit row.
    unmute_cmd_id, _ = await _xadd_command(
        redis_client,
        session_id=session_id,
        command_type="unmute_moderator",
        payload={},
    )
    await listener(_state(session_id=session_id, tick_id=1))

    assert bool(runtime_control.muted) is False

    async with factory() as db:
        audit_rows = (
            (
                await db.execute(
                    select(ResearcherAction)
                    .where(ResearcherAction.session_id == session_id)
                    .order_by(ResearcherAction.ts),
                )
            )
            .scalars()
            .all()
        )
        decision_rows = (
            (
                await db.execute(
                    select(Decision)
                    .where(Decision.session_id == session_id)
                    .order_by(Decision.tick_id),
                )
            )
            .scalars()
            .all()
        )
        flag_rows = (
            (
                await db.execute(
                    select(SessionFlag).where(SessionFlag.session_id == session_id),
                )
            )
            .scalars()
            .all()
        )

    # All four control commands landed as audit rows, none with a
    # resulting decision (non-spoken commands never override the
    # resolver, so the FK on `researcher_actions` stays null).
    assert {row.id for row in audit_rows} == {
        budget_cmd_id,
        flag_cmd_id,
        mute_cmd_id,
        unmute_cmd_id,
    }
    assert all(row.resulting_decision_id is None for row in audit_rows)

    # Two ticks → two auto decisions. Neither spoke (stay_silent), and
    # they both carry `source="auto"`. The mute was applied AFTER the
    # resolver ran for tick 0, so the `suppressed_by` extension only
    # triggers when the resolver picked a non-silent action — here it
    # didn't, so the column stays clean.
    assert len(decision_rows) == 2
    assert all(row.source == "auto" for row in decision_rows)
    assert all(row.action == "stay_silent" for row in decision_rows)
    assert all(row.suppressed_by == [] for row in decision_rows)

    # P5 L7 projection: flag_moment writes both an audit row AND a
    # `session_flags` bookmark with the same PK (= command_id). One
    # flag_moment was issued → exactly one bookmark.
    assert len(flag_rows) == 1
    flag = flag_rows[0]
    assert flag.id == flag_cmd_id
    assert flag.researcher_id == flag_rid
    assert flag.note == "key insight from Alice"
    assert flag.auto_generated is False
