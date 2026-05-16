"""Decision repository — one insert per tick (Phase 3 L9).

Every tick of the engine produces exactly one `ModeratorDecision`
(silent or spoken); the resolver hands it off and the tick-loop wiring
(P3 L10) calls this repo to put a row down. Persistence happens
*before* any mouth/TTS execution — brief invariant: a crash mid-tick
must never leave a spoken utterance with no decision record.

API:
  - `insert(record)` : persist one decision; returns the row with its id.

No `insert_many` here: decisions arrive serially from a per-session
tick loop, one every 500ms. Bulk import for replay tooling can grow
the method when that path lands.

This module is intentionally read-free; replay queries are a Phase 6
concern and live alongside the export tooling, not here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from verbio_engine.persistence.models import Decision

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class DecisionInsert:
    """Input record for `DecisionRepo.insert`.

    Field shapes match the brief §10.1 SQL one-to-one. `decision_id` is
    accepted from the caller (the resolver mints it via `uuid4()` so
    `rule_evaluations.decision_id` can be set in the same pass without
    a round-trip).

    `target_participant_id` is a DB UUID, not the LiveKit identity that
    `ModeratorDecision.target_participant_id` carries — the caller
    translates from LiveKit identity to DB UUID before constructing
    this record. Decisions whose target was a participant since purged
    pass None and the row keeps the moderator's intent without the link.
    """

    decision_id: uuid.UUID
    session_id: uuid.UUID
    tick_id: int
    ts: datetime
    action: str
    source: str
    was_executed: bool
    cooldown_until: datetime
    target_participant_id: uuid.UUID | None = None
    triggering_rule: str | None = None
    researcher_id: uuid.UUID | None = None
    researcher_hint: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    reason_human: str = ""
    confidence: float | None = None
    suppressed_by: list[str] = field(default_factory=list)
    llm_prompt: dict[str, object] | None = None
    llm_output: str | None = None
    tts_audio_url: str | None = None
    spoken_at: datetime | None = None


class DecisionRepo:
    """Async repository for the `decisions` table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert(self, record: DecisionInsert) -> Decision:
        """Persist a single decision and return the row.

        The caller-supplied `decision_id` is honoured verbatim — the
        resolver already minted it as the parent id for the matching
        `rule_evaluations` batch, so we don't generate a new one.
        """
        row = Decision(
            id=record.decision_id,
            session_id=record.session_id,
            tick_id=record.tick_id,
            ts=record.ts,
            action=record.action,
            target_participant_id=record.target_participant_id,
            source=record.source,
            triggering_rule=record.triggering_rule,
            researcher_id=record.researcher_id,
            researcher_hint=record.researcher_hint,
            reason_codes=list(record.reason_codes),
            reason_human=record.reason_human,
            confidence=record.confidence,
            suppressed_by=list(record.suppressed_by),
            was_executed=record.was_executed,
            llm_prompt=record.llm_prompt,
            llm_output=record.llm_output,
            tts_audio_url=record.tts_audio_url,
            spoken_at=record.spoken_at,
            cooldown_until=record.cooldown_until,
        )
        self._session.add(row)
        await self._session.flush([row])
        return row
