"""Agent integration suite — Docker-gated.

The agent runtime touches the real DB. Unit tests for the runtime live
in `test_runtime_unit.py` (no marker, runs everywhere); integration
tests in `test_runtime_integration.py` carry `@pytest.mark.integration`
and are skipped here when Docker isn't running.
"""

from __future__ import annotations

import pytest

from tests.conftest import DOCKER_AVAILABLE


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip Docker-dependent items only — keep pure unit tests live."""
    if DOCKER_AVAILABLE:
        return
    skip_marker = pytest.mark.skip(
        reason="Docker not available — skipping agent integration tests.",
    )
    for item in items:
        if "integration" in {m.name for m in item.iter_markers()}:
            item.add_marker(skip_marker)
