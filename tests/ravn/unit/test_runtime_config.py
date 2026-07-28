"""Tests for shared CLI runtime configuration resolution."""

from ravn.adapters.personas.loader import PersonaConfig, PersonaLLMConfig
from ravn.cli.runtime_config import _resolve_extended_thinking
from ravn.config import Settings


def test_persona_can_enable_reasoning() -> None:
    settings = Settings()
    persona = PersonaConfig(
        name="resident",
        llm=PersonaLLMConfig(thinking_enabled=True),
    )

    resolved = _resolve_extended_thinking(
        settings,
        persona,
        cli_transport_executor=False,
    )

    assert resolved is not None
    assert resolved.enabled is True
    assert resolved.budget_tokens == settings.llm.extended_thinking.budget_tokens


def test_global_reasoning_remains_enabled_for_persona_without_override() -> None:
    settings = Settings()
    settings.llm.extended_thinking.enabled = True

    resolved = _resolve_extended_thinking(
        settings,
        PersonaConfig(name="resident"),
        cli_transport_executor=False,
    )

    assert resolved is not None
    assert resolved.enabled is True


def test_reasoning_is_absent_when_not_enabled() -> None:
    resolved = _resolve_extended_thinking(
        Settings(),
        PersonaConfig(name="resident"),
        cli_transport_executor=False,
    )

    assert resolved is None


def test_cli_transport_owns_its_reasoning_contract() -> None:
    settings = Settings()
    settings.llm.extended_thinking.enabled = True

    resolved = _resolve_extended_thinking(
        settings,
        PersonaConfig(name="resident"),
        cli_transport_executor=True,
    )

    assert resolved is None
