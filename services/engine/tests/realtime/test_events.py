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

from verbio_engine.realtime import (
    TranscriptEvent,
    UtteranceEventPayload,
    channel_for,
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


def test_transcript_event_serialises_through_json_round_trip() -> None:
    event = TranscriptEvent(
        type="utterance",
        id=str(uuid.uuid4()),
        session_id=uuid.uuid4(),
        ts=datetime.now(UTC),
        payload=_payload(),
    )

    raw = event.model_dump_json()
    reloaded = TranscriptEvent.model_validate_json(raw)
    assert reloaded == event


def test_confidence_outside_unit_interval_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _payload(confidence=1.5)
    with pytest.raises(ValidationError):
        _payload(confidence=-0.1)


def test_extra_fields_are_forbidden_on_envelope() -> None:
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
        TranscriptEvent.model_validate(json.loads(json.dumps(body)))


def test_blank_identity_is_rejected() -> None:
    with pytest.raises(ValidationError):
        _payload(participant_identity="")
    with pytest.raises(ValidationError):
        _payload(participant_display_name="")
