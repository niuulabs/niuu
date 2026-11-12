"""Tests for the Volundr Bifrost catalog HTTP adapter."""

from __future__ import annotations

import httpx
import pytest

from niuu.adapters.outbound.http_auth import StaticBearerTokenAuthAdapter
from volundr.adapters.outbound.bifrost_catalog_http import HttpBifrostCatalogAdapter


@pytest.mark.asyncio
async def test_list_models_calls_bifrost_models_endpoint_and_maps_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/bifrost/models"
        assert request.headers["authorization"] == "Bearer token-123"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "gpt-5",
                    "name": "GPT-5",
                    "provider": "cloud",
                    "vendor": "openai",
                    "tier": "frontier",
                    "session_definition": "skuldCodex",
                    "cost_per_million_tokens": 12.5,
                }
            ],
        )

    adapter = HttpBifrostCatalogAdapter(
        base_url="http://guild.test",
        auth=StaticBearerTokenAuthAdapter(token="token-123"),
        transport=httpx.MockTransport(handler),
    )

    models = await adapter.list_models()

    assert len(models) == 1
    assert models[0].id == "gpt-5"
    assert models[0].vendor == "openai"
    assert models[0].session_definition == "skuldCodex"


@pytest.mark.asyncio
async def test_list_models_carries_the_context_window_and_null() -> None:
    adapter = HttpBifrostCatalogAdapter(
        base_url="http://guild.test",
        auth=StaticBearerTokenAuthAdapter(token="token-123"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json=[
                    {"id": "known", "context_window": 1_000_000},
                    {"id": "unknown", "context_window": None},
                    {"id": "absent"},
                ],
            )
        ),
    )

    models = await adapter.list_models()

    assert [model.context_window for model in models] == [1_000_000, None, None]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -1, "1000000", 2.5, True])
async def test_list_models_refuses_a_malformed_context_window(bad: object) -> None:
    adapter = HttpBifrostCatalogAdapter(
        base_url="http://guild.test",
        auth=StaticBearerTokenAuthAdapter(token="token-123"),
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=[{"id": "m", "context_window": bad}])
        ),
    )

    with pytest.raises(ValueError, match="model 'm' has context_window"):
        await adapter.list_models()
