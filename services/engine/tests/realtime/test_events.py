"""Unit tests for the SSE event envelope shapes.

Validates Pydantic invariants — discriminator literal, channel naming,
JSON round-trip — so any change to the wire format surfaces here before
the web side observes it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from verbio_engine.domain import ParticipantState, QuietnessBudget, SessionState
from verbio_engine.realtime import (
    StateSnapshotEventEnvelope,
    StateSnapshotEventPayload,
    TranscriptEventAdapter,
    UtteranceEventEnvelope,
    UtteranceEventPayload,
    channel_for,
    state_snapshot_event,
    utterance_event,
)


def _payload(**overrides: object) -> UtteranceEventPayload:
    defaults: dict[str, object] = {
        "utterance_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "participant_id": uuid.uuid4(),
        "participant_identity": "id-1",
        "participant_display_name": "Speaker 1",
        "text": "hello",
        "is_final": True,
        "confidence": 0.92,
        "start_ts": datetime.now(UTC),
        "end_ts": datetime.now(UTC),
    }
    defaults.update(overrides)
    return UtteranceEventPayload(**defaults)  # type: ignore[arg-type]


def _state(*, session_id: uuid.UUID | None = None, tick_id: int = 0) -> SessionState:
    sid = session_id or uuid.uuid4()
    started = datetime.now(UTC)
    return SessionState(
        session_id=sid,
        tick_id=tick_id,
        t=started,
        started_at=started,
        elapsed_sec=0.0,
        participants={
            "p1": ParticipantState(  # type: ignore[call-arg]
                participant_id="p1",
                display_name="Maya",
                joined_at=started,
            ),
        },
        quietness_budget=QuietnessBudget(),
    )


def test_channel_for_uses_brief_prefix() -> None:
    session_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert channel_for(session_id) == "verbio:events:11111111-1111-1111-1111-111111111111"


def test_utterance_event_constructor_mirrors_id_and_session() -> None:
    session_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    utterance_id = uuid.uuid4()
    start = datetime.now(UTC)
    end = datetime.now(UTC)

    event = utterance_event(
        utterance_id=utterance_id,
        session_id=session_id,
        participant_id=participant_id,
        participant_identity="id-7",
        participant_display_name="Maya",
        text="testing",
        is_final=False,
        confidence=0.5,
        start_ts=start,
        end_ts=end,
    )

    assert event.type == "utterance"
    assert event.id == str(utterance_id)
    assert event.session_id == session_id
    assert event.payload.participant_identity == "id-7"
    assert event.payload.is_final is False


def test_utterance_envelope_round_trips_through_adapter() -> None:
    event = UtteranceEventEnvelope(
        type="utterance",
        id=str(uuid.uuid4()),
        session_id=uuid.uuid4(),
        ts=datetime.now(UTC),
        payload=_payload(),
    )

    raw = event.model_dump_json()
    reloaded = TranscriptEventAdapter.validate_json(raw)
    assert reloaded == event


def test_state_snapshot_event_constructor_mirrors_id_and_clock() -> None:
    session_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()
    state = _state(session_id=session_id, tick_id=42)

    event = state_snapshot_event(snapshot_id=snapshot_id, state=state)

    assert event.type == "state_snapshot"
    assert event.id == str(snapshot_id)
    assert event.session_id == session_id
    assert event.ts == state.t
    assert event.payload.snapshot_id == snapshot_id
    assert event.payload.state.tick_id == 42


def test_state_snapshot_envelope_round_trips_through_adapter() -> None:
    snapshot_id = uuid.uuid4()
    event = state_snapshot_event(snapshot_id=snapshot_id, state=_state())

    raw = event.model_dump_json()
    reloaded = TranscriptEventAdapter.validate_json(raw)

    assert isinstance(reloaded, StateSnapshotEventEnvelope)
    assert reloaded == event


def test_adapter_discriminates_on_type_field() -> None:
    utter = utterance_event(
        utterance_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        participant_id=uuid.uuid4(),
        participant_identity="id-x",
        participant_display_name="X",
        text="hi",
        is_final=True,
        confidence=None,
        start_ts=datetime.now(UTC),
        end_ts=datetime.now(UTC),
    )
    snap = state_snapshot_event(snapshot_id=uuid.uuid4(), state=_state())

    parsed_utter = TranscriptEventAdapter.validate_json(utter.model_dump_json())
    parsed_snap = TranscriptEventAdapter.validate_json(snap.model_dump_json())

    assert isinstance(parsed_utter, UtteranceEventEnvelope)
    assert isinstance(parsed_snap, StateSnapshotEventEnvelope)


def test_confidence_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _payload(confidence=1.5)
    with pytest.raises(ValidationError):
        _payload(confidence=-0.1)


def test_extra_fields_are_forbidden_on_utterance_envelope() -> None:
    """Wire-shape drift between languages must surface as a hard error."""
    body = {
        "type": "utterance",
        "id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": _payload().model_dump(mode="json"),
        "unexpected": "field",
    }
    with pytest.raises(ValidationError):
        TranscriptEventAdapter.validate_python(json.loads(json.dumps(body)))


def test_extra_fields_are_forbidden_on_state_snapshot_envelope() -> None:
    payload = StateSnapshotEventPayload(snapshot_id=uuid.uuid4(), state=_state())
    body = {
        "type": "state_snapshot",
        "id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
        "ts": datetime.now(UTC).isoformat(),
        "payload": payload.model_dump(mode="json"),
        "unexpected": "field",
    }
    with pytest.raises(ValidationError):
        TranscriptEventAdapter.validate_python(json.loads(json.dumps(body)))


def test_blank_identity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _payload(participant_identity="")
    with pytest.raises(ValidationError):
        _payload(participant_display_name="")
