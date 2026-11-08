"""Tests for PersonaContributor."""

from unittest.mock import AsyncMock

import pytest

from volundr.adapters.outbound.contributors.persona import PersonaContributor
from volundr.domain.models import GitSource, Principal, Session
from volundr.domain.ports import SessionContext, SessionPersona, SessionPersonaProvider


def _session() -> Session:
    return Session(name="persona-session", model="gpt", source=GitSource())


def _document(name: str = "reviewer") -> dict:
    return {
        "schema_version": 1,
        "id": name,
        "revision": "content-0123456789abcdef",
        "definition": {
            "name": name,
            "system_prompt_template": "Review carefully",
            "allowed_tools": ["file_read", "git_read"],
            "permission_mode": "read_only",
        },
    }


async def test_persona_contributor_applies_selected_user_persona() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = SessionPersona(
        name="reviewer", system_prompt="Review carefully", document=_document()
    )
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
    # The pinned document is delivered whole; the prompt string is not sent in its place.
    assert result.values == {"session": {"persona": _document()}}


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


async def test_persona_contributor_refuses_a_persona_without_a_pinned_document() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = SessionPersona(name="reviewer", system_prompt="Review carefully")
    contributor = PersonaContributor(persona_provider=provider)

    with pytest.raises(ValueError, match="carries no pinned document"):
        await contributor.contribute(
            _session(),
            SessionContext(workload_config={"persona": "reviewer"}),
        )


async def test_a_request_prompt_is_delivered_beside_the_persona_not_over_it() -> None:
    """The persona is the base; Skuld appends the request's prompt after its instructions."""
    from volundr.adapters.outbound.contributors.prompt import PromptContributor

    provider = AsyncMock(spec=SessionPersonaProvider)
    provider.get.return_value = SessionPersona(
        name="reviewer", system_prompt="Review carefully", document=_document()
    )
    context = SessionContext(
        workload_config={"persona": "reviewer"}, system_prompt="Focus on the migrations."
    )

    persona = await PersonaContributor(persona_provider=provider).contribute(_session(), context)
    prompt = await PromptContributor().contribute(_session(), context)

    assert persona.values["session"] == {"persona": _document()}
    assert prompt.values["session"] == {"systemPrompt": "Focus on the migrations."}


async def test_a_pinned_document_is_played_verbatim_without_the_catalog() -> None:
    provider = AsyncMock(spec=SessionPersonaProvider)
    contributor = PersonaContributor(persona_provider=provider)

    result = await contributor.contribute(
        _session(), SessionContext(workload_config={"persona_definition": _document("coder")})
    )

    assert result.values == {"session": {"persona": _document("coder")}}
    provider.get.assert_not_awaited()


async def test_a_pinned_document_needs_no_catalog_at_all() -> None:
    result = await PersonaContributor().contribute(
        _session(), SessionContext(workload_config={"persona_definition": _document("coder")})
    )

    assert result.values == {"session": {"persona": _document("coder")}}


async def test_a_name_beside_a_pinned_document_is_refused() -> None:
    contributor = PersonaContributor(persona_provider=AsyncMock(spec=SessionPersonaProvider))

    with pytest.raises(ValueError, match="names a persona and also carries"):
        await contributor.contribute(
            _session(),
            SessionContext(
                workload_config={"persona": "coder", "persona_definition": _document("coder")}
            ),
        )


async def test_a_name_without_a_catalog_is_refused_with_the_remedy() -> None:
    with pytest.raises(ValueError, match="no persona catalog.*persona_definition"):
        await PersonaContributor().contribute(
            _session(), SessionContext(workload_config={"persona": "coder"})
        )
