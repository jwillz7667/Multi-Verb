"""Persistence integration suite — Docker-gated.

Shared fixtures (`postgres_url`, `engine`, `session`, etc.) live in the
root `tests/conftest.py` so the agent suite can reuse them. This module
exists only to skip the whole subtree when Docker isn't available so
the unit suite stays fast on developer laptops without Docker running.
"""

from __future__ import annotations

import pytest

from tests.conftest import DOCKER_AVAILABLE

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason="Docker not available — skipping persistence integration tests.",
)
