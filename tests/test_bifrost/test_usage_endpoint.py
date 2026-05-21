"""Tests for the GET /v1/usage endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from bifrost.app import create_app
from bifrost.config import BifrostConfig, ProviderConfig
from bifrost.ports.usage_store import UsageRecord


def _make_config() -> BifrostConfig:
    return BifrostConfig(providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])})


def _make_record(
    agent_id: str = "agent-1",
    tenant_id: str = "tenant-1",
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
    cost_usd: float = 0.001,
    timestamp: datetime | None = None,
) -> UsageRecord:
    return UsageRecord(
        request_id="req-1",
        agent_id=agent_id,
        tenant_id=tenant_id,
        session_id="sess",
        saga_id="saga",
        model=model,
        provider="anthropic",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost_usd,
        timestamp=timestamp or datetime.now(UTC),
    )


@pytest.fixture
def app_with_data():
    """App with pre-seeded usage records."""
    config = _make_config()
    app = create_app(config)
    return app


class TestUsageEndpoint:
    @pytest.fixture
    def client(self) -> TestClient:
        config = _make_config()
        app = create_app(config)
        with TestClient(app) as c:
            yield c

    def _seed(self, client: TestClient, n: int = 3) -> None:
        """Make *n* completion requests to populate the usage store."""
        from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo

        response = AnthropicResponse(
            id="msg",
            content=[TextBlock(text="hi")],
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            usage=UsageInfo(input_tokens=100, output_tokens=50),
        )
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = response
            for _ in range(n):
                client.post(
                    "/api/v1/bifrost/v1/messages",
                    json={
                        "model": "claude-sonnet-4-6",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                )

    def test_empty_usage_returns_zero_summary(self, client):
        resp = client.get("/api/v1/bifrost/v1/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_requests"] == 0
        assert data["summary"]["total_cost_usd"] == 0.0
        assert data["records"] == []

    def test_usage_counts_after_requests(self, client):
        self._seed(client, 3)
        resp = client.get("/api/v1/bifrost/v1/usage")
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_requests"] == 3
        assert data["summary"]["total_input_tokens"] == 300
        assert data["summary"]["total_output_tokens"] == 150
        assert len(data["records"]) == 3

    def test_filter_by_agent_id(self, client):
        from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo

        response = AnthropicResponse(
            id="msg",
            content=[TextBlock(text="hi")],
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
            usage=UsageInfo(input_tokens=100, output_tokens=50),
        )
        with patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as m:
            m.return_value = response
            client.post(
                "/api/v1/bifrost/v1/messages",
                headers={"x-agent-id": "agent-A"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
            client.post(
                "/api/v1/bifrost/v1/messages",
                headers={"x-agent-id": "agent-B"},
                json={
                    "model": "claude-sonnet-4-6",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )

        resp = client.get("/api/v1/bifrost/v1/usage", params={"agent_id": "agent-A"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["summary"]["total_requests"] == 1
        assert all(r["agent_id"] == "agent-A" for r in data["records"])

    def test_filter_by_model(self, client):
        self._seed(client, 2)
        resp = client.get("/api/v1/bifrost/v1/usage", params={"model": "claude-sonnet-4-6"})
        data = resp.json()
        assert data["summary"]["total_requests"] == 2

        resp2 = client.get("/api/v1/bifrost/v1/usage", params={"model": "gpt-4o"})
        data2 = resp2.json()
        assert data2["summary"]["total_requests"] == 0

    def test_filter_by_time_range(self, client):
        self._seed(client, 2)
        far_future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
        resp = client.get("/api/v1/bifrost/v1/usage", params={"since": far_future})
        data = resp.json()
        assert data["summary"]["total_requests"] == 0

    def test_invalid_since_returns_422(self, client):
        resp = client.get("/api/v1/bifrost/v1/usage", params={"since": "not-a-date"})
        assert resp.status_code == 422

    def test_invalid_until_returns_422(self, client):
        resp = client.get("/api/v1/bifrost/v1/usage", params={"until": "bad-date"})
        assert resp.status_code == 422

    def test_invalid_granularity_returns_422(self, client):
        resp = client.get("/api/v1/bifrost/v1/usage", params={"granularity": "minute"})
        assert resp.status_code == 422

    def test_response_includes_per_model_breakdown(self, client):
        self._seed(client, 2)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        by_model = data["summary"]["by_model"]
        assert "claude-sonnet-4-6" in by_model
        assert by_model["claude-sonnet-4-6"]["requests"] == 2

    def test_response_includes_per_provider_breakdown(self, client):
        self._seed(client, 2)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        by_provider = data["summary"]["by_provider"]
        # Provider is looked up as "anthropic" for claude-sonnet-4-6.
        assert "anthropic" in by_provider
        assert by_provider["anthropic"]["requests"] == 2

    def test_cost_is_tracked_and_returned(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        # claude-sonnet-4-6: 100 input + 50 output tokens
        # 100 * 3.0/1M + 50 * 15.0/1M = 0.0003 + 0.00075 = 0.00105
        assert data["summary"]["total_cost_usd"] > 0.0

    def test_records_have_required_fields(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        record = data["records"][0]
        required_fields = [
            "request_id",
            "agent_id",
            "tenant_id",
            "session_id",
            "saga_id",
            "model",
            "provider",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
            "cost_usd",
            "latency_ms",
            "streaming",
            "timestamp",
        ]
        for field in required_fields:
            assert field in record, f"Missing field: {field}"

    def test_records_include_streaming_flag(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        record = data["records"][0]
        assert isinstance(record["streaming"], bool)

    def test_records_include_latency_ms(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        record = data["records"][0]
        assert isinstance(record["latency_ms"], int | float)

    def test_records_include_provider(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        record = data["records"][0]
        assert record["provider"] == "anthropic"

    def test_limit_parameter_respected(self, client):
        self._seed(client, 5)
        resp = client.get("/api/v1/bifrost/v1/usage", params={"limit": 2})
        data = resp.json()
        assert len(data["records"]) <= 2
        # Summary still reflects all records (no limit on summary).
        assert data["summary"]["total_requests"] == 5

    def test_granularity_hour_returns_timeseries(self, client):
        self._seed(client, 2)
        resp = client.get("/api/v1/bifrost/v1/usage", params={"granularity": "hour"})
        assert resp.status_code == 200
        data = resp.json()
        assert "timeseries" in data
        assert isinstance(data["timeseries"], list)
        assert len(data["timeseries"]) >= 1
        entry = data["timeseries"][0]
        assert "bucket" in entry
        assert "requests" in entry
        assert "input_tokens" in entry
        assert "output_tokens" in entry
        assert "cost_usd" in entry

    def test_granularity_day_returns_timeseries(self, client):
        self._seed(client, 3)
        resp = client.get("/api/v1/bifrost/v1/usage", params={"granularity": "day"})
        assert resp.status_code == 200
        data = resp.json()
        assert "timeseries" in data
        # All seeded records are in the same day.
        assert len(data["timeseries"]) == 1
        assert data["timeseries"][0]["requests"] == 3

    def test_no_granularity_no_timeseries(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage")
        data = resp.json()
        assert "timeseries" not in data

    def test_timeseries_bucket_format(self, client):
        self._seed(client, 1)
        resp = client.get("/api/v1/bifrost/v1/usage", params={"granularity": "hour"})
        data = resp.json()
        bucket = data["timeseries"][0]["bucket"]
        # Must be parseable as ISO-8601.
        dt = datetime.fromisoformat(bucket)
        assert dt.tzinfo is not None
