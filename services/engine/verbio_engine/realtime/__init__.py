"""Realtime event publishing for the dashboard.

The engine writes utterances (Phase 1) and state snapshots (Phase 2 L3)
— and later decisions (Phase 3) — to Postgres for the audit trail, then
publishes a lightweight envelope to Redis pub/sub so the Next.js SSE
route can fan out to live `EventSource` listeners.

Persistence is the source of truth; pub/sub is fire-and-forget. The web
SSE route backfills from Postgres on reconnect (last-event-id), so a
Redis publish that's lost in flight is observable but not data-losing.

Public surface:
  - `RedisEventPublisher`         : connects to Redis, publishes envelopes.
  - `NullEventPublisher`          : no-op for tests / unconfigured environments.
  - `EventPublisher`              : structural type for the runtime to depend on.
  - `TranscriptEvent`             : discriminated-union type alias for any envelope.
  - `TranscriptEventAdapter`      : Pydantic TypeAdapter for validation + JSON Schema.
  - `UtteranceEventEnvelope`      : the `type="utterance"` variant.
  - `UtteranceEventPayload`       : Phase 1 utterance payload.
  - `StateSnapshotEventEnvelope`  : the `type="state_snapshot"` variant (P2 L3).
  - `StateSnapshotEventPayload`   : P2 L3 snapshot payload (full SessionState).
  - `DecisionEventEnvelope`       : the `type="decision"` variant (P3 L10).
  - `DecisionEventPayload`        : P3 L10 decision payload (ModeratorDecision).
  - `SessionFlagEventEnvelope`    : the `type="session_flag"` variant (P5 L7).
  - `SessionFlagEventPayload`     : P5 L7 flag-bookmark payload.
  - `utterance_event`             : convenience constructor for utterance variant.
  - `state_snapshot_event`        : convenience constructor for snapshot variant.
  - `decision_event`              : convenience constructor for decision variant.
  - `session_flag_event`          : convenience constructor for session_flag variant.
  - `channel_for`                 : canonical channel name builder.
"""

from verbio_engine.realtime.events import (
    DecisionEventEnvelope,
    DecisionEventPayload,
    SessionFlagEventEnvelope,
    SessionFlagEventPayload,
    StateSnapshotEventEnvelope,
    StateSnapshotEventPayload,
    TranscriptEvent,
    TranscriptEventAdapter,
    UtteranceEventEnvelope,
    UtteranceEventPayload,
    channel_for,
    decision_event,
    session_flag_event,
    state_snapshot_event,
    utterance_event,
)
from verbio_engine.realtime.publisher import (
    EventPublisher,
    NullEventPublisher,
    RedisEventPublisher,
)

__all__ = [
    "DecisionEventEnvelope",
    "DecisionEventPayload",
    "EventPublisher",
    "NullEventPublisher",
    "RedisEventPublisher",
    "SessionFlagEventEnvelope",
    "SessionFlagEventPayload",
    "StateSnapshotEventEnvelope",
    "StateSnapshotEventPayload",
    "TranscriptEvent",
    "TranscriptEventAdapter",
    "UtteranceEventEnvelope",
    "UtteranceEventPayload",
    "channel_for",
    "decision_event",
    "session_flag_event",
    "state_snapshot_event",
    "utterance_event",
]
