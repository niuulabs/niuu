"""Config-driven pricing provider — models come from Helm values."""

from __future__ import annotations

from typing import TYPE_CHECKING

from volundr.domain.models import Model, ModelProvider, ModelTier
from volundr.domain.ports import PricingProvider

if TYPE_CHECKING:
    from volundr.config import AIModelConfig

# Local models (not managed via Helm config)
_LOCAL_MODELS: list[Model] = [
    Model(
        id="llama3.2:latest",
        name="Llama 3.2",
        description="Open source model running locally via Ollama",
        provider=ModelProvider.LOCAL,
        vendor="local",
        tier=ModelTier.BALANCED,
        color="#F59E0B",
        cost_per_million_tokens=None,
        vram_required="8GB",
    ),
    Model(
        id="codellama:latest",
        name="Code Llama",
        description="Specialized coding model running locally via Ollama",
        provider=ModelProvider.LOCAL,
        vendor="local",
        tier=ModelTier.EXECUTION,
        color="#EF4444",
        cost_per_million_tokens=None,
        vram_required="16GB",
    ),
    Model(
        id="deepseek-r1:latest",
        name="DeepSeek R1",
        description="Advanced reasoning model running locally via Ollama",
        provider=ModelProvider.LOCAL,
        vendor="local",
        tier=ModelTier.REASONING,
        color="#8B5CF6",
        cost_per_million_tokens=None,
        vram_required="32GB",
    ),
]


class HardcodedPricingProvider(PricingProvider):
    """Pricing provider that reads cloud models from config."""

    def __init__(self, model_configs: list[AIModelConfig] | None = None) -> None:
        self._cloud_models: list[Model] = []
        self._pricing: dict[str, float] = {}

        if model_configs:
            for cfg in model_configs:
                self._pricing[cfg.id] = cfg.cost_per_million_tokens
                self._cloud_models.append(
                    Model(
                        id=cfg.id,
                        name=cfg.name,
                        description="",
                        provider=ModelProvider.CLOUD,
                        vendor=str(cfg.provider or "").strip().lower(),
                        tier=ModelTier.BALANCED,
                        color="#2563EB",
                        cost_per_million_tokens=cfg.cost_per_million_tokens,
                        session_definition=cfg.session_definition,
                    )
                )

    def get_price(self, model_id: str) -> float | None:
        return self._pricing.get(model_id)

    def list_models(self) -> list[Model]:
        return self._cloud_models + _LOCAL_MODELS
