"""Models served by our own hardware show up as local, whatever the provider key."""

from __future__ import annotations

import pytest

from bifrost.catalog import list_models
from bifrost.config import BifrostConfig, ProviderConfig
from niuu.domain.model_catalog import ManagedModelProvider


@pytest.mark.parametrize("provider_key", ["vllm", "ollama", "local"])
def test_self_hosted_provider_models_are_local(provider_key: str) -> None:
    config = BifrostConfig(
        providers={
            provider_key: ProviderConfig(
                base_url="http://host.docker.internal:8000", models=["nvidia/nemotron-test"]
            )
        }
    )
    entry = next(m for m in list_models(config) if m.id == "nvidia/nemotron-test")
    assert entry.vendor == "local"
    assert entry.provider == ManagedModelProvider.LOCAL


def test_cloud_provider_models_keep_their_vendor() -> None:
    config = BifrostConfig(
        providers={"openai": ProviderConfig(base_url="https://api.openai.com", models=["gpt-x"])}
    )
    entry = next(m for m in list_models(config) if m.id == "gpt-x")
    assert entry.vendor == "openai"
    assert entry.provider == ManagedModelProvider.CLOUD
