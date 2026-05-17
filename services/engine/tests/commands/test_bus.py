"""Unit tests for `RedisCommandStreamBus` — no Redis required.

Monkeypatches `redis.asyncio.Redis.xread` so we exercise the bus's
cursor-advancement, decoding, idempotent failure modes, and Redis-error
swallowing without a live Redis instance. A Docker-gated integration
smoke against a real Redis container lives in `tests/agent/test_command_drain.py`
once that lands.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import pytest

from verbio_engine.commands import NullCommandBus, RedisCommandStreamBus, commands_stream_key


def _make_command(
    *,
    session_id: uuid.UUID,
    command_id: uuid.UUID | None = None,
    command_type: str = "set_quietness_budget",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "command_id": str(command_id or uuid.uuid4()),
        "session_id": str(session_id),
        "researcher_id": str(uuid.uuid4()),
        "issued_at": datetime.now(UTC).isoformat(),
        "command_type": command_type,
        "payload": payload if payload is not None else {"max_utterances_per_10min": 2},
    }


def _entry(entry_id: bytes, body: dict[str, Any]) -> tuple[bytes, dict[bytes, bytes]]:
    return (entry_id, {b"data": json.dumps(body).encode("utf-8")})


def test_stream_key_matches_brief_prefix() -> None:
    session_id = uuid.uuid4()
    assert commands_stream_key(session_id) == f"verbio:commands:{session_id}"


async def test_drain_no_entries_returns_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RedisCommandStreamBus("redis://localhost:6379/0")
    session_id = uuid.uuid4()

    async def fake_xread(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(bus._client, "xread", fake_xread)

    commands = await bus.drain(session_id)
    assert commands == []

    await bus.aclose()


async def test_drain_decodes_and_advances_cursor(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RedisCommandStreamBus("redis://localhost:6379/0")
    session_id = uuid.uuid4()
    key = commands_stream_key(session_id).encode("utf-8")

    body_a = _make_command(session_id=session_id, command_type="flag_moment", payload={})
    body_b = _make_command(
        session_id=session_id,
        command_type="set_quietness_budget",
        payload={"max_utterances_per_10min": 3},
    )

    calls: list[dict[bytes, bytes]] = []

    async def fake_xread(
        streams: dict[bytes, bytes],
        *,
        count: int,
        block: int | None,
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]] | None:
        _ = count
        # Regression guard: `block=None` is the only mode that doesn't
        # deadlock an empty-stream drain (Redis `BLOCK 0` blocks forever).
        assert block is None, f"drain must pass block=None, got {block!r}"
        calls.append(streams)
        cursor = streams[key]
        if cursor == b"0":
            return [(key, [_entry(b"1700-0", body_a), _entry(b"1700-1", body_b)])]
        return None

    monkeypatch.setattr(bus._client, "xread", fake_xread)

    first = await bus.drain(session_id)
    assert [c.command_type for c in first] == ["flag_moment", "set_quietness_budget"]
    assert [str(c.command_id) for c in first] == [body_a["command_id"], body_b["command_id"]]

    # Second drain reuses the advanced cursor (`1700-1`), gets nothing back.
    second = await bus.drain(session_id)
    assert second == []
    assert calls[0][key] == b"0"
    assert calls[1][key] == b"1700-1"

    await bus.aclose()


async def test_drain_skips_malformed_entries_but_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = RedisCommandStreamBus("redis://localhost:6379/0")
    session_id = uuid.uuid4()
    key = commands_stream_key(session_id).encode("utf-8")

    valid = _make_command(session_id=session_id, command_type="flag_moment", payload={})

    def _entries() -> Iterable[tuple[bytes, dict[bytes, bytes]]]:
        # entry 1: missing `data` field entirely → dropped.
        yield (b"1700-0", {b"other": b"unexpected"})
        # entry 2: data is not valid JSON → dropped.
        yield (b"1700-1", {b"data": b"not-json{"})
        # entry 3: JSON but fails ResearcherCommand validation (wrong type).
        yield (
            b"1700-2",
            {
                b"data": json.dumps(
                    {
                        "command_id": str(uuid.uuid4()),
                        "session_id": str(session_id),
                        "researcher_id": "not-a-uuid-is-fine-payload-is-wrong",
                        "issued_at": datetime.now(UTC).isoformat(),
                        "command_type": "set_quietness_budget",
                        # Missing both required fields.
                        "payload": {},
                    },
                ).encode("utf-8"),
            },
        )
        # entry 4: valid command — must still surface despite the rejects.
        yield _entry(b"1700-3", valid)

    async def fake_xread(
        *_args: Any, **_kwargs: Any
    ) -> list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]] | None:
        return [(key, list(_entries()))]

    monkeypatch.setattr(bus._client, "xread", fake_xread)

    commands = await bus.drain(session_id)
    assert len(commands) == 1
    assert commands[0].command_type == "flag_moment"
    assert str(commands[0].command_id) == valid["command_id"]

    # Cursor advanced past the malformed entries so we never re-read them.
    assert bus._cursors[session_id] == b"1700-3"

    await bus.aclose()


async def test_drain_swallows_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RedisCommandStreamBus("redis://localhost:6379/0")
    session_id = uuid.uuid4()

    async def boom(*_args: Any, **_kwargs: Any) -> None:
        msg = "connection refused"
        raise ConnectionError(msg)

    monkeypatch.setattr(bus._client, "xread", boom)

    # Must not raise — the tick loop's only safe behaviour is "drain returned
    # nothing this tick; try again next tick".
    commands = await bus.drain(session_id)
    assert commands == []

    await bus.aclose()


async def test_aclose_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RedisCommandStreamBus("redis://localhost:6379/0")

    async def boom() -> None:
        msg = "already closed"
        raise RuntimeError(msg)

    monkeypatch.setattr(bus._client, "aclose", boom)

    # aclose must not propagate — the worker shutdown path needs to keep
    # flushing other resources even if redis-py has hiccuped.
    await bus.aclose()


async def test_null_bus_returns_empty_and_acloses_cleanly() -> None:
    bus = NullCommandBus()
    assert await bus.drain(uuid.uuid4()) == []
    await bus.aclose()
    await bus.aclose()
