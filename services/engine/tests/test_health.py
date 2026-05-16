"""Engine `/health` and `/ready` smoke tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from verbio_engine import __version__

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "verbio-engine"
    assert body["version"] == __version__
    assert body["environment"] == "development"


def test_ready_reports_phase_zero_skips(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"postgres": "skip", "redis": "skip", "livekit": "skip"}


def test_docs_exposed_outside_production(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
