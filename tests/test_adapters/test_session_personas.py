"""Tests for the Forge projection of the Ravn persona registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from volundr.adapters.outbound.session_personas import RegistrySessionPersonaProvider


async def test_registry_session_persona_provider_projects_runtime_fields() -> None:
    registry = SimpleNamespace(
        get_persona=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(name="reviewer", system_prompt_template="Review carefully")
            )
        )
    )
    provider = RegistrySessionPersonaProvider(registry)

    persona = await provider.get("user-1", "reviewer")

    registry.get_persona.assert_awaited_once_with("user-1", "reviewer")
    assert persona is not None
    assert persona.name == "reviewer"
    assert persona.system_prompt == "Review carefully"


async def test_registry_session_persona_provider_preserves_missing_result() -> None:
    registry = SimpleNamespace(get_persona=AsyncMock(return_value=None))
    provider = RegistrySessionPersonaProvider(registry)

    assert await provider.get("user-1", "missing") is None
