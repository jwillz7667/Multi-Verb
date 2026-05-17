"""R2 destination config for LiveKit egress.

LiveKit Cloud writes egress output directly to S3-compatible storage —
the engine never proxies the bytes, it just hands the credentials and
endpoint to the egress API on each `Start*Egress` call. R2 is S3-shaped
so we use the same `S3Upload` proto with R2-specific values for
`region`, `endpoint`, and `force_path_style`.

The engine accepts the same four env vars as the web side (the human
operator copies them from the Cloudflare dashboard into both services'
env). The `from_settings()` helper returns None when any of the four are
missing — that's the signal to the worker to skip dispatcher
construction and run in shadow-recording mode (no egress, replay UI
will simply show "no recording available").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verbio_engine.config import Settings


R2_REGION = "auto"
"""R2's S3-compat region literal. Required verbatim."""


@dataclass(frozen=True, slots=True)
class R2EgressConfig:
    """Immutable R2 destination handed to LiveKit's egress S3Upload proto.

    `endpoint` is computed from `account_id` so the worker doesn't have
    to know the Cloudflare URL shape — passing the four pieces from env
    is enough. `force_path_style=True` because virtual-hosted-style on
    R2 requires a custom domain we don't assume.
    """

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    @property
    def endpoint(self) -> str:
        """R2 S3 endpoint URL — `https://<account>.r2.cloudflarestorage.com`."""
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def r2_config_from_settings(settings: Settings) -> R2EgressConfig | None:
    """Build an `R2EgressConfig` from `Settings`, or None if any var is unset.

    Returning None (rather than raising) keeps the worker bootable when
    R2 isn't configured — the recordings layer simply doesn't run, the
    rest of the engine keeps working. The caller branches once on the
    return value and never has to handle a `KeyError`-shaped failure
    later.
    """
    account_id = settings.r2_account_id
    access_key_id = (
        settings.r2_access_key_id.get_secret_value() if settings.r2_access_key_id else None
    )
    secret_access_key = (
        settings.r2_secret_access_key.get_secret_value() if settings.r2_secret_access_key else None
    )
    bucket = settings.r2_bucket
    if not account_id or not access_key_id or not secret_access_key or not bucket:
        return None
    return R2EgressConfig(
        account_id=account_id,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
    )
