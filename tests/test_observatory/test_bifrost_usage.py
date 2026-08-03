"""Bifröst's usage records become signal lines and an observed edge rate.

The gateway has always recorded every proxied call — caller, provider, model,
latency, tokens — and nothing read it. The canvas could say a model existed
but never that anything used it, and the signal log carried only discovery
failures.
"""

from __future__ import annotations

import httpx
import pytest

from observatory.entity_discovery import BifrostUsageDiscoveryAdapter


def _usage(records: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/usage")
        assert request.url.params["since"]
        return httpx.Response(200, json={"summary": {}, "records": records})

    return httpx.MockTransport(handler)


def _adapter(records: list[dict], **kwargs) -> BifrostUsageDiscoveryAdapter:
    return BifrostUsageDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="valhalla",
        transport=_usage(records),
        **kwargs,
    )


RECORD = {
    "request_id": "req-1",
    "agent_id": "muninn",
    "model": "nvidia/nemotron-3-super",
    "provider": "valaskjalf-nemotron",
    "input_tokens": 23,
    "output_tokens": 16,
    "cost_usd": 0.0,
    "latency_ms": 1508.0,
    "timestamp": "2026-08-03T01:00:55+00:00",
}


@pytest.mark.asyncio
async def test_a_call_becomes_a_line_naming_who_asked() -> None:
    result = await _adapter([RECORD]).discover()

    (event,) = result.events
    assert event["type"] == "BIFROST"
    assert event["subject"] == "muninn"
    assert "→ nvidia/nemotron-3-super" in event["body"]
    assert "via valaskjalf-nemotron" in event["body"]
    assert "1.5s" in event["body"]
    assert "23/16 tokens" in event["body"]
    # Keyed on the request, so an overlapping poll cannot report it twice.
    assert event["id"].endswith("req-1")


@pytest.mark.asyncio
async def test_an_unclaimed_call_says_so_rather_than_guessing() -> None:
    result = await _adapter([{**RECORD, "agent_id": ""}]).discover()

    assert result.events[0]["subject"] == "anonymous"


@pytest.mark.asyncio
async def test_the_rate_lands_on_the_edge_the_catalogue_already_drew() -> None:
    records = [{**RECORD, "request_id": f"req-{i}"} for i in range(10)]

    result = await _adapter(records, window_minutes=5.0).discover()

    (edge,) = result.edges
    assert edge["sourceId"] == "bifrost:valhalla"
    assert edge["targetId"] == "model:valhalla:nvidia-nemotron-3-super"
    assert edge["relationType"] == "routes_to"
    assert edge["confidence"] == "observed"
    assert edge["ratePerMinute"] == 2.0


@pytest.mark.asyncio
async def test_a_quiet_gateway_reports_no_rate_at_all() -> None:
    """An edge nothing measured must carry no rate, so nothing animates it."""
    result = await _adapter([]).discover()

    assert result.edges == []
    assert result.events == []


@pytest.mark.asyncio
async def test_an_unreachable_gateway_warns_instead_of_raising() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    adapter = BifrostUsageDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="valhalla",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert result.edges == []
    assert result.events[0]["level"] == "warning"


def test_a_window_must_be_positive() -> None:
    with pytest.raises(ValueError, match="window_minutes"):
        BifrostUsageDiscoveryAdapter(base_url="http://bifrost.test", window_minutes=0)
