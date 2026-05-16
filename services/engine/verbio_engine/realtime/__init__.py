"""Realtime event publishing for the dashboard.

The engine writes utterances (Phase 1) — and later decisions and state
snapshots (Phase 3) — to Postgres for the audit trail, then publishes a
lightweight envelope to Redis pub/sub so the Next.js SSE route can fan
out to live `EventSource` listeners.

Persistence is the source of truth; pub/sub is fire-and-forget. The web
SSE route backfills from Postgres on reconnect (last-event-id), so a
Redis publish that's lost in flight is observable but not data-losing.

Public surface:
  - `RedisEventPublisher` : connects to Redis, publishes envelopes.
  - `TranscriptEvent`     : SSE envelope (Pydantic, single source of truth).
  - `UtteranceEventPayload`: the Phase 1 payload shape.
  - `channel_for`         : canonical channel name builder.
"""

from verbio_engine.realtime.events import (
    TranscriptEvent,
    UtteranceEventPayload,
    channel_for,
    utterance_event,
)
from verbio_engine.realtime.publisher import (
    EventPublisher,
    NullEventPublisher,
    RedisEventPublisher,
)

__all__ = [
    "EventPublisher",
    "NullEventPublisher",
    "RedisEventPublisher",
    "TranscriptEvent",
    "UtteranceEventPayload",
    "channel_for",
    "utterance_event",
]
