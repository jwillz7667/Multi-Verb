"""Event-sourced state projection — see `state.store.StateStore`.

This is the public surface for the projection layer:
  * `StateStore` — fold events into snapshots.
  * `StateStoreConfig` — knobs for window sizes and flag thresholds.
  * `StateEvent` and the concrete event types — input contract.
  * `PersistAndPublishListener` — tick-loop callback that persists each
    snapshot and publishes it to Redis (Phase 2 L3).

`SessionState` itself lives in `verbio_engine.domain.session_state`
because it is the shared cross-service shape (Pydantic → JSON Schema →
TS) and belongs with the other canonical domain types.
"""

from verbio_engine.state.config import StateStoreConfig
from verbio_engine.state.events import (
    ParticipantJoinEvent,
    ParticipantLeaveEvent,
    StateEvent,
    UtteranceFinalEvent,
    VadOffEvent,
    VadOnEvent,
)
from verbio_engine.state.listener import PersistAndPublishListener
from verbio_engine.state.store import StateStore

__all__ = [
    "ParticipantJoinEvent",
    "ParticipantLeaveEvent",
    "PersistAndPublishListener",
    "StateEvent",
    "StateStore",
    "StateStoreConfig",
    "UtteranceFinalEvent",
    "VadOffEvent",
    "VadOnEvent",
]
