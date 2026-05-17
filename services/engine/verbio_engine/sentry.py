"""Sentry initialisation for verbio-engine (P7 L4).

`configure_sentry(settings)` is a no-op when `SENTRY_DSN` is unset,
mirroring the web side. Both the FastAPI admin server (`main.py`) and
the LiveKit agent worker (`agent/worker.py`) call it at startup; calling
twice in the same process is safe (Sentry's `init` resets the hub).

We intentionally enable only the integrations we actually rely on:

* `LoggingIntegration` so a `log.error(...)` from the existing
  structlog/stdlib pipeline forwards as a Sentry event without any
  per-call `capture_exception` glue.
* The FastAPI/Starlette integrations are pulled in by the
  `sentry-sdk[fastapi]` extra and auto-attach when FastAPI is imported.

We do NOT enable PII (`send_default_pii`) — IRB posture (brief §13.4)
forbids forwarding participant audio/transcripts to third-party error
trackers; Sentry events carry stack traces and structured context only.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration

if TYPE_CHECKING:
    from verbio_engine.config import Settings


def configure_sentry(settings: Settings) -> bool:
    """Initialise Sentry from settings.

    Returns True when Sentry was actually configured (DSN present),
    False otherwise. The boolean is useful for tests + a single-line
    boot log so operators can see at a glance whether reporting is on.
    """
    dsn = settings.sentry_dsn
    if dsn is None:
        return False

    sentry_sdk.init(
        dsn=dsn.get_secret_value(),
        environment=settings.environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        release=None,
        # Forward stdlib WARNING+ as breadcrumbs, ERROR+ as events.
        # structlog → stdlib bridge means our existing `log.error(...)`
        # calls reach this without any extra wiring.
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
        # IRB posture: participant content stays out of error reports.
        send_default_pii=False,
        # Cap to keep accidentally-large payloads (e.g., a full
        # SessionState dump in `extra`) from blowing Sentry's per-event
        # quotas. Sentry's default is 4 KB — bump to 16 KB so legit
        # debug context fits without truncation.
        max_request_body_size="medium",
    )
    return True
