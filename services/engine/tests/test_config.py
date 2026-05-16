"""`Settings` boundary tests."""

from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from verbio_engine.config import Settings, load_settings


def test_defaults_are_safe_for_local_dev() -> None:
    s = Settings()

    assert s.environment == "development"
    assert s.log_level == "info"
    assert s.tick_interval_ms == 500
    assert s.port == 8000
    assert s.admin_token is None
    assert s.is_production is False


def test_environment_overrides_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "production")
    monkeypatch.setenv("VERBIO_ENGINE_PORT", "9001")
    monkeypatch.setenv("VERBIO_ENGINE_ADMIN_TOKEN", "rotateme")

    s = load_settings()

    assert s.environment == "production"
    assert s.port == 9001
    assert s.is_production is True
    assert isinstance(s.admin_token, SecretStr)
    assert s.admin_token.get_secret_value() == "rotateme"


@pytest.mark.parametrize("bad_port", [0, -1, 65536, 999999])
def test_port_must_be_a_valid_tcp_port(
    monkeypatch: pytest.MonkeyPatch,
    bad_port: int,
) -> None:
    monkeypatch.setenv("VERBIO_ENGINE_PORT", str(bad_port))
    with pytest.raises(ValidationError):
        load_settings()


@pytest.mark.parametrize("bad_tick", [0, 49, 5001, 10_000])
def test_tick_interval_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    bad_tick: int,
) -> None:
    monkeypatch.setenv("TICK_INTERVAL_MS", str(bad_tick))
    with pytest.raises(ValidationError):
        load_settings()


def test_livekit_defaults_unconfigured_for_non_agent_processes() -> None:
    s = Settings()

    # Process can boot without LiveKit creds (admin-only roles); the
    # agent worker entrypoint asserts presence before connecting.
    assert s.livekit_url is None
    assert s.livekit_api_key is None
    assert s.livekit_api_secret is None
    assert s.moderator_identity == "verbio-moderator"
    assert s.moderator_display_name == "Verbio"


def test_livekit_overrides_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_URL", "wss://verbio-test.livekit.cloud")
    monkeypatch.setenv("LIVEKIT_API_KEY", "APIxxxxxxxxx")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secretxxxx")
    monkeypatch.setenv("MODERATOR_IDENTITY", "verbio-agent-stage")
    monkeypatch.setenv("MODERATOR_DISPLAY_NAME", "Verbio (staging)")

    s = load_settings()

    assert s.livekit_url == "wss://verbio-test.livekit.cloud"
    assert isinstance(s.livekit_api_key, SecretStr)
    assert s.livekit_api_key.get_secret_value() == "APIxxxxxxxxx"
    assert isinstance(s.livekit_api_secret, SecretStr)
    assert s.livekit_api_secret.get_secret_value() == "secretxxxx"
    assert s.moderator_identity == "verbio-agent-stage"
    assert s.moderator_display_name == "Verbio (staging)"


def test_openai_embedding_defaults_match_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    # The developer's local env often has OPENAI_API_KEY set; strip it
    # so the test reads the actual default rather than an injected value.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    s = load_settings()
    # No key in dev — embedding-dependent rules degrade to "no signal".
    assert s.openai_api_key is None
    assert s.openai_embedding_model == "text-embedding-3-small"
    assert s.openai_base_url is None


def test_openai_overrides_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-xxxxx")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://proxy.example/v1")

    s = load_settings()

    assert isinstance(s.openai_api_key, SecretStr)
    assert s.openai_api_key.get_secret_value() == "sk-test-xxxxx"
    assert s.openai_embedding_model == "text-embedding-3-large"
    assert s.openai_base_url == "https://proxy.example/v1"
