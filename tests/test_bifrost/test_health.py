"""Tests for Bifrost health endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient


class TestHealthEndpoint:
    def test_health_returns_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["caps"]["cap_hits_total"] >= 0

    def test_health_reports_cap_hits(self, client: TestClient) -> None:
        from niuu.cap_telemetry import record_cap_hit

        record_cap_hit("test.bifrost_health_knob", "raise test.bifrost_health_knob")
        caps = client.get("/health").json()["caps"]
        assert caps["cap_hits.test.bifrost_health_knob"] >= 1

    def test_health_has_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health")
        assert "x-correlation-id" in response.headers

    def test_health_preserves_correlation_id(self, client: TestClient) -> None:
        response = client.get("/health", headers={"X-Correlation-ID": "bifrost-test-123"})
        assert response.headers["x-correlation-id"] == "bifrost-test-123"
