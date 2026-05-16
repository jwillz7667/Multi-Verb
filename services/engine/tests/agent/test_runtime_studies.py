"""Integration tests for SessionRuntime + Study wiring (Phase 3 L11).

Drives the three brief-mandated invariants of session-attached config:

  * `on_room_connected` freezes the study's config into
    `sessions.config_snapshot`. Replay relies on this snapshot being
    present and correct; an empty snapshot is a P0 audit bug.
  * The freeze is idempotent — a worker restart must not rewrite the
    original snapshot even if the study has been edited in the
    meantime (brief §7.5: rule versioning is sacred).
  * The auto-build path produces a registry whose per-rule configs
    reflect the study's overrides. We verify by reading the
    constructed `SilenceGapRule`'s config, since `silence_gap` is
    the cheapest rule to inspect end-to-end.
  * A session with no study attached still gets a snapshot — the v1
    defaults — so the audit trail is never empty in shadow mode.

All four tests are Docker-gated via `tests/agent/conftest.py`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.agent import SessionRuntime
from verbio_engine.domain.rules_config import RulesConfig
from verbio_engine.domain.study import SessionConfigSnapshot
from verbio_engine.persistence import (
    Session,
    StudyInsert,
    StudyRepo,
    create_session_factory,
)
from verbio_engine.rules import build_v1_registry
from verbio_engine.rules.silence_gap import SilenceGapRule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def _seed_study_and_session(
    factory: object,
    *,
    room_name: str,
    rules_config: dict[str, object] | None = None,
    moderator_persona: dict[str, object] | None = None,
    retention_policy: dict[str, object] | None = None,
) -> uuid.UUID:
    """Insert one study + one session pointing at it. Returns study_id."""
    async with factory() as db, db.begin():  # type: ignore[operator]
        study = await StudyRepo(db).insert(
            StudyInsert(
                org_id=uuid.uuid4(),
                name="Coffee Habits Pilot",
                prompt="Tell me about your morning coffee routine.",
                rules_version="v1.0",
                created_by=uuid.uuid4(),
                rules_config=rules_config or {},
                moderator_persona=moderator_persona or {},
                retention_policy=retention_policy or {},
            )
        )
        db.add(
            Session(
                livekit_room_name=room_name,
                status="scheduled",
                study_id=study.id,
            )
        )
        return study.id


@pytest.mark.integration
async def test_on_room_connected_freezes_config_snapshot_from_study(
    engine: AsyncEngine,
) -> None:
    """Study `rules_config` lands verbatim in `sessions.config_snapshot`."""
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    study_overrides = {"silence_gap": {"threshold_sec": 12.0}}
    persona = {"voice": "cartesia/sonic-en-female-1"}
    retention = {"recordings_ttl_days": 90}
    await _seed_study_and_session(
        factory,
        room_name=room_name,
        rules_config=study_overrides,
        moderator_persona=persona,
        retention_policy=retention,
    )

    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        rules_registry_factory=build_v1_registry,
    )
    await runtime.on_room_connected()

    async with factory() as db:
        row = (
            await db.execute(select(Session).where(Session.livekit_room_name == room_name))
        ).scalar_one()

    # Round-trip: the snapshot must parse as a `SessionConfigSnapshot`.
    snapshot = SessionConfigSnapshot.model_validate(row.config_snapshot)
    assert snapshot.rules_config.rules_version == "v1.0"
    assert snapshot.rules_config.rules == study_overrides
    assert snapshot.moderator_persona == persona
    assert snapshot.retention_policy == retention


@pytest.mark.integration
async def test_auto_built_registry_honors_study_overrides(
    engine: AsyncEngine,
) -> None:
    """The auto-build path threads `silence_gap.threshold_sec` into the rule.

    Reads back the constructed rule's config to prove the worker would
    see the study-tuned threshold and not the baked-in default.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    custom_threshold = 12.0
    await _seed_study_and_session(
        factory,
        room_name=room_name,
        rules_config={"silence_gap": {"threshold_sec": custom_threshold}},
    )

    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        rules_registry_factory=build_v1_registry,
    )
    await runtime.on_room_connected()

    registry = runtime._rules_registry
    assert registry is not None, "factory should have produced a registry"
    silence = registry.get("silence_gap")
    assert isinstance(silence, SilenceGapRule)
    assert silence._config.threshold_sec == custom_threshold


@pytest.mark.integration
async def test_config_snapshot_freeze_is_idempotent_on_restart(
    engine: AsyncEngine,
) -> None:
    """A second `on_room_connected` preserves the original snapshot.

    Simulates a worker restart by re-instantiating `SessionRuntime` and
    calling `on_room_connected` again. The first call's snapshot must
    survive even if the study row has been edited in the meantime
    (brief §7.5 — historical sessions stay frozen).
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    study_id = await _seed_study_and_session(
        factory,
        room_name=room_name,
        rules_config={"silence_gap": {"threshold_sec": 12.0}},
    )

    first = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        rules_registry_factory=build_v1_registry,
    )
    await first.on_room_connected()

    # Mutate the study between starts — this is the scenario the brief
    # warns against. The original session must not pick up the new value.
    async with factory() as db, db.begin():
        study = await StudyRepo(db).get_by_id(study_id)
        assert study is not None
        study.rules_config = {"silence_gap": {"threshold_sec": 99.0}}

    second = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        rules_registry_factory=build_v1_registry,
    )
    await second.on_room_connected()

    async with factory() as db:
        row = (
            await db.execute(select(Session).where(Session.livekit_room_name == room_name))
        ).scalar_one()

    snapshot = SessionConfigSnapshot.model_validate(row.config_snapshot)
    assert snapshot.rules_config.rules == {
        "silence_gap": {"threshold_sec": 12.0}
    }, "snapshot must reflect the ORIGINAL study config, not the post-edit value"

    # The rebuilt registry on the restarted runtime should also use the
    # original (snapshotted) threshold, not the post-edit one.
    registry = second._rules_registry
    assert registry is not None
    silence = registry.get("silence_gap")
    assert isinstance(silence, SilenceGapRule)
    assert silence._config.threshold_sec == 12.0


@pytest.mark.integration
async def test_session_without_study_still_gets_default_snapshot(
    engine: AsyncEngine,
) -> None:
    """A session with no `study_id` snapshots empty v1 defaults.

    The audit trail must never be empty in shadow mode — replay needs
    *some* RulesConfig to reconstruct the rule set, even if it's the
    bare defaults.
    """
    room_name = f"room-{uuid.uuid4()}"
    factory = create_session_factory(engine)
    async with factory() as db, db.begin():
        db.add(Session(livekit_room_name=room_name, status="scheduled"))

    runtime = SessionRuntime(
        session_factory=factory,
        room_name=room_name,
        rules_registry_factory=build_v1_registry,
    )
    await runtime.on_room_connected()

    async with factory() as db:
        row = (
            await db.execute(select(Session).where(Session.livekit_room_name == room_name))
        ).scalar_one()
    snapshot = SessionConfigSnapshot.model_validate(row.config_snapshot)
    assert snapshot.rules_config == RulesConfig(rules_version="v1.0")
    assert snapshot.moderator_persona == {}
    assert snapshot.retention_policy == {}

    # And the auto-build path still produces a usable registry.
    registry = runtime._rules_registry
    assert registry is not None
    assert len(registry) == 6
