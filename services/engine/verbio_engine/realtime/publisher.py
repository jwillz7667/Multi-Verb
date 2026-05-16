"""Redis pub/sub publisher for SSE events.

The engine writes its audit-trail rows to Postgres first (the
durability layer) and then publishes a lightweight envelope to Redis
for fan-out to the dashboard via the Next.js SSE route. If the
publish fails or the message is lost in flight, the SSE route's
Postgres backfill recovers it on the next reconnect — pub/sub is the
fast path, not the source of truth.

This module never crashes the caller:
  - construction is sync (just stores the URL); the connection is
    lazy on first publish.
  - publish errors are logged and swallowed; the utterance has already
    been persisted by the time we get here.

Design notes:
  - One `RedisEventPublisher` per worker (not per session). The
    underlying `redis.asyncio.Redis` connection is multiplexed —
    publishing on N channels uses one socket.
  - Closing happens at worker shutdown; `aclose()` is idempotent.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from redis import asyncio as redis_async

from verbio_engine.logging import get_logger
from verbio_engine.realtime.events import (
    DecisionEventEnvelope,
    StateSnapshotEventEnvelope,
    UtteranceEventEnvelope,
    channel_for,
)

log = get_logger(__name__)


# Concrete union the publisher dispatches over. We accept the variants
# directly (not the `TranscriptEvent` discriminated alias) so the
# publisher API remains a normal nominal type that mypy can narrow on
# attribute access without unwrapping the `Annotated[...]` wrapper.
_PublishableEvent = UtteranceEventEnvelope | StateSnapshotEventEnvelope | DecisionEventEnvelope


@runtime_checkable
class EventPublisher(Protocol):
    """Structural type for `SessionRuntime` to depend on.

    Lets the runtime accept either the live Redis publisher or a no-op /
    in-memory fake during tests without inheriting from a base class.
    """

    async def publish(self, event: _PublishableEvent) -> int: ...

    async def aclose(self) -> None: ...


class RedisEventPublisher:
    """Async pub/sub publisher for SSE envelopes."""

    def __init__(self, url: str) -> None:
        """Store the connection URL; connection is opened lazily.

        `from_url` itself does no I/O — it constructs the connection pool
        and the first command (`publish`) opens the socket. This keeps
        the worker startup path light when Redis is unreachable, so a
        slow Redis doesn't gate the agent joining the room.
        """
        self._url = url
        # `decode_responses=False` because we only publish bytes; the SSE
        # route decodes utf-8 itself.
        self._client: redis_async.Redis = redis_async.Redis.from_url(
            url,
            decode_responses=False,
        )

    async def publish(self, event: _PublishableEvent) -> int:
        """Publish one envelope to `verbio:events:{session_id}`.

        Returns the number of subscribers that received the message (per
        Redis `PUBLISH` semantics). Zero is normal — it just means no
        dashboard is currently watching.

        Failures are logged and swallowed: the canonical row is already
        in Postgres, and the SSE route's reconnect backfill will pick
        the row up. We refuse to crash the audio pipeline because Redis
        is degraded.
        """
        channel = channel_for(event.session_id)
        # `by_alias=False` is the default — our field names already
        # match the wire shape. `exclude_none=False` so the consumer
        # can rely on a stable shape regardless of optional values.
        body = event.model_dump_json().encode("utf-8")
        try:
            # redis-py's async API is typed as `Awaitable[Any]`; PUBLISH
            # always returns an int (subscriber count), so coerce explicitly
            # rather than swallow the type narrowing.
            return int(await self._client.publish(channel, body))
        except Exception as exc:
            log.warning(
                "realtime.publish_failed",
                session_id=str(event.session_id),
                event_type=event.type,
                event_id=event.id,
                error=str(exc),
            )
            return 0

    async def aclose(self) -> None:
        """Tear down the connection pool. Safe to call multiple times."""
        try:
            await self._client.aclose()
        except Exception as exc:
            # Already-closed / never-opened conditions raise; we don't
            # care at shutdown time.
            log.debug("realtime.close_warning", error=str(exc))


class NullEventPublisher:
    """No-op publisher used when Redis is intentionally unconfigured.

    Lets the agent worker run end-to-end in environments that don't yet
    have a Redis URL (e.g., a smoke test where the dashboard isn't
    booted). Same surface as `RedisEventPublisher` so the runtime
    doesn't branch on the publisher type.
    """

    async def publish(self, event: _PublishableEvent) -> int:
        _ = event  # interface — argument deliberately ignored.
        return 0

    async def aclose(self) -> None:
        return None
