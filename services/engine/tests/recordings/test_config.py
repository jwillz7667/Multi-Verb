"""Unit tests for `R2EgressConfig` + `r2_config_from_settings`."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from verbio_engine.config import Settings
from verbio_engine.recordings import R2EgressConfig, r2_config_from_settings


def test_endpoint_uses_r2_dot_cloudflarestorage_url() -> None:
    cfg = R2EgressConfig(
        account_id="abc123",
        access_key_id="ak",
        secret_access_key="sk",  # noqa: S106 — synthetic test value
        bucket="my-bucket",
    )
    assert cfg.endpoint == "https://abc123.r2.cloudflarestorage.com"


def _settings_with(**overrides: Any) -> Settings:
    """Construct Settings, bypassing env-file lookups for isolation."""
    base: dict[str, Any] = {
        "r2_account_id": None,
        "r2_access_key_id": None,
        "r2_secret_access_key": None,
        "r2_bucket": None,
    }
    base.update(overrides)
    return Settings.model_construct(**base)


def test_r2_config_from_settings_returns_none_when_unset() -> None:
    cfg = r2_config_from_settings(_settings_with())
    assert cfg is None


@pytest.mark.parametrize(
    "missing",
    ["r2_account_id", "r2_access_key_id", "r2_secret_access_key", "r2_bucket"],
)
def test_r2_config_from_settings_returns_none_when_any_var_missing(missing: str) -> None:
    full: dict[str, Any] = {
        "r2_account_id": "acct",
        "r2_access_key_id": SecretStr("ak"),
        "r2_secret_access_key": SecretStr("sk"),
        "r2_bucket": "bucket",
    }
    full[missing] = None
    cfg = r2_config_from_settings(_settings_with(**full))
    assert cfg is None


def test_r2_config_from_settings_unwraps_secret_strs() -> None:
    cfg = r2_config_from_settings(
        _settings_with(
            r2_account_id="acct-xyz",
            r2_access_key_id=SecretStr("ak-test"),
            r2_secret_access_key=SecretStr("sk-test"),
            r2_bucket="verbio-recordings",
        )
    )
    assert cfg is not None
    assert cfg.account_id == "acct-xyz"
    assert cfg.access_key_id == "ak-test"
    assert cfg.secret_access_key == "sk-test"  # noqa: S105 — synthetic test value
    assert cfg.bucket == "verbio-recordings"
    assert cfg.endpoint == "https://acct-xyz.r2.cloudflarestorage.com"
