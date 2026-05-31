"""Tests for model/runtime resolution helpers."""

from __future__ import annotations

from types import SimpleNamespace

from bifrost.config import ManagedModelConfig
from niuu.domain.model_catalog import ManagedModelProvider, ManagedModelTier
from niuu.domain.model_runtime import (
    infer_model_vendor,
    normalize_model_vendor,
    resolve_session_definition_for_models,
    session_definition_for_model,
    transport_adapter_for_session_definition,
    validate_session_definition_for_models,
    vendor_for_model,
    vendors_for_models,
)


def _definition(
    *,
    enabled: bool = True,
    compatible_providers: list[str] | None = None,
    defaults: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        enabled=enabled,
        compatible_providers=compatible_providers or [],
        defaults=defaults or {},
    )


def _configured_models() -> list[ManagedModelConfig]:
    return [
        ManagedModelConfig(
            id="gpt-5.5",
            name="GPT-5.5",
            vendor="openai",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.FRONTIER,
            session_definition="skuldCodex",
        ),
        ManagedModelConfig(
            id="claude-sonnet-4-6",
            name="Claude Sonnet 4.6",
            vendor="anthropic",
            provider=ManagedModelProvider.CLOUD,
            tier=ManagedModelTier.BALANCED,
        ),
        ManagedModelConfig(
            id="llama3.2:latest",
            name="Llama 3.2",
            vendor="local",
            provider=ManagedModelProvider.LOCAL,
            tier=ManagedModelTier.EXECUTION,
        ),
    ]


def test_normalize_model_vendor_maps_aliases_and_unknowns() -> None:
    assert normalize_model_vendor("claude") == "anthropic"
    assert normalize_model_vendor("codex") == "openai"
    assert normalize_model_vendor("ollama") == "local"
    assert normalize_model_vendor("custom") == "custom"
    assert normalize_model_vendor(None) == ""


def test_infer_model_vendor_uses_model_id_shapes() -> None:
    assert infer_model_vendor("claude-sonnet-4-6") == "anthropic"
    assert infer_model_vendor("gpt-5.5") == "openai"
    assert infer_model_vendor("o3") == "openai"
    assert infer_model_vendor("llama3.2:latest") == "local"
    assert infer_model_vendor("mystery-model") == ""
    assert infer_model_vendor("") == ""


def test_vendor_for_model_prefers_configured_model_metadata() -> None:
    assert vendor_for_model("gpt-5.5", configured_models=_configured_models()) == "openai"
    assert vendor_for_model("llama3.2:latest", configured_models=_configured_models()) == "local"
    assert vendor_for_model("claude-opus-4-6") == "anthropic"
    assert vendor_for_model("") == ""


def test_session_definition_for_model_requires_explicit_model() -> None:
    definition, error = session_definition_for_model("", session_definitions={})
    assert definition is None
    assert error == "Workflow stages must declare an explicit model."


def test_session_definition_for_model_uses_explicit_model_override() -> None:
    definitions = {"skuldCodex": _definition(compatible_providers=["openai"])}
    definition, error = session_definition_for_model(
        "gpt-5.5",
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition == "skuldCodex"
    assert error is None


def test_session_definition_for_model_rejects_unknown_or_disabled_override() -> None:
    definitions = {"skuldCodex": _definition(enabled=False, compatible_providers=["openai"])}
    definition, error = session_definition_for_model(
        "gpt-5.5",
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition is None
    assert error == "Model 'gpt-5.5' references unknown or disabled runtime 'skuldCodex'."


def test_session_definition_for_model_falls_back_to_provider_match() -> None:
    definitions = {"skuldClaude": _definition(compatible_providers=["anthropic"])}
    definition, error = session_definition_for_model(
        "claude-sonnet-4-6",
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition == "skuldClaude"
    assert error is None


def test_session_definition_for_model_uses_provider_neutral_fallback() -> None:
    definitions = {
        "neutral": _definition(),
        "skuldClaude": _definition(compatible_providers=["anthropic"], enabled=False),
    }
    definition, error = session_definition_for_model(
        "claude-sonnet-4-6",
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition == "neutral"
    assert error is None


def test_session_definition_for_model_reports_unknown_vendor_or_no_match() -> None:
    definition, error = session_definition_for_model(
        "mystery-model",
        session_definitions={"skuldClaude": _definition(compatible_providers=["anthropic"])},
    )
    assert definition is None
    assert error == "Workflow model 'mystery-model' could not be mapped to a runtime provider."

    definition, error = session_definition_for_model(
        "claude-sonnet-4-6",
        session_definitions={"skuldCodex": _definition(compatible_providers=["openai"])},
        configured_models=_configured_models(),
    )
    assert definition is None
    assert error == "No enabled session definition accepts provider 'anthropic'."


def test_vendors_for_models_collects_known_vendors() -> None:
    vendors = vendors_for_models(
        ["gpt-5.5", "claude-sonnet-4-6", "mystery-model"],
        configured_models=_configured_models(),
    )
    assert vendors == {"openai", "anthropic"}


def test_resolve_session_definition_for_models_handles_success_and_errors() -> None:
    definitions = {
        "skuldClaude": _definition(compatible_providers=["anthropic"]),
        "skuldCodex": _definition(compatible_providers=["openai"]),
    }

    definition, error = resolve_session_definition_for_models(
        ["claude-sonnet-4-6"],
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition == "skuldClaude"
    assert error is None

    definition, error = resolve_session_definition_for_models(
        [],
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition is None
    assert error == "Workflow stages must declare an explicit model."

    definition, error = resolve_session_definition_for_models(
        ["mystery-model"],
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition is None
    assert error == "Workflow models could not be mapped to a runtime provider: mystery-model"

    definition, error = resolve_session_definition_for_models(
        ["gpt-5.5", "claude-sonnet-4-6"],
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition is None
    assert (
        error
        == "Workflow stages mix multiple model providers; use a single provider per workflow."
    )

    definition, error = resolve_session_definition_for_models(
        ["llama3.2:latest"],
        session_definitions=definitions,
        configured_models=_configured_models(),
    )
    assert definition is None
    assert error == "No enabled session definition accepts provider 'local'."


def test_validate_session_definition_for_models_covers_all_validation_branches() -> None:
    definitions = {
        "skuldClaude": _definition(compatible_providers=["anthropic"]),
        "neutral": _definition(),
        "brokenDefaults": _definition(defaults={"broker": {"transportAdapter": "adapter"}}),
    }

    assert (
        validate_session_definition_for_models(
            None,
            ["claude-sonnet-4-6"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == "Workflow must declare an explicit session definition/runtime."
    )

    assert (
        validate_session_definition_for_models(
            "missing",
            ["claude-sonnet-4-6"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == "Workflow references unknown or disabled session definition 'missing'."
    )

    assert (
        validate_session_definition_for_models(
            "skuldClaude",
            [],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == "Workflow stages must declare an explicit model."
    )

    assert (
        validate_session_definition_for_models(
            "skuldClaude",
            ["mystery-model"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == "Workflow models could not be mapped to a runtime provider: mystery-model"
    )

    assert (
        validate_session_definition_for_models(
            "skuldClaude",
            ["gpt-5.5", "claude-sonnet-4-6"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == "Workflow stages mix multiple model providers; use a single provider per workflow."
    )

    assert (
        validate_session_definition_for_models(
            "neutral",
            ["gpt-5.5"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        is None
    )

    assert (
        validate_session_definition_for_models(
            "skuldClaude",
            ["claude-sonnet-4-6"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        is None
    )

    assert (
        validate_session_definition_for_models(
            "skuldClaude",
            ["gpt-5.5"],
            session_definitions=definitions,
            configured_models=_configured_models(),
        )
        == (
            "Session definition 'skuldClaude' does not accept provider 'openai'. "
            "Compatible providers: anthropic"
        )
    )


def test_transport_adapter_for_session_definition_handles_missing_shapes() -> None:
    definitions = {
        "good": _definition(defaults={"broker": {"transportAdapter": "skuld.transport.Adapter"}}),
        "badDefaults": _definition(defaults=[]),
        "badBroker": _definition(defaults={"broker": []}),
    }
    assert transport_adapter_for_session_definition(None, session_definitions=definitions) == ""
    assert (
        transport_adapter_for_session_definition("missing", session_definitions=definitions) == ""
    )
    assert (
        transport_adapter_for_session_definition("badDefaults", session_definitions=definitions)
        == ""
    )
    assert (
        transport_adapter_for_session_definition("badBroker", session_definitions=definitions)
        == ""
    )
    assert (
        transport_adapter_for_session_definition("good", session_definitions=definitions)
        == "skuld.transport.Adapter"
    )
