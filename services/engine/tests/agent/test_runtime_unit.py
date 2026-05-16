"""Unit tests for `agent.runtime` — no DB, no LiveKit.

These exercise the pure-Python pieces of `SessionRuntime`: snapshot
parsing, role inference, error surface. The DB-touching transitions are
covered by `tests/agent/test_runtime_integration.py`.
"""

from __future__ import annotations

import asyncio

import pytest

from verbio_engine.agent.runtime import (
    ParticipantSnapshot,
    SessionRuntime,
    _parse_role,
)


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


# ---------------------------------------------------------------------------
# participant_id cache + await_participant_id — pure asyncio, no DB.
# ---------------------------------------------------------------------------


def test_participant_id_for_returns_none_when_unknown() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    assert rt.participant_id_for("ghost") is None


async def test_await_participant_id_resolves_when_event_fires() -> None:
    """`track_subscribed` may race ahead of `participant_connected`.

    The transcriber awaits the per-identity Event; once the join
    handler populates the cache + sets the event, the wait returns.
    """
    import uuid

    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    pid = uuid.uuid4()

    async def _resolve_later() -> None:
        # Let the awaiter park on the Event first.
        await asyncio.sleep(0)
        rt._participant_ids["id-a"] = pid
        rt._participant_events.setdefault("id-a", asyncio.Event()).set()

    waker = asyncio.create_task(_resolve_later())
    try:
        got = await rt.await_participant_id("id-a", timeout=1.0)
    finally:
        await waker
    assert got == pid


async def test_await_participant_id_returns_immediately_when_already_cached() -> None:
    import uuid

    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    pid = uuid.uuid4()
    rt._participant_ids["id-cached"] = pid

    # Should not raise TimeoutError even with a tiny timeout.
    got = await rt.await_participant_id("id-cached", timeout=0.001)
    assert got == pid


async def test_await_participant_id_times_out_when_never_joined() -> None:
    rt = SessionRuntime(session_factory=None, room_name="r")  # type: ignore[arg-type]
    with pytest.raises(asyncio.TimeoutError):
        await rt.await_participant_id("never", timeout=0.05)
