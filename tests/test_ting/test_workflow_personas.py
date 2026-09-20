"""Authoring uses owner-scoped remote personas without local substitution."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from niuu.domain.models import Principal
from ravn.domain.persona_document import PortablePersonaDefinition
from ting.api.workflow_personas import authoring_persona_source


def request_for(state):
    return Request(
        {
            "type": "http",
            "headers": [],
            "query_string": b"",
            "app": SimpleNamespace(state=state),
        }
    )


def principal():
    return Principal(user_id="alice", tenant_id="tenant-a", email="", roles=[])


async def test_authoring_fetches_only_required_personas_as_caller():
    doc = PortablePersonaDefinition(id="custom", revision="r1", definition={"name": "custom"})
    adapter = SimpleNamespace(get_current_portable_persona=AsyncMock(return_value=doc))
    factory = SimpleNamespace(primary_for_principal=AsyncMock(return_value=adapter))
    caller = principal()
    source = await authoring_persona_source(
        request_for(SimpleNamespace(volundr_factory=factory)),
        caller,
        {"custom"},
    )
    factory.primary_for_principal.assert_awaited_once_with(caller)
    adapter.get_current_portable_persona.assert_awaited_once_with(
        "custom",
        auth_token=None,
        principal=caller,
    )
    assert source.load_portable("custom", "r1") == doc
    assert source.load_current_portable("custom") == doc


async def test_complete_scoped_bundle_needs_no_registry_connection():
    factory = SimpleNamespace(primary_for_principal=AsyncMock())
    source = await authoring_persona_source(
        request_for(SimpleNamespace(volundr_factory=factory)),
        principal(),
        set(),
    )
    factory.primary_for_principal.assert_not_called()
    assert source.load_current_portable("missing") is None


async def test_explicit_local_composition_uses_local_source():
    local = object()
    assert (
        await authoring_persona_source(
            request_for(SimpleNamespace(persona_source=local)),
            principal(),
            {"custom"},
        )
        is local
    )


async def test_missing_remote_connection_is_not_replaced_with_local_persona():
    factory = SimpleNamespace(primary_for_principal=AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await authoring_persona_source(
            request_for(SimpleNamespace(volundr_factory=factory, persona_source=object())),
            principal(),
            {"custom"},
        )
    assert exc.value.status_code == 503


@pytest.mark.parametrize("status, expected", [(403, 403), (422, 422), (500, 502)])
async def test_remote_status_errors_are_explicit(status, expected):
    req = httpx.Request("GET", "https://registry.invalid/personas/custom/portable")
    response = httpx.Response(status, request=req)
    error = httpx.HTTPStatusError("Failed", request=req, response=response)
    adapter = SimpleNamespace(get_current_portable_persona=AsyncMock(side_effect=error))
    factory = SimpleNamespace(primary_for_principal=AsyncMock(return_value=adapter))
    with pytest.raises(HTTPException) as exc:
        await authoring_persona_source(
            request_for(SimpleNamespace(volundr_factory=factory)), principal(), {"custom"}
        )
    assert exc.value.status_code == expected


async def test_remote_transport_error_fails_explicitly():
    adapter = SimpleNamespace(
        get_current_portable_persona=AsyncMock(side_effect=httpx.ConnectError("down"))
    )
    factory = SimpleNamespace(primary_for_principal=AsyncMock(return_value=adapter))
    with pytest.raises(HTTPException) as exc:
        await authoring_persona_source(
            request_for(SimpleNamespace(volundr_factory=factory)), principal(), {"custom"}
        )
    assert exc.value.status_code == 502
