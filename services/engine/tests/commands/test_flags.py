"""Unit tests for `commands.flags` (P5 L7).

Covers `apply_flag_commands` in isolation: builds batches of
`ResearcherCommand`s, asserts the walker shapes the correct
`SessionFlagInsert` records, drops commands whose researcher_id was
missing from the pre-validated map, and ignores non-flag commands.

The listener-level wiring (audit row + flag row + SSE per row) is
covered by `tests/decisions/test_listener.py`; this module stays
narrowly focused on the pure batch-walking logic — same split as
`test_control.py` / `test_budget.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from verbio_engine.commands import (
    FLAG_COMMAND_TYPES,
    FlagEffects,
    apply_flag_commands,
)
from verbio_engine.domain.command import ResearcherCommand, ResearcherCommandType


def _command(
    command_type: ResearcherCommandType,
    *,
    payload: dict[str, object] | None = None,
    researcher_id: str | None = None,
    session_id: uuid.UUID | None = None,
    command_id: uuid.UUID | None = None,
    issued_at: datetime | None = None,
) -> ResearcherCommand:
    return ResearcherCommand(
        command_id=command_id if command_id is not None else uuid.uuid4(),
        session_id=session_id if session_id is not None else uuid.uuid4(),
        researcher_id=researcher_id if researcher_id is not None else str(uuid.uuid4()),
        issued_at=issued_at if issued_at is not None else datetime.now(UTC),
        command_type=command_type,
        payload=payload if payload is not None else {},
    )


class TestFlagCommandTypes:
    """The frozen set of command types this module owns."""

    def test_set_membership_is_singleton(self) -> None:
        # Brief §5.4: flag_moment is its own layer (P5 L7) because it
        # writes to a different table (`session_flags`) than the audit
        # plane and does not produce a `ModeratorDecision`.
        assert frozenset({"flag_moment"}) == FLAG_COMMAND_TYPES

    def test_other_command_types_excluded(self) -> None:
        for command_type in (
            "force_prompt",
            "force_redirect",
            "force_summary",
            "whisper",
            "mute_moderator",
            "unmute_moderator",
            "pause_session",
            "resume_session",
            "set_quietness_budget",
            "end_session",
        ):
            assert command_type not in FLAG_COMMAND_TYPES


class TestApplyFlagCommands:
    """End-to-end batch walking semantics."""

    def test_empty_batch_returns_empty_effects(self) -> None:
        effects = apply_flag_commands(commands=[], researcher_id_for={})
        assert effects == FlagEffects()
        assert effects.applied is False
        assert effects.applied_count == 0
        assert effects.records == ()
        assert effects.counts == {}

    def test_non_flag_commands_are_skipped(self) -> None:
        # All other command types are owned by other handlers. The
        # walker drops them silently — they still get an audit row via
        # `_persist_commands`, but never a `session_flags` row.
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        commands = [
            _command("mute_moderator", researcher_id=researcher_str),
            _command(
                "force_prompt",
                payload={"prompt": "ask Maria"},
                researcher_id=researcher_str,
            ),
            _command(
                "whisper",
                payload={"text": "hi"},
                researcher_id=researcher_str,
            ),
            _command("pause_session", researcher_id=researcher_str),
            _command(
                "set_quietness_budget",
                payload={"max_utterances_per_10min": 5},
                researcher_id=researcher_str,
            ),
            _command(
                "end_session",
                payload={},
                researcher_id=researcher_str,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.applied is False
        assert effects.applied_count == 0
        assert effects.records == ()
        assert effects.counts == {}

    def test_single_flag_shapes_one_record(self) -> None:
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        session_id = uuid.uuid4()
        command_id = uuid.uuid4()
        issued = datetime(2026, 5, 17, 11, 30, 0, tzinfo=UTC)
        commands = [
            _command(
                "flag_moment",
                payload={"note": "good cross-talk moment"},
                researcher_id=researcher_str,
                session_id=session_id,
                command_id=command_id,
                issued_at=issued,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.applied is True
        assert effects.applied_count == 1
        assert effects.counts == {"flag_moment": 1}
        assert len(effects.records) == 1

        record = effects.records[0]
        # Wire-shape command_id is the row PK — re-drains after restart
        # collapse to the same row.
        assert record.id == command_id
        assert record.session_id == session_id
        assert record.ts == issued
        assert record.researcher_id == researcher_uuid
        assert record.note == "good cross-talk moment"
        # Researcher-issued, always.
        assert record.auto_generated is False

    def test_flag_with_no_note_shapes_record_with_none(self) -> None:
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        commands = [
            _command(
                "flag_moment",
                payload={},  # note is optional
                researcher_id=researcher_str,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.applied_count == 1
        assert effects.records[0].note is None

    def test_flag_with_missing_researcher_id_is_dropped(self) -> None:
        # `_persist_commands` skips commands whose researcher_id won't
        # parse as a UUID, so the map never gets that key. The walker
        # must match that policy — no audit row, no bookmark.
        commands = [
            _command(
                "flag_moment",
                payload={"note": "x"},
                researcher_id="not-a-uuid",
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={},  # parse failed upstream
        )
        # Walker still counts the command (so the structured-log line
        # shows the researcher tried), but no record is shaped.
        assert effects.applied_count == 1
        assert effects.records == ()
        assert effects.counts == {"flag_moment": 1}


class TestBatchOrdering:
    """FIFO ordering when multiple flag_moment commands arrive together."""

    def test_two_flags_emit_two_records_in_fifo_order(self) -> None:
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        session_id = uuid.uuid4()
        first_id = uuid.uuid4()
        second_id = uuid.uuid4()
        first_ts = datetime(2026, 5, 17, 11, 30, 0, tzinfo=UTC)
        second_ts = datetime(2026, 5, 17, 11, 30, 0, 500_000, tzinfo=UTC)
        commands = [
            _command(
                "flag_moment",
                payload={"note": "first"},
                researcher_id=researcher_str,
                session_id=session_id,
                command_id=first_id,
                issued_at=first_ts,
            ),
            _command(
                "flag_moment",
                payload={"note": "second"},
                researcher_id=researcher_str,
                session_id=session_id,
                command_id=second_id,
                issued_at=second_ts,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.applied_count == 2
        assert effects.counts == {"flag_moment": 2}
        # FIFO: first command → first record.
        assert effects.records[0].id == first_id
        assert effects.records[0].note == "first"
        assert effects.records[0].ts == first_ts
        assert effects.records[1].id == second_id
        assert effects.records[1].note == "second"
        assert effects.records[1].ts == second_ts

    def test_mix_of_flag_and_non_flag_walks_only_flag(self) -> None:
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        flag_id = uuid.uuid4()
        commands = [
            _command("mute_moderator", researcher_id=researcher_str),
            _command(
                "flag_moment",
                payload={"note": "interesting cross-talk"},
                researcher_id=researcher_str,
                command_id=flag_id,
            ),
            _command(
                "set_quietness_budget",
                payload={"max_utterances_per_10min": 6},
                researcher_id=researcher_str,
            ),
            _command(
                "force_prompt",
                payload={"prompt": "ask"},
                researcher_id=researcher_str,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.applied_count == 1
        assert effects.counts == {"flag_moment": 1}
        assert len(effects.records) == 1
        assert effects.records[0].id == flag_id

    def test_flags_from_two_researchers_in_one_batch(self) -> None:
        # Multi-observer session: two researchers click flag within the
        # same tick. Each ends up with its own row, each tied to its
        # own researcher_id.
        first_str = str(uuid.uuid4())
        first_uuid = uuid.UUID(first_str)
        second_str = str(uuid.uuid4())
        second_uuid = uuid.UUID(second_str)
        session_id = uuid.uuid4()
        commands = [
            _command(
                "flag_moment",
                payload={"note": "from first"},
                researcher_id=first_str,
                session_id=session_id,
            ),
            _command(
                "flag_moment",
                payload={"note": "from second"},
                researcher_id=second_str,
                session_id=session_id,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={
                first_str: first_uuid,
                second_str: second_uuid,
            },
        )
        assert effects.applied_count == 2
        assert len(effects.records) == 2
        assert effects.records[0].researcher_id == first_uuid
        assert effects.records[0].note == "from first"
        assert effects.records[1].researcher_id == second_uuid
        assert effects.records[1].note == "from second"

    def test_only_dropped_flags_still_count_but_emit_no_records(self) -> None:
        # Batch where every flag has an unparseable researcher_id —
        # walker counts them (for the log line) but emits nothing.
        commands = [
            _command(
                "flag_moment",
                payload={"note": "a"},
                researcher_id="bad-1",
            ),
            _command(
                "flag_moment",
                payload={"note": "b"},
                researcher_id="bad-2",
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={},
        )
        assert effects.applied_count == 2
        assert effects.records == ()
        assert effects.counts == {"flag_moment": 2}


class TestPayloadValidation:
    """The walker re-validates payloads as belt-and-braces."""

    def test_note_at_max_length_is_accepted(self) -> None:
        # FlagMomentPayload.note is min_length=1, max_length=1000.
        researcher_str = str(uuid.uuid4())
        researcher_uuid = uuid.UUID(researcher_str)
        long_note = "x" * 1000
        commands = [
            _command(
                "flag_moment",
                payload={"note": long_note},
                researcher_id=researcher_str,
            ),
        ]
        effects = apply_flag_commands(
            commands=commands,
            researcher_id_for={researcher_str: researcher_uuid},
        )
        assert effects.records[0].note == long_note

    def test_construction_rejects_empty_note(self) -> None:
        # ResearcherCommand validates payload at construction time;
        # min_length=1 on note means an empty string never reaches the
        # walker. The walker's defensive re-validation is for
        # post-deserialization drift, not for catching constructor
        # bypasses.
        with pytest.raises(ValueError, match="invalid payload"):
            ResearcherCommand(
                command_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                researcher_id=str(uuid.uuid4()),
                issued_at=datetime.now(UTC),
                command_type="flag_moment",
                payload={"note": ""},
            )
