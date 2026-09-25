"""Tests for the standalone Ravn API service entrypoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ravn.main import create_app


def test_create_app_exposes_health_under_its_api_prefix() -> None:
    """A standalone Ravn instance is commonly registered in Guild with a
    base_url already carrying the /api/v1/ravn prefix (the same convention
    rest_ravn.py uses to reach a Volundr-colocated Ravn service), so the
    health probe needs this route to actually resolve — see
    DEFAULT_HEALTH_PATHS in http_instance_probe.py."""
    app = create_app()
    with TestClient(app) as client:
        root = client.get("/health")
        api = client.get("/api/v1/ravn/health")

    assert root.status_code == 200
    assert api.status_code == 200
    assert root.json() == {"status": "healthy"}
    assert api.json() == root.json()
