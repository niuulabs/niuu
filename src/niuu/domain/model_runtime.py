"""Helpers for resolving workflow/runtime execution from explicit model IDs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from niuu.config_models import SessionDefinitionConfig


def _model_field(model_config: Any | None, field_name: str) -> str:
    return str(getattr(model_config, field_name, "") or "").strip()


def _find_model_config(
    model_id: str,
    *,
    configured_models: Iterable[Any] | None = None,
) -> Any | None:
    normalized_model = str(model_id or "").strip()
    if not normalized_model:
        return None
    for config in configured_models or []:
        if _model_field(config, "id") == normalized_model:
            return config
    return None


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
    configured_models: Iterable[Any] | None = None,
) -> str:
    normalized_model = str(model_id or "").strip()
    if not normalized_model:
        return ""

    config = _find_model_config(normalized_model, configured_models=configured_models)
    if config is not None:
        vendor = normalize_model_vendor(
            _model_field(config, "vendor") or _model_field(config, "provider")
        )
        if vendor:
            return vendor

    return infer_model_vendor(normalized_model)


def session_definition_for_model(
    model_id: str,
    *,
    session_definitions: Mapping[str, SessionDefinitionConfig],
    configured_models: Iterable[Any] | None = None,
) -> tuple[str | None, str | None]:
    normalized_model = str(model_id or "").strip()
    if not normalized_model:
        return None, "Workflow stages must declare an explicit model."

    config = _find_model_config(normalized_model, configured_models=configured_models)
    model_override = _model_field(config, "session_definition")
    if model_override:
        definition = session_definitions.get(model_override)
        if definition is None or not getattr(definition, "enabled", True):
            return (
                None,
                f"Model '{normalized_model}' references unknown or disabled runtime "
                f"'{model_override}'.",
            )
        return model_override, None

    vendor = vendor_for_model(normalized_model, configured_models=configured_models)
    if not vendor:
        return (
            None,
            f"Workflow model '{normalized_model}' could not be mapped to a runtime provider.",
        )

    provider_neutral_match: str | None = None
    for key, definition in session_definitions.items():
        if not getattr(definition, "enabled", True):
            continue
        compatible = [
            normalize_model_vendor(entry)
            for entry in getattr(definition, "compatible_providers", []) or []
        ]
        if compatible and vendor in compatible:
            return key, None
        if not compatible and provider_neutral_match is None:
            provider_neutral_match = key

    if provider_neutral_match:
        return provider_neutral_match, None

    return None, f"No enabled session definition accepts provider '{vendor}'."


def vendors_for_models(
    model_ids: Iterable[str],
    *,
    configured_models: Iterable[Any] | None = None,
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
    configured_models: Iterable[Any] | None = None,
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


def validate_session_definition_for_models(
    definition_key: str | None,
    model_ids: Iterable[str],
    *,
    session_definitions: Mapping[str, SessionDefinitionConfig],
    configured_models: Iterable[Any] | None = None,
) -> str | None:
    if not definition_key:
        return "Workflow must declare an explicit session definition/runtime."

    definition = session_definitions.get(definition_key)
    if definition is None or not getattr(definition, "enabled", True):
        return f"Workflow references unknown or disabled session definition '{definition_key}'."

    models = [str(model_id).strip() for model_id in model_ids if str(model_id).strip()]
    if not models:
        return "Workflow stages must declare an explicit model."

    vendors = vendors_for_models(models, configured_models=configured_models)
    if not vendors:
        unknown = ", ".join(sorted(set(models)))
        return f"Workflow models could not be mapped to a runtime provider: {unknown}"

    if len(vendors) > 1:
        return "Workflow stages mix multiple model providers; use a single provider per workflow."

    compatible = {
        normalize_model_vendor(entry)
        for entry in getattr(definition, "compatible_providers", []) or []
    }
    if not compatible:
        return None

    vendor = next(iter(vendors))
    if vendor in compatible:
        return None

    return (
        f"Session definition '{definition_key}' does not accept provider '{vendor}'. "
        f"Compatible providers: {', '.join(sorted(compatible))}"
    )


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
