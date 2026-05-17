"""Integration tests for the researcher_actions repository (P5 L2).

Covers the brief §10.1 audit-row guarantees: idempotency on
`command_id`, JSONB payload round-trip, FK cascade from sessions
(`ON DELETE CASCADE`), FK SET NULL from decisions (so purging an
auto-generated decision doesn't erase the researcher audit row).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from verbio_engine.persistence import (
    DecisionInsert,
    DecisionRepo,
    ResearcherAction,
    ResearcherActionInsert,
    ResearcherActionRepo,
    Session,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _make_session(session_id: uuid.UUID) -> Session:
    return Session(
        id=session_id,
        livekit_room_name=f"room-{uuid.uuid4()}",
        status="live",
    )


def _make_action_insert(
    *,
    session_id: uuid.UUID,
    command_id: uuid.UUID | None = None,
    researcher_id: uuid.UUID | None = None,
    command_type: str = "set_quietness_budget",
    payload: dict[str, object] | None = None,
    resulting_decision_id: uuid.UUID | None = None,
    ts: datetime | None = None,
) -> ResearcherActionInsert:
    return ResearcherActionInsert(
        command_id=command_id or uuid.uuid4(),
        session_id=session_id,
        researcher_id=researcher_id or uuid.uuid4(),
        ts=ts or datetime.now(UTC),
        command_type=command_type,
        payload=payload if payload is not None else {"budget": 2},
        resulting_decision_id=resulting_decision_id,
    )


class TestResearcherActionRepoInsert:
    @pytest.mark.integration
    async def test_insert_persists_row_with_payload(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = ResearcherActionRepo(session)
        record = _make_action_insert(
            session_id=session_id,
            payload={"budget": 3, "reason": "feels chatty"},
        )
        row = await repo.insert(record)

        assert row.id == record.command_id
        assert row.session_id == session_id
        assert row.researcher_id == record.researcher_id
        assert row.command_type == "set_quietness_budget"
        assert row.payload == {"budget": 3, "reason": "feels chatty"}
        assert row.resulting_decision_id is None

    @pytest.mark.integration
    async def test_insert_with_null_payload(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        """`mute` / `pause_session` carry no payload — None must persist as NULL."""
        session.add(_make_session(session_id))
        await session.flush()

        repo = ResearcherActionRepo(session)
        record = _make_action_insert(
            session_id=session_id,
            command_type="mute",
            payload=None,
        )
        row = await repo.insert(record)

        assert row.payload is None
        assert row.command_type == "mute"

    @pytest.mark.integration
    async def test_insert_idempotent_on_duplicate_command_id(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        """Re-draining the same command after a restart must not produce a duplicate row."""
        session.add(_make_session(session_id))
        await session.flush()

        repo = ResearcherActionRepo(session)
        command_id = uuid.uuid4()
        first = await repo.insert(
            _make_action_insert(
                session_id=session_id,
                command_id=command_id,
                command_type="flag_moment",
                payload={"label": "key insight"},
            ),
        )
        await session.flush()

        # Second insert with same command_id but different payload — the
        # original row wins; the second attempt is a no-op.
        second = await repo.insert(
            _make_action_insert(
                session_id=session_id,
                command_id=command_id,
                command_type="flag_moment",
                payload={"label": "second attempt"},
            ),
        )
        assert second.id == first.id
        assert second.payload == {"label": "key insight"}

        all_rows = (
            (
                await session.execute(
                    select(ResearcherAction).where(ResearcherAction.id == command_id),
                )
            )
            .scalars()
            .all()
        )
        assert len(all_rows) == 1


class TestResearcherActionCascadeBehavior:
    @pytest.mark.integration
    async def test_session_cascade_deletes_actions(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        sess_row = _make_session(session_id)
        session.add(sess_row)
        await session.flush()

        repo = ResearcherActionRepo(session)
        await repo.insert(_make_action_insert(session_id=session_id))
        await session.flush()

        await session.delete(sess_row)
        await session.flush()

        remaining = (
            (
                await session.execute(
                    select(ResearcherAction).where(
                        ResearcherAction.session_id == session_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    @pytest.mark.integration
    async def test_decision_delete_nulls_link_but_keeps_action(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        """Purging the produced decision must not erase the researcher audit row."""
        session.add(_make_session(session_id))
        await session.flush()

        decision_repo = DecisionRepo(session)
        when = datetime.now(UTC)
        decision_id = uuid.uuid4()
        await decision_repo.insert(
            DecisionInsert(
                decision_id=decision_id,
                session_id=session_id,
                tick_id=12,
                ts=when,
                action="prompt_participant",
                source="researcher_manual",
                was_executed=True,
                cooldown_until=when + timedelta(seconds=30),
                researcher_id=uuid.uuid4(),
                researcher_hint="ask Alice about onboarding",
            ),
        )
        await session.flush()

        repo = ResearcherActionRepo(session)
        record = _make_action_insert(
            session_id=session_id,
            command_type="force_prompt",
            payload={"hint": "ask Alice about onboarding"},
            resulting_decision_id=decision_id,
        )
        row = await repo.insert(record)
        assert row.resulting_decision_id == decision_id
        await session.flush()

        # Delete the decision row directly via SQL so cascade fires.
        from verbio_engine.persistence.models import Decision

        decision_row = (
            await session.execute(select(Decision).where(Decision.id == decision_id))
        ).scalar_one()
        await session.delete(decision_row)
        await session.flush()
        session.expire_all()

        reread = (
            await session.execute(
                select(ResearcherAction).where(ResearcherAction.id == record.command_id),
            )
        ).scalar_one()
        assert reread.resulting_decision_id is None
        # The audit row itself survives intact.
        assert reread.command_type == "force_prompt"
        assert reread.payload == {"hint": "ask Alice about onboarding"}
