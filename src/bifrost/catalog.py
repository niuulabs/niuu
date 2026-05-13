"""Helpers for projecting Bifrost config into the shared model catalog."""

from __future__ import annotations

from bifrost.config import BifrostConfig
from niuu.domain.model_catalog import (
    ManagedModel,
    ManagedModelAlias,
    ManagedModelProvider,
    ManagedModelTier,
    ManagedProvider,
    ProviderHealthState,
)


def _infer_provider_kind(provider_key: str, model_id: str) -> ManagedModelProvider:
    normalized_provider = str(provider_key or "").strip().lower()
    normalized_model = str(model_id or "").strip().lower()
    if normalized_provider in {"local", "ollama"} or ":" in normalized_model:
        return ManagedModelProvider.LOCAL
    return ManagedModelProvider.CLOUD


def _model_vendor(config: BifrostConfig, provider_key: str, model_id: str) -> str:
    model_entry = config.model_entry(model_id)
    if model_entry is not None and model_entry.vendor:
        return model_entry.vendor
    normalized_provider = str(provider_key or "").strip().lower()
    if normalized_provider == "ollama":
        return "local"
    return normalized_provider


def list_models(config: BifrostConfig) -> list[ManagedModel]:
    """Return the canonical Bifrost-owned model catalog."""
    aliases_by_model: dict[str, list[str]] = {}
    for alias, target in config.aliases.items():
        if alias == target:
            continue
        aliases_by_model.setdefault(target, []).append(alias)

    provider_keys_by_model: dict[str, list[str]] = {}
    for provider_key, provider in config.providers.items():
        for model_id in provider.models:
            canonical = config.resolve_alias(model_id)
            provider_keys_by_model.setdefault(canonical, []).append(provider_key)

    configured_by_id = {model.id: model for model in config.models}
    entries: list[ManagedModel] = []
    seen_model_ids: set[str] = set()

    for model in config.models:
        provider_keys = provider_keys_by_model.get(model.id, [])
        entries.append(
            ManagedModel(
                id=model.id,
                name=model.name,
                vendor=model.vendor,
                provider=model.provider,
                tier=model.tier,
                color=model.color,
                description=model.description,
                cost_per_million_tokens=model.cost_per_million_tokens,
                vram_required=model.vram_required,
                session_definition=model.session_definition,
                supports_tools=model.supports_tools,
                supports_thinking=model.supports_thinking,
                enabled=model.enabled,
                aliases=tuple(sorted(aliases_by_model.get(model.id, []))),
                provider_keys=tuple(provider_keys),
            )
        )
        seen_model_ids.add(model.id)

    for model_id, provider_keys in provider_keys_by_model.items():
        if model_id in seen_model_ids:
            continue
        model = configured_by_id.get(model_id)
        if model is None:
            model = config.model_entry(model_id)
        if model is None:
            primary_provider = provider_keys[0] if provider_keys else ""
            model = type(
                "_SyntheticModelConfig",
                (),
                {
                    "id": model_id,
                    "name": model_id,
                    "vendor": _model_vendor(config, primary_provider, model_id),
                    "provider": _infer_provider_kind(primary_provider, model_id),
                    "tier": ManagedModelTier.BALANCED,
                    "color": "#2563EB",
                    "description": "",
                    "cost_per_million_tokens": None,
                    "vram_required": None,
                    "session_definition": None,
                    "supports_tools": True,
                    "supports_thinking": True,
                    "enabled": True,
                },
            )()
        entries.append(
            ManagedModel(
                id=model.id,
                name=model.name,
                vendor=model.vendor,
                provider=model.provider,
                tier=model.tier,
                color=model.color,
                description=model.description,
                cost_per_million_tokens=model.cost_per_million_tokens,
                vram_required=model.vram_required,
                session_definition=model.session_definition,
                supports_tools=model.supports_tools,
                supports_thinking=model.supports_thinking,
                enabled=model.enabled,
                aliases=tuple(sorted(aliases_by_model.get(model.id, []))),
                provider_keys=tuple(provider_keys),
            )
        )
    return entries


def get_model(config: BifrostConfig, model_id: str) -> ManagedModel | None:
    """Return one model entry by canonical ID or alias."""
    canonical = config.resolve_alias(model_id)
    for entry in list_models(config):
        if entry.id == canonical:
            return entry
    return None


def list_aliases(config: BifrostConfig) -> list[ManagedModelAlias]:
    """Return alias mappings in stable order."""
    return [
        ManagedModelAlias(alias=alias, target=target)
        for alias, target in sorted(config.aliases.items())
        if alias != target
    ]


def list_providers(
    config: BifrostConfig,
    *,
    health_states: dict[str, ProviderHealthState] | None = None,
    health_details: dict[str, str] | None = None,
) -> list[ManagedProvider]:
    """Return provider entries with optional observed health data."""
    states = health_states or {}
    details = health_details or {}
    model_entries = list_models(config)

    if not config.providers:
        models_by_vendor: dict[str, list[str]] = {}
        for model in model_entries:
            if not model.vendor:
                continue
            models_by_vendor.setdefault(model.vendor, []).append(model.id)
        return [
            ManagedProvider(
                key=vendor,
                vendor=vendor,
                base_url="",
                model_ids=tuple(sorted(model_ids)),
                timeout_seconds=120.0,
                cost_per_token=0.0,
                state=states.get(vendor, ProviderHealthState.UNKNOWN),
                detail=details.get(vendor, ""),
            )
            for vendor, model_ids in sorted(models_by_vendor.items())
        ]

    provider_entries: list[ManagedProvider] = []
    for key, provider in config.providers.items():
        vendor = key
        for model_id in provider.models:
            model_entry = next((entry for entry in model_entries if entry.id == model_id), None)
            if model_entry is not None and model_entry.vendor:
                vendor = model_entry.vendor
                break
        provider_entries.append(
            ManagedProvider(
                key=key,
                vendor=vendor,
                base_url=config.effective_base_url(key),
                model_ids=tuple(provider.models),
                timeout_seconds=provider.timeout,
                cost_per_token=provider.cost_per_token,
                state=states.get(key, ProviderHealthState.UNKNOWN),
                detail=details.get(key, ""),
            )
        )
    return provider_entries
