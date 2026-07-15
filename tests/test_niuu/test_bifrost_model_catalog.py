"""Tests for the shared Bifrost model catalog adapters."""

from __future__ import annotations

import httpx
import pytest
import respx

from bifrost.config import BifrostConfig, ManagedModelConfig, ProviderConfig
from niuu.adapters.bifrost_model_catalog import (
    HttpBifrostModelCatalog,
    StaticBifrostModelCatalog,
    _coerce_alias,
    _coerce_model,
    _coerce_provider,
)
from niuu.domain.model_catalog import (
    ManagedModelProvider,
    ManagedModelTier,
    ProviderHealthState,
)


def test_coerce_model_uses_explicit_values() -> None:
    model = _coerce_model(
        {
            "id": "gpt-5.5",
            "name": "GPT 5.5",
            "vendor": "openai",
            "provider": "local",
            "tier": "frontier",
            "color": "#123456",
            "description": "Fast execution model",
            "cost_per_million_tokens": 12.5,
            "vram_required": "24GB",
            "session_definition": "skuldCodex",
            "supports_tools": False,
            "supports_thinking": False,
            "enabled": False,
            "aliases": ["smart", "default"],
            "provider_keys": ["openai-primary"],
        }
    )
    assert model.id == "gpt-5.5"
    assert model.name == "GPT 5.5"
    assert model.vendor == "openai"
    assert model.provider is ManagedModelProvider.LOCAL
    assert model.tier is ManagedModelTier.FRONTIER
    assert model.color == "#123456"
    assert model.description == "Fast execution model"
    assert model.cost_per_million_tokens == 12.5
    assert model.vram_required == "24GB"
    assert model.session_definition == "skuldCodex"
    assert model.supports_tools is False
    assert model.supports_thinking is False
    assert model.enabled is False
    assert model.aliases == ("smart", "default")
    assert model.provider_keys == ("openai-primary",)


def test_coerce_model_falls_back_to_defaults() -> None:
    model = _coerce_model({"id": "claude-sonnet-4-6"})
    assert model.name == ""
    assert model.vendor == ""
    assert model.provider is ManagedModelProvider.CLOUD
    assert model.tier is ManagedModelTier.BALANCED
    assert model.color == "#2563EB"
    assert model.supports_tools is True
    assert model.supports_thinking is True
    assert model.enabled is True
    assert model.aliases == ()
    assert model.provider_keys == ()


def test_coerce_alias_strips_values() -> None:
    alias = _coerce_alias({"alias": " smart ", "target": " gpt-5.5 "})
    assert alias.alias == "smart"
    assert alias.target == "gpt-5.5"


def test_coerce_provider_uses_defaults() -> None:
    provider = _coerce_provider({"key": "openai", "vendor": "openai"})
    assert provider.key == "openai"
    assert provider.vendor == "openai"
    assert provider.base_url == ""
    assert provider.model_ids == ()
    assert provider.timeout_seconds == 120.0
    assert provider.cost_per_token == 0.0
    assert provider.state is ProviderHealthState.UNKNOWN
    assert provider.detail == ""


@pytest.mark.asyncio
async def test_static_catalog_uses_configured_bifrost_catalog() -> None:
    config = BifrostConfig(
        models=[
            ManagedModelConfig(
                id="gpt-5.5",
                name="GPT 5.5",
                vendor="openai",
                tier=ManagedModelTier.FRONTIER,
                session_definition="skuldCodex",
            )
        ],
        providers={"openai-primary": ProviderConfig(models=["gpt-5.5"])},
        aliases={"smart": "gpt-5.5"},
    )
    catalog = StaticBifrostModelCatalog(config)

    models = await catalog.list_models()
    assert models[0].id == "gpt-5.5"
    assert models[0].provider_keys == ("openai-primary",)
    assert models[0].aliases == ("smart",)

    model = await catalog.get_model("smart")
    assert model is not None
    assert model.id == "gpt-5.5"
    assert model.session_definition == "skuldCodex"

    aliases = await catalog.list_aliases()
    assert aliases == [_coerce_alias({"alias": "smart", "target": "gpt-5.5"})]

    providers = await catalog.list_providers()
    assert len(providers) == 1
    assert providers[0].key == "openai-primary"
    assert providers[0].vendor == "openai"


@pytest.mark.asyncio
async def test_static_catalog_without_explicit_config_uses_builtin_catalog() -> None:
    catalog = StaticBifrostModelCatalog()
    model = await catalog.get_model("claude-sonnet-5")
    assert model is not None
    assert model.vendor == "anthropic"


@pytest.mark.asyncio
@respx.mock
async def test_http_catalog_round_trips_models_aliases_and_providers() -> None:
    base_url = "http://bifrost.example/api/v1/bifrost"
    respx.get(f"{base_url}/models").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "gpt-5.5",
                    "name": "GPT 5.5",
                    "vendor": "openai",
                    "provider": "cloud",
                    "tier": "execution",
                    "session_definition": "skuldCodex",
                    "aliases": ["smart"],
                    "provider_keys": ["openai-primary"],
                }
            ],
        )
    )
    respx.get(f"{base_url}/models/gpt-5.5").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "gpt-5.5",
                "name": "GPT 5.5",
                "vendor": "openai",
                "provider": "cloud",
                "tier": "execution",
                "session_definition": "skuldCodex",
            },
        )
    )
    respx.get(f"{base_url}/aliases").mock(
        return_value=httpx.Response(200, json=[{"alias": "smart", "target": "gpt-5.5"}])
    )
    respx.get(f"{base_url}/providers/health").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "openai-primary",
                    "vendor": "openai",
                    "base_url": "https://api.openai.com/v1",
                    "model_ids": ["gpt-5.5"],
                    "timeout_seconds": 30,
                    "cost_per_token": 0.002,
                    "state": "healthy",
                    "detail": "serving",
                }
            ],
        )
    )

    catalog = HttpBifrostModelCatalog(base_url)

    models = await catalog.list_models()
    assert len(models) == 1
    assert models[0].provider is ManagedModelProvider.CLOUD
    assert models[0].tier is ManagedModelTier.EXECUTION
    assert models[0].session_definition == "skuldCodex"

    model = await catalog.get_model("gpt-5.5")
    assert model is not None
    assert model.id == "gpt-5.5"

    aliases = await catalog.list_aliases()
    assert aliases[0].alias == "smart"
    assert aliases[0].target == "gpt-5.5"

    providers = await catalog.list_providers()
    assert providers[0].state is ProviderHealthState.HEALTHY
    assert providers[0].detail == "serving"


@pytest.mark.asyncio
@respx.mock
async def test_http_catalog_returns_none_for_missing_model() -> None:
    base_url = "http://bifrost.example/api/v1/bifrost"
    respx.get(f"{base_url}/models/missing-model").mock(return_value=httpx.Response(404))
    catalog = HttpBifrostModelCatalog(base_url)
    assert await catalog.get_model("missing-model") is None
