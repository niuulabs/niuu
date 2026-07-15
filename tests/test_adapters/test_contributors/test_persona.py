"""Tests for PersonaContributor."""

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.persona import PersonaContributor
from volundr.domain.models import GitSource, Principal, Session
from volundr.domain.ports import SessionContext, SessionPersona, SessionPersonaProvider


def _session() -> Session:
    return Session(name="persona-session", model="gpt", source=GitSource())


async def test_persona_contributor_applies_selected_user_persona() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = SessionPersona(name="reviewer", system_prompt="Review carefully")
    contributor = PersonaContributor(persona_provider=provider)
    principal = Principal(
        user_id="user-1",
        email="user@example.com",
        tenant_id="tenant-1",
        roles=[],
    )

    result = await contributor.contribute(
        _session(),
        SessionContext(principal=principal, workload_config={"persona": "reviewer"}),
    )

    provider.get.assert_awaited_once_with("user-1", "reviewer")
    assert result.values == {"session": {"systemPrompt": "Review carefully"}}


async def test_persona_contributor_ignores_launch_without_persona() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    contributor = PersonaContributor(persona_provider=provider)

    result = await contributor.contribute(_session(), SessionContext())

    assert result.values == {}
    provider.get.assert_not_awaited()


async def test_persona_contributor_rejects_unknown_persona() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = None
    contributor = PersonaContributor(persona_provider=provider)

    with pytest.raises(ValueError, match="Persona not found: missing"):
        await contributor.contribute(
            _session(),
            SessionContext(workload_config={"persona": "missing"}),
        )
