"""Config-driven pricing provider backed by the canonical Bifrost catalog."""

from __future__ import annotations

from typing import Any

from niuu.domain.model_catalog import ManagedModelProvider, ManagedModelTier
from volundr.domain.models import Model, ModelProvider, ModelTier
from volundr.domain.ports import PricingProvider

_PROVIDER_BY_NAME = {
    ManagedModelProvider.CLOUD.value: ModelProvider.CLOUD,
    ManagedModelProvider.LOCAL.value: ModelProvider.LOCAL,
}

_TIER_BY_NAME = {
    ManagedModelTier.FRONTIER.value: ModelTier.FRONTIER,
    ManagedModelTier.BALANCED.value: ModelTier.BALANCED,
    ManagedModelTier.EXECUTION.value: ModelTier.EXECUTION,
    ManagedModelTier.REASONING.value: ModelTier.REASONING,
}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip()


def _model_provider(value: Any) -> ModelProvider:
    return _PROVIDER_BY_NAME.get(_enum_value(value), ModelProvider.CLOUD)


def _model_tier(value: Any) -> ModelTier:
    return _TIER_BY_NAME.get(_enum_value(value), ModelTier.BALANCED)


class HardcodedPricingProvider(PricingProvider):
    """Pricing provider that projects Bifrost-managed models into Forge metadata."""

    def __init__(self, model_configs: list[Any] | None = None) -> None:
        self._models: list[Model] = []
        self._pricing: dict[str, float] = {}

        self.replace_models(model_configs or [])

    def replace_models(self, model_configs: list[Any]) -> None:
        """Replace the projected catalog with a fresh Bifrost snapshot."""
        self._models = []
        self._pricing = {}

        for cfg in model_configs:
            if not bool(getattr(cfg, "enabled", True)):
                continue
            model_id = str(getattr(cfg, "id", "") or "").strip()
            if not model_id:
                continue
            cost = getattr(cfg, "cost_per_million_tokens", None)
            if cost is not None:
                self._pricing[model_id] = float(cost)
            self._models.append(
                Model(
                    id=model_id,
                    name=str(getattr(cfg, "name", "") or model_id).strip(),
                    description=str(getattr(cfg, "description", "") or "").strip(),
                    provider=_model_provider(getattr(cfg, "provider", "")),
                    vendor=(
                        str(getattr(cfg, "vendor", "") or "").strip().lower()
                        or str(getattr(cfg, "provider", "") or "").strip().lower()
                    ),
                    tier=_model_tier(getattr(cfg, "tier", "")),
                    color=str(getattr(cfg, "color", "") or "#2563EB"),
                    cost_per_million_tokens=float(cost) if cost is not None else None,
                    vram_required=str(getattr(cfg, "vram_required", "") or "").strip() or None,
                    session_definition=(
                        str(getattr(cfg, "session_definition", "") or "").strip() or None
                    ),
                )
            )

    def get_price(self, model_id: str) -> float | None:
        return self._pricing.get(model_id)

    def list_models(self) -> list[Model]:
        return list(self._models)
