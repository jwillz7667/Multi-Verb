"""Recording egress — engine-side public surface.

The engine owns the call site that asks LiveKit Cloud to record each
session and upload the result to Cloudflare R2. Composite recording +
per-participant audio start at the matching lifecycle hooks
(`on_room_connected` and `on_participant_joined`); the synchronous
`EgressInfo` confirms LiveKit accepted the start. Completion arrives at
the web service as a webhook (Phase 6 L3), which is where the final
R2 URL gets persisted to the `sessions` row.

This barrel is the only import path other modules should use; deep
imports into `recordings.dispatcher` are reserved for tests.
"""

from verbio_engine.recordings.config import R2_REGION, R2EgressConfig, r2_config_from_settings
from verbio_engine.recordings.dispatcher import (
    EgressDispatcher,
    EgressHandle,
    LiveKitEgressDispatcher,
    NullEgressDispatcher,
)
from verbio_engine.recordings.keys import (
    COMPOSITE_FILE_SUFFIX,
    PARTICIPANT_AUDIO_SUFFIX,
    RecordingKeyInvalidError,
    composite_audio_key,
    participant_audio_key,
    session_prefix,
)

__all__ = [
    "COMPOSITE_FILE_SUFFIX",
    "PARTICIPANT_AUDIO_SUFFIX",
    "R2_REGION",
    "EgressDispatcher",
    "EgressHandle",
    "LiveKitEgressDispatcher",
    "NullEgressDispatcher",
    "R2EgressConfig",
    "RecordingKeyInvalidError",
    "composite_audio_key",
    "participant_audio_key",
    "r2_config_from_settings",
    "session_prefix",
]
