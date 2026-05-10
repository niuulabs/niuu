"""Helpers for resolving workflow/runtime execution from explicit model IDs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from volundr.config import AIModelConfig, SessionDefinitionConfig


_DEFAULT_SESSION_DEFINITION_BY_VENDOR = {
    "anthropic": "skuldClaude",
    "openai": "skuldCodex",
    "local": "skuldOpenCode",
}

_DEFAULT_TRANSPORT_ADAPTER_BY_VENDOR = {
    "anthropic": "skuld.transports.persistent_subprocess.PersistentSubprocessTransport",
    "openai": "skuld.transports.codex_ws.CodexWebSocketTransport",
}


def normalize_model_vendor(value: str | None) -> str:
    vendor = str(value or "").strip().lower()
    aliases = {
        "anthropic": "anthropic",
        "claude": "anthropic",
        "openai": "openai",
        "codex": "openai",
        "ollama": "local",
        "local": "local",
    }
    return aliases.get(vendor, vendor)


def infer_model_vendor(model_id: str) -> str:
    normalized = str(model_id or "").strip().lower()
    if not normalized:
        return ""
    if normalized.startswith("claude-"):
        return "anthropic"
    if normalized.startswith(("gpt-", "o1", "o3", "o4", "codex")):
        return "openai"
    if ":" in normalized:
        return "local"
    return ""


def vendor_for_model(
    model_id: str,
    *,
    configured_models: Iterable[AIModelConfig] | None = None,
) -> str:
    normalized_model = str(model_id or "").strip()
    if not normalized_model:
        return ""

    for config in configured_models or []:
        if str(getattr(config, "id", "")).strip() != normalized_model:
            continue
        vendor = normalize_model_vendor(getattr(config, "provider", ""))
        if vendor:
            return vendor

    return infer_model_vendor(normalized_model)


def vendors_for_models(
    model_ids: Iterable[str],
    *,
    configured_models: Iterable[AIModelConfig] | None = None,
) -> set[str]:
    vendors: set[str] = set()
    for model_id in model_ids:
        vendor = vendor_for_model(model_id, configured_models=configured_models)
        if vendor:
            vendors.add(vendor)
    return vendors


def resolve_session_definition_for_models(
    model_ids: Iterable[str],
    *,
    session_definitions: Mapping[str, SessionDefinitionConfig],
    configured_models: Iterable[AIModelConfig] | None = None,
) -> tuple[str | None, str | None]:
    models = [str(model_id).strip() for model_id in model_ids if str(model_id).strip()]
    if not models:
        return None, "Workflow stages must declare an explicit model."

    vendors = vendors_for_models(models, configured_models=configured_models)
    if not vendors:
        unknown = ", ".join(sorted(set(models)))
        return None, f"Workflow models could not be mapped to a runtime provider: {unknown}"

    if len(vendors) > 1:
        return (
            None,
            "Workflow stages mix multiple model providers; use a single provider per workflow.",
        )

    vendor = next(iter(vendors))
    for key, definition in session_definitions.items():
        if not getattr(definition, "enabled", True):
            continue
        compatible = {
            normalize_model_vendor(entry)
            for entry in getattr(definition, "compatible_providers", []) or []
        }
        if not compatible:
            continue
        if vendor in compatible:
            return key, None

    return None, f"No enabled session definition accepts provider '{vendor}'."


def transport_adapter_for_session_definition(
    definition_key: str | None,
    *,
    session_definitions: Mapping[str, SessionDefinitionConfig],
) -> str:
    if not definition_key:
        return ""

    definition = session_definitions.get(definition_key)
    if definition is None:
        return ""

    defaults = getattr(definition, "defaults", {}) or {}
    if not isinstance(defaults, dict):
        return ""

    broker = defaults.get("broker")
    if not isinstance(broker, dict):
        return ""

    return str(broker.get("transportAdapter") or "").strip()


def default_session_definition_for_vendor(vendor: str | None) -> str | None:
    return _DEFAULT_SESSION_DEFINITION_BY_VENDOR.get(normalize_model_vendor(vendor))


def default_transport_adapter_for_vendor(vendor: str | None) -> str:
    return _DEFAULT_TRANSPORT_ADAPTER_BY_VENDOR.get(normalize_model_vendor(vendor), "")
