"""Unit tests for `RedisEventPublisher` — no Redis required.

Patches `redis.asyncio.Redis.publish` so we cover the publisher's
behavioural contract without needing a live Redis instance. The
docker-gated integration smoke (publisher → real Redis → subscriber)
runs as part of the L6 Playwright suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from verbio_engine.realtime import (
    NullEventPublisher,
    RedisEventPublisher,
    TranscriptEventAdapter,
    UtteranceEventEnvelope,
    UtteranceEventPayload,
    channel_for,
)


def _event() -> UtteranceEventEnvelope:
    session_id = uuid.uuid4()
    return UtteranceEventEnvelope(
        type="utterance",
        id=str(uuid.uuid4()),
        session_id=session_id,
        ts=datetime.now(UTC),
        payload=UtteranceEventPayload(
            utterance_id=uuid.uuid4(),
            session_id=session_id,
            participant_id=uuid.uuid4(),
            participant_identity="id-x",
            participant_display_name="X",
            text="hello",
            is_final=True,
            confidence=0.9,
            start_ts=datetime.now(UTC),
            end_ts=datetime.now(UTC),
        ),
    )


async def test_publish_sends_to_canonical_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    pub = RedisEventPublisher("redis://localhost:6379/0")
    event = _event()
    calls: list[tuple[str, bytes]] = []

    async def fake_publish(channel: str, body: bytes) -> int:
        calls.append((channel, body))
        return 3

    monkeypatch.setattr(pub._client, "publish", fake_publish)

    delivered = await pub.publish(event)

    assert delivered == 3
    assert calls and calls[0][0] == channel_for(event.session_id)
    # The body is the canonical Pydantic JSON serialisation — round-trip
    # via the discriminated-union adapter rather than the concrete class
    # so the test exercises the same parse path the SSE consumer uses.
    body = calls[0][1]
    assert TranscriptEventAdapter.validate_json(body) == event

    await pub.aclose()


async def test_publish_swallows_redis_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistence has already happened — Redis hiccups must not propagate."""
    pub = RedisEventPublisher("redis://localhost:6379/0")

    async def boom(*_args: Any, **_kwargs: Any) -> int:
        msg = "connection refused"
        raise ConnectionError(msg)

    monkeypatch.setattr(pub._client, "publish", boom)

    delivered = await pub.publish(_event())

    assert delivered == 0

    await pub.aclose()


async def test_aclose_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    pub = RedisEventPublisher("redis://localhost:6379/0")
    call_count = 0

    async def fake_aclose() -> None:
        nonlocal call_count
        call_count += 1

    monkeypatch.setattr(pub._client, "aclose", fake_aclose)

    await pub.aclose()
    await pub.aclose()
    assert call_count == 2  # both calls reached the underlying client cleanly


async def test_null_publisher_returns_zero_subscribers() -> None:
    pub = NullEventPublisher()

    assert await pub.publish(_event()) == 0
    # aclose is also safe to call repeatedly.
    await pub.aclose()
    await pub.aclose()
