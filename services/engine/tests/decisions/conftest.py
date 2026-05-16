"""Decisions integration suite — Docker-gated.

Unit tests in this directory don't touch the DB and run everywhere;
integration tests carry `@pytest.mark.integration` and are skipped
here when Docker isn't running so the unit suite stays fast on
developer laptops without Docker.
"""

from __future__ import annotations

import pytest

from tests.conftest import DOCKER_AVAILABLE


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip Docker-dependent items only — keep pure unit tests live."""
    if DOCKER_AVAILABLE:
        return
    skip_marker = pytest.mark.skip(
        reason="Docker not available — skipping decisions integration tests.",
    )
    for item in items:
        if "integration" in {m.name for m in item.iter_markers()}:
            item.add_marker(skip_marker)
