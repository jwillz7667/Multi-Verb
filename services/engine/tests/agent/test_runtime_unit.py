"""Unit tests for `agent.runtime` — no DB, no LiveKit.

These exercise the pure-Python pieces of `SessionRuntime`: snapshot
parsing, role inference, error surface. The DB-touching transitions are
covered by `tests/agent/test_runtime_integration.py`.
"""

from __future__ import annotations

import pytest

from verbio_engine.agent.runtime import ParticipantSnapshot, _parse_role


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (None, "participant"),
        ("", "participant"),
        ('{"role": "researcher"}', "researcher"),
        ('{"role": "moderator"}', "moderator"),
        ('{"role": "participant"}', "participant"),
        # Default-deny when role is missing — never silently grant
        # researcher privileges.
        ('{"display_name": "Maya"}', "participant"),
        # Malformed JSON falls back to participant.
        ("not-json", "participant"),
        # Unknown role string falls back to participant.
        ('{"role": "admin"}', "participant"),
        # Non-object JSON (e.g. an array) falls back to participant.
        ('["researcher"]', "participant"),
    ],
)
def test_parse_role_default_denies_privileged_roles(metadata: str | None, expected: str) -> None:
    assert _parse_role(metadata) == expected


def test_from_livekit_uses_identity_when_display_name_blank() -> None:
    snap = ParticipantSnapshot.from_livekit(
        identity="id-abc",
        display_name="",
        metadata=None,
    )

    assert snap.identity == "id-abc"
    assert snap.display_name == "id-abc"
    assert snap.role == "participant"


def test_from_livekit_preserves_metadata_raw() -> None:
    raw = '{"role": "researcher", "org_id": "o-123"}'
    snap = ParticipantSnapshot.from_livekit(
        identity="id-r1",
        display_name="Researcher 1",
        metadata=raw,
    )

    assert snap.role == "researcher"
    assert snap.metadata_raw == raw
