"""Tests for the pricing provider adapter."""

import pytest

from bifrost.config import ManagedModelConfig
from volundr.adapters.outbound.pricing import HardcodedPricingProvider
from volundr.domain.models import ModelProvider


class TestHardcodedPricingProvider:
    """Tests for HardcodedPricingProvider."""

    @pytest.fixture
    def configs(self) -> list[ManagedModelConfig]:
        return [
            ManagedModelConfig(
                id="claude-opus-4-6",
                name="Opus 4.6",
                cost_per_million_tokens=15.0,
            ),
            ManagedModelConfig(
                id="claude-sonnet-4-6",
                name="Sonnet 4.6",
                cost_per_million_tokens=3.0,
            ),
            ManagedModelConfig(
                id="claude-haiku-4-5-20251001",
                name="Haiku 4.5",
                cost_per_million_tokens=1.0,
            ),
        ]

    @pytest.fixture
    def provider(self, configs: list[ManagedModelConfig]) -> HardcodedPricingProvider:
        return HardcodedPricingProvider(configs)

    def test_get_price_cloud_model(self, provider: HardcodedPricingProvider):
        assert provider.get_price("claude-opus-4-6") == 15.0
        assert provider.get_price("claude-sonnet-4-6") == 3.0
        assert provider.get_price("claude-haiku-4-5-20251001") == 1.0

    def test_get_price_local_model_returns_none(self, provider: HardcodedPricingProvider):
        assert provider.get_price("llama3.2:latest") is None

    def test_get_price_unknown_model_returns_none(self, provider: HardcodedPricingProvider):
        assert provider.get_price("nonexistent-model") is None

    def test_list_models_returns_catalog_projection(self, provider: HardcodedPricingProvider):
        models = provider.list_models()
        cloud = [m for m in models if m.provider == ModelProvider.CLOUD]
        assert len(cloud) == 3

    def test_list_models_returns_copy(self, provider: HardcodedPricingProvider):
        models1 = provider.list_models()
        models2 = provider.list_models()
        assert models1 is not models2

    def test_no_config_returns_empty_catalog(self):
        provider = HardcodedPricingProvider()
        models = provider.list_models()
        assert models == []

    def test_cloud_models_have_pricing(self, provider: HardcodedPricingProvider):
        models = provider.list_models()
        cloud = [m for m in models if m.provider == ModelProvider.CLOUD]
        for model in cloud:
            assert model.cost_per_million_tokens is not None
            assert model.cost_per_million_tokens > 0

    def test_local_models_have_vram(self, provider: HardcodedPricingProvider):
        models = provider.list_models()
        local = [m for m in models if m.provider == ModelProvider.LOCAL]
        for model in local:
            assert model.vram_required is not None

    def test_replace_models_replaces_existing_catalog(self):
        provider = HardcodedPricingProvider(
            [
                ManagedModelConfig(
                    id="claude-opus-4-6",
                    name="Opus 4.6",
                    cost_per_million_tokens=15.0,
                )
            ]
        )

        provider.replace_models(
            [
                ManagedModelConfig(
                    id="llama3.2:latest",
                    name="Llama 3.2",
                    provider="local",
                    vram_required="24GB",
                    session_definition="skuldCodex",
                )
            ]
        )

        assert provider.get_price("claude-opus-4-6") is None
        models = provider.list_models()
        assert len(models) == 1
        assert models[0].id == "llama3.2:latest"
        assert models[0].provider == ModelProvider.LOCAL
        assert models[0].vram_required == "24GB"
        assert models[0].session_definition == "skuldCodex"
