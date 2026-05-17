"""`configure_sentry` boundary tests.

The init call is heavy (network handshake) and global; we patch
`sentry_sdk.init` and assert the call shape rather than letting the
real SDK touch the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from verbio_engine.config import Settings
from verbio_engine.sentry import configure_sentry

if TYPE_CHECKING:
    import pytest


def test_returns_false_when_dsn_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    settings = Settings()

    with patch("verbio_engine.sentry.sentry_sdk.init") as init:
        enabled = configure_sentry(settings)

    assert enabled is False
    init.assert_not_called()


def test_initialises_when_dsn_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")
    settings = Settings()

    with patch("verbio_engine.sentry.sentry_sdk.init") as init:
        enabled = configure_sentry(settings)

    assert enabled is True
    init.assert_called_once()
    kwargs = init.call_args.kwargs
    assert kwargs["dsn"] == "https://public@example.ingest.sentry.io/1"
    assert kwargs["environment"] == "staging"
    assert kwargs["traces_sample_rate"] == 0.25
    # IRB posture: no participant content sent to Sentry.
    assert kwargs["send_default_pii"] is False
    # LoggingIntegration must be wired so stdlib `_log.exception(...)` reaches Sentry.
    integration_names = {type(i).__name__ for i in kwargs["integrations"]}
    assert "LoggingIntegration" in integration_names


def test_traces_sample_rate_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0")
    settings = Settings()

    with patch("verbio_engine.sentry.sentry_sdk.init") as init:
        configure_sentry(settings)

    assert init.call_args.kwargs["traces_sample_rate"] == 0.0
