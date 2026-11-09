"""Tests for the Forge projection of the Ravn persona registry."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from niuu.domain.outcome import OutcomeField
from personas.domain.persona_document import PortablePersonaDefinition
from volundr.adapters.outbound.session_personas import RegistrySessionPersonaProvider


async def test_registry_session_persona_provider_projects_runtime_fields() -> None:
    document = PortablePersonaDefinition(
        id="reviewer",
        revision="content-0123456789abcdef",
        definition={
            "name": "reviewer",
            "system_prompt_template": "Review carefully",
        },
    )
    registry = SimpleNamespace(
        get_current_portable_persona=AsyncMock(return_value=document),
        get_persona=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    name="reviewer",
                    system_prompt_template="Review carefully",
                    consumes=SimpleNamespace(event_types=["code.changed"]),
                    produces=SimpleNamespace(
                        event_type="review.completed",
                        schema={
                            "summary": OutcomeField(
                                type="string",
                                description="review summary",
                            )
                        },
                    ),
                )
            )
        ),
    )
    provider = RegistrySessionPersonaProvider(registry)

    persona = await provider.get("user-1", "reviewer")

    registry.get_persona.assert_awaited_once_with("user-1", "reviewer")
    assert persona is not None
    assert persona.name == "reviewer"
    assert persona.system_prompt.startswith("Review carefully")
    assert "---outcome---" in persona.system_prompt
    assert persona.consumes_event_types == ("code.changed",)
    assert persona.produces_event_type == "review.completed"
    assert list(persona.produces_schema) == ["summary"]
    # The session plays the pinned source document, exactly as the registry holds it.
    registry.get_current_portable_persona.assert_awaited_once_with("user-1", "reviewer")
    assert persona.document == document.to_dict()
    assert persona.document["definition"]["system_prompt_template"] == "Review carefully"


async def test_registry_session_persona_provider_preserves_missing_result() -> None:
    registry = SimpleNamespace(get_persona=AsyncMock(return_value=None))
    provider = RegistrySessionPersonaProvider(registry)

    assert await provider.get("user-1", "missing") is None


async def test_registry_session_persona_provider_refuses_a_persona_it_cannot_pin() -> None:
    registry = SimpleNamespace(
        get_current_portable_persona=AsyncMock(return_value=None),
        get_persona=AsyncMock(
            return_value=SimpleNamespace(
                config=SimpleNamespace(
                    name="reviewer",
                    system_prompt_template="Review carefully",
                    consumes=SimpleNamespace(event_types=[]),
                    produces=SimpleNamespace(event_type="", schema={}),
                )
            )
        ),
    )

    with pytest.raises(ValueError, match="no portable document to pin"):
        await RegistrySessionPersonaProvider(registry).get("user-1", "reviewer")
