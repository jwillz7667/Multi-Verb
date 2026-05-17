"""Unit tests for the R2 key helpers.

The key format is part of the engine ↔ web contract: the engine writes
recordings under `sessions/<uuid>/...`, the web service mints signed
URLs from the same prefix. These tests pin the shape so a refactor
can't silently break replay playback.
"""

from __future__ import annotations

import uuid

import pytest

from verbio_engine.recordings import (
    COMPOSITE_FILE_SUFFIX,
    PARTICIPANT_AUDIO_SUFFIX,
    RecordingKeyInvalidError,
    composite_audio_key,
    participant_audio_key,
    session_prefix,
)


def test_session_prefix_ends_with_slash() -> None:
    sid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert session_prefix(sid) == "sessions/11111111-1111-1111-1111-111111111111/"


def test_composite_audio_key_combines_prefix_and_suffix() -> None:
    sid = uuid.UUID("22222222-2222-2222-2222-222222222222")
    key = composite_audio_key(sid)
    assert key.startswith(session_prefix(sid))
    assert key.endswith(COMPOSITE_FILE_SUFFIX)
    assert key == "sessions/22222222-2222-2222-2222-222222222222/composite.mp4"


def test_participant_audio_key_preserves_safe_identity() -> None:
    sid = uuid.UUID("33333333-3333-3333-3333-333333333333")
    key = participant_audio_key(sid, "alice-42")
    assert key == "sessions/33333333-3333-3333-3333-333333333333/tracks/alice-42.ogg"
    assert key.endswith(PARTICIPANT_AUDIO_SUFFIX)


@pytest.mark.parametrize(
    ("identity", "expected_slug"),
    [
        ("alice bob", "alice_bob"),
        ("p:with:colons", "p_with_colons"),
        ("path/traversal", "path_traversal"),
        ("p.with.dots", "p.with.dots"),
        ("p_with_underscores", "p_with_underscores"),
        ("p-with-dashes", "p-with-dashes"),
        ("UPPER_lower_123", "UPPER_lower_123"),
        ("../escape", ".._escape"),
        ("中文-id", "__-id"),
    ],
)
def test_participant_audio_key_sanitises_identity(identity: str, expected_slug: str) -> None:
    sid = uuid.UUID("44444444-4444-4444-4444-444444444444")
    key = participant_audio_key(sid, identity)
    assert key == f"sessions/44444444-4444-4444-4444-444444444444/tracks/{expected_slug}.ogg"


def test_participant_audio_key_rejects_empty_identity() -> None:
    sid = uuid.UUID("55555555-5555-5555-5555-555555555555")
    with pytest.raises(RecordingKeyInvalidError):
        participant_audio_key(sid, "")


def test_keys_share_session_prefix() -> None:
    """Composite + per-participant keys must live under the same prefix.

    Retention lists the session prefix and deletes everything underneath;
    if these diverged, one artifact would survive the sweep.
    """
    sid = uuid.uuid4()
    prefix = session_prefix(sid)
    assert composite_audio_key(sid).startswith(prefix)
    assert participant_audio_key(sid, "p1").startswith(prefix)
