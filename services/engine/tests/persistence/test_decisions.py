"""Integration tests for the decision + rule_evaluations repositories.

Boots a real Postgres container (testcontainers) and exercises the
full path: Alembic migrations → ORM model → repository insert →
read-back, including the brief's CHECK constraints (action enum,
source enum, confidence range) and the FK cascade behavior.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from verbio_engine.persistence import (
    Decision,
    DecisionInsert,
    DecisionRepo,
    Participant,
    RuleEvaluation,
    RuleEvaluationInsert,
    RuleEvaluationRepo,
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


def _make_participant(session_id: uuid.UUID) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        session_id=session_id,
        display_name="Alice",
        role="participant",
        livekit_identity=f"p-{uuid.uuid4()}",
    )


def _stay_silent_insert(
    *,
    session_id: uuid.UUID,
    tick_id: int = 0,
    ts: datetime | None = None,
) -> DecisionInsert:
    """A bare `stay_silent` decision — matches the resolver's default output."""
    when = ts or datetime.now(UTC)
    return DecisionInsert(
        decision_id=uuid.uuid4(),
        session_id=session_id,
        tick_id=tick_id,
        ts=when,
        action="stay_silent",
        source="auto",
        was_executed=False,
        cooldown_until=when,
    )


def _spoken_decision_insert(
    *,
    session_id: uuid.UUID,
    target_participant_id: uuid.UUID | None = None,
    triggering_rule: str = "silence_gap",
    action: str = "prompt_participant",
    ts: datetime | None = None,
) -> DecisionInsert:
    when = ts or datetime.now(UTC)
    return DecisionInsert(
        decision_id=uuid.uuid4(),
        session_id=session_id,
        tick_id=7,
        ts=when,
        action=action,
        target_participant_id=target_participant_id,
        source="auto",
        triggering_rule=triggering_rule,
        reason_codes=[f"{triggering_rule}_8s"],
        reason_human="The room has been quiet for a while.",
        confidence=0.75,
        suppressed_by=[],
        was_executed=True,
        cooldown_until=when + timedelta(seconds=45),
        spoken_at=when + timedelta(milliseconds=120),
    )


class TestDecisionRepoInsert:
    @pytest.mark.integration
    async def test_insert_stay_silent_persists_row(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        record = _stay_silent_insert(session_id=session_id)
        row = await repo.insert(record)

        assert row.id == record.decision_id
        assert row.action == "stay_silent"
        assert row.was_executed is False
        # Empty defaults for array fields come back as Python lists, not None.
        assert row.reason_codes == []
        assert row.suppressed_by == []

    @pytest.mark.integration
    async def test_insert_spoken_decision_round_trip(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        participant = _make_participant(session_id)
        session.add(participant)
        await session.flush()

        repo = DecisionRepo(session)
        record = _spoken_decision_insert(
            session_id=session_id,
            target_participant_id=participant.id,
        )
        await repo.insert(record)
        await session.flush()

        reread = (
            await session.execute(select(Decision).where(Decision.id == record.decision_id))
        ).scalar_one()

        assert reread.action == "prompt_participant"
        assert reread.triggering_rule == "silence_gap"
        assert reread.reason_codes == ["silence_gap_8s"]
        assert reread.reason_human.startswith("The room has been quiet")
        assert reread.confidence == pytest.approx(0.75)
        assert reread.was_executed is True
        assert reread.target_participant_id == participant.id

    @pytest.mark.integration
    async def test_jsonb_llm_prompt_round_trips(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        when = datetime.now(UTC)
        payload: dict[str, object] = {
            "system": "moderator",
            "decision": {
                "action": "redirect_topic",
                "reason_codes": ["topic_drift_sim_18pct"],
            },
            "context": {"recent_text": "lake hike"},
        }
        record = DecisionInsert(
            decision_id=uuid.uuid4(),
            session_id=session_id,
            tick_id=10,
            ts=when,
            action="redirect_topic",
            source="auto",
            triggering_rule="topic_drift",
            was_executed=True,
            cooldown_until=when + timedelta(seconds=180),
            llm_prompt=payload,
            llm_output="Let's swing back to the music app conversation.",
        )
        await repo.insert(record)
        await session.flush()

        reread = (
            await session.execute(select(Decision).where(Decision.id == record.decision_id))
        ).scalar_one()
        assert reread.llm_prompt == payload
        assert reread.llm_output is not None
        assert reread.llm_output.startswith("Let's swing back")


class TestDecisionConstraints:
    @pytest.mark.integration
    async def test_unknown_action_rejected_by_check_constraint(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        bad = _stay_silent_insert(session_id=session_id)
        bad_action = dataclasses.replace(bad, action="yodel_at_them")
        with pytest.raises(IntegrityError, match="ck_decisions_action"):
            await repo.insert(bad_action)

    @pytest.mark.integration
    async def test_unknown_source_rejected_by_check_constraint(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        bad = _stay_silent_insert(session_id=session_id)
        bad_source = dataclasses.replace(bad, source="anonymous_tip")
        with pytest.raises(IntegrityError, match="ck_decisions_source"):
            await repo.insert(bad_source)

    @pytest.mark.integration
    async def test_confidence_out_of_range_rejected(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        bad = _stay_silent_insert(session_id=session_id)
        bad_conf = dataclasses.replace(bad, confidence=1.5)
        with pytest.raises(IntegrityError, match="ck_decisions_confidence_unit"):
            await repo.insert(bad_conf)

    @pytest.mark.integration
    async def test_auto_non_silent_must_name_rule(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        # A spoken auto decision with no triggering_rule is a bug — the
        # CHECK is the safety net.
        when = datetime.now(UTC)
        record = DecisionInsert(
            decision_id=uuid.uuid4(),
            session_id=session_id,
            tick_id=1,
            ts=when,
            action="prompt_participant",
            source="auto",
            was_executed=True,
            cooldown_until=when + timedelta(seconds=45),
        )
        with pytest.raises(IntegrityError, match="ck_decisions_auto_has_rule_or_silent"):
            await repo.insert(record)

    @pytest.mark.integration
    async def test_negative_tick_id_rejected(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        repo = DecisionRepo(session)
        bad = _stay_silent_insert(session_id=session_id)
        bad_tick = dataclasses.replace(bad, tick_id=-1)
        with pytest.raises(IntegrityError, match="ck_decisions_tick_id_non_negative"):
            await repo.insert(bad_tick)


class TestDecisionCascadeBehavior:
    @pytest.mark.integration
    async def test_session_cascade_deletes_decisions(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        sess_row = _make_session(session_id)
        session.add(sess_row)
        await session.flush()

        repo = DecisionRepo(session)
        await repo.insert(_stay_silent_insert(session_id=session_id))

        await session.delete(sess_row)
        await session.flush()

        remaining = (
            (
                await session.execute(
                    select(Decision).where(Decision.session_id == session_id),
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []

    @pytest.mark.integration
    async def test_participant_delete_nulls_target_keeps_decision(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        """A purged participant must not erase the moderator's audit trail."""
        session.add(_make_session(session_id))
        participant = _make_participant(session_id)
        session.add(participant)
        await session.flush()

        repo = DecisionRepo(session)
        record = _spoken_decision_insert(
            session_id=session_id,
            target_participant_id=participant.id,
        )
        await repo.insert(record)
        await session.flush()

        await session.delete(participant)
        await session.flush()
        # Force a re-read so the in-session cache reflects the SET NULL.
        session.expire_all()

        reread = (
            await session.execute(select(Decision).where(Decision.id == record.decision_id))
        ).scalar_one()
        assert reread.target_participant_id is None
        # The rest of the row survives intact.
        assert reread.triggering_rule == "silence_gap"
        assert reread.action == "prompt_participant"


class TestRuleEvaluationRepoInsertMany:
    @pytest.mark.integration
    async def test_insert_many_persists_full_batch(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        decision_repo = DecisionRepo(session)
        decision_record = _stay_silent_insert(session_id=session_id)
        await decision_repo.insert(decision_record)

        eval_repo = RuleEvaluationRepo(session)
        records = [
            RuleEvaluationInsert(
                evaluation_id=uuid.uuid4(),
                decision_id=decision_record.decision_id,
                rule_name=name,
                rule_version="v1.0",
                fired=False,
                confidence=0.0,
                predicate_inputs={"checked": True, "name": name},
            )
            for name in (
                "silence_gap",
                "speaker_imbalance",
                "topic_drift",
                "cross_talk_pattern",
                "unheard_participant",
                "time_remaining_pressure",
            )
        ]
        rows = await eval_repo.insert_many(records)
        assert len(rows) == len(records)

        rereads = (
            (
                await session.execute(
                    select(RuleEvaluation).where(
                        RuleEvaluation.decision_id == decision_record.decision_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert {r.rule_name for r in rereads} == {r.rule_name for r in records}
        assert all(r.predicate_inputs.get("checked") is True for r in rereads)

    @pytest.mark.integration
    async def test_insert_many_empty_is_noop(
        self,
        session: AsyncSession,
    ) -> None:
        eval_repo = RuleEvaluationRepo(session)
        rows = await eval_repo.insert_many([])
        assert list(rows) == []

    @pytest.mark.integration
    async def test_suppressed_reason_check_rejects_unknown(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        decision_repo = DecisionRepo(session)
        decision_record = _stay_silent_insert(session_id=session_id)
        await decision_repo.insert(decision_record)

        eval_repo = RuleEvaluationRepo(session)
        bad = RuleEvaluationInsert(
            evaluation_id=uuid.uuid4(),
            decision_id=decision_record.decision_id,
            rule_name="silence_gap",
            rule_version="v1.0",
            fired=True,
            suppressed_reason="moon_phase",
        )
        with pytest.raises(IntegrityError, match="ck_rule_evaluations_suppressed_reason"):
            await eval_repo.insert_many([bad])

    @pytest.mark.integration
    async def test_confidence_out_of_range_rejected(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        decision_repo = DecisionRepo(session)
        decision_record = _stay_silent_insert(session_id=session_id)
        await decision_repo.insert(decision_record)

        eval_repo = RuleEvaluationRepo(session)
        bad = RuleEvaluationInsert(
            evaluation_id=uuid.uuid4(),
            decision_id=decision_record.decision_id,
            rule_name="silence_gap",
            rule_version="v1.0",
            fired=True,
            confidence=2.5,
        )
        with pytest.raises(IntegrityError, match="ck_rule_evaluations_confidence_unit"):
            await eval_repo.insert_many([bad])

    @pytest.mark.integration
    async def test_decision_cascade_deletes_evaluations(
        self,
        session: AsyncSession,
        session_id: uuid.UUID,
    ) -> None:
        session.add(_make_session(session_id))
        await session.flush()

        decision_repo = DecisionRepo(session)
        decision_record = _stay_silent_insert(session_id=session_id)
        await decision_repo.insert(decision_record)

        eval_repo = RuleEvaluationRepo(session)
        await eval_repo.insert_many(
            [
                RuleEvaluationInsert(
                    evaluation_id=uuid.uuid4(),
                    decision_id=decision_record.decision_id,
                    rule_name="silence_gap",
                    rule_version="v1.0",
                    fired=False,
                ),
            ],
        )

        # Delete the parent decision; FK cascade removes the evaluations.
        decision_row = (
            await session.execute(
                select(Decision).where(Decision.id == decision_record.decision_id),
            )
        ).scalar_one()
        await session.delete(decision_row)
        await session.flush()

        remaining = (
            (
                await session.execute(
                    select(RuleEvaluation).where(
                        RuleEvaluation.decision_id == decision_record.decision_id,
                    ),
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []
