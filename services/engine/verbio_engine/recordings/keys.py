"""Canonical R2 key helpers for recording artifacts.

Mirrors `apps/web/src/features/recordings/r2.ts#sessionObjectKey` so the
two services agree byte-for-byte on the bucket layout. The engine
generates these keys and hands them to LiveKit's egress API as the
upload `filepath`; the web service reads the same keys back from the
egress completion webhook and uses them to mint signed playback URLs.

Layout (one prefix per session, owned by the session row):
  sessions/<uuid>/composite.mp4         — mixed audio/video composite
  sessions/<uuid>/tracks/<identity>.ogg — per-participant audio

`<identity>` is the LiveKit participant identity (slug-safe by
construction — the web side builds it from the participant_id). We
sanitise here as a belt-and-braces guard in case a future identity
source produces something exotic.
"""

from __future__ import annotations

import re
import uuid

_SAFE_IDENTITY_RE = re.compile(r"[^A-Za-z0-9_.-]")
"""Identity characters that survive verbatim in an R2 key.

Anything outside this class is collapsed to `_` so the on-bucket path
stays predictable and safe for shell tooling. We deliberately keep the
mapping lossy-but-deterministic — replay UI never needs to round-trip
the original identity from the key; it has the participants row.
"""

COMPOSITE_FILE_SUFFIX = "composite.mp4"
"""File name for the room-composite recording (audio+video mixed)."""

PARTICIPANT_AUDIO_SUFFIX = ".ogg"
"""Extension for per-participant audio egress (OGG/Opus container)."""


class RecordingKeyInvalidError(ValueError):
    """Raised when a key part would break the `sessions/<uuid>/` prefix."""


def session_prefix(session_id: uuid.UUID) -> str:
    """Return the `sessions/<uuid>/` prefix for a session's R2 objects.

    All composite + per-track keys live under this prefix so the
    retention sweep can list-and-delete a session with one S3 call.
    """
    return f"sessions/{session_id}/"


def composite_audio_key(session_id: uuid.UUID) -> str:
    """Return the R2 key for the room composite recording."""
    return f"{session_prefix(session_id)}{COMPOSITE_FILE_SUFFIX}"


def participant_audio_key(session_id: uuid.UUID, identity: str) -> str:
    """Return the R2 key for one participant's audio egress.

    Sanitises `identity` to a `[A-Za-z0-9_.-]+` slug so identities with
    unusual characters (legacy imports, manual test participants) cannot
    escape the session prefix or break shell tooling.

    Raises:
        RecordingKeyInvalidError: empty identity (cannot be sanitised).
    """
    if not identity:
        msg = "identity must not be empty"
        raise RecordingKeyInvalidError(msg)
    safe = _SAFE_IDENTITY_RE.sub("_", identity)
    return f"{session_prefix(session_id)}tracks/{safe}{PARTICIPANT_AUDIO_SUFFIX}"
