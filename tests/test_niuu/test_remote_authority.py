"""Central authority changes are observed without positive caching."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import respx

from identity.adapters.remote import RemoteHeaderAuthenticationAdapter
from identity.models import Principal
from identity.ports import AuthorizationDeniedError, AuthorizationEvaluationError
from niuu.adapters.remote_pats import RemotePATService, RemotePATValidator
from niuu.ports.identity import InvalidTokenError

URL = "https://identity.test"
IDENTITY = {
    "user_id": "alice",
    "tenant_id": "acme",
    "email": "a@test",
    "roles": ["volundr:developer"],
    "status": "active",
}
HEADERS = {
    "authorization": "Bearer credential",
    "x-auth-user-id": "alice",
    "x-auth-tenant": "acme",
    "x-auth-roles": "volundr:admin",
}


@respx.mock
async def test_membership_change_and_revocation_are_immediate():
    route = respx.get(URL + "/api/v1/identity/me").mock(
        side_effect=[
            httpx.Response(200, json=IDENTITY),
            httpx.Response(200, json={**IDENTITY, "roles": ["volundr:viewer"]}),
            httpx.Response(401),
        ]
    )
    adapter = RemoteHeaderAuthenticationAdapter(authority_url=URL)
    assert (await adapter.validate_headers(HEADERS)).roles == ["volundr:developer"]
    assert (await adapter.validate_headers(HEADERS)).roles == ["volundr:viewer"]
    with pytest.raises(InvalidTokenError):
        await adapter.validate_headers(HEADERS)
    assert route.call_count == 3
    assert "x-auth-roles" not in route.calls[0].request.headers


@respx.mock
@pytest.mark.parametrize(
    "body",
    [
        {**IDENTITY, "status": "suspended"},
        {**IDENTITY, "tenant_id": "other"},
        {**IDENTITY, "user_id": "bob"},
    ],
)
async def test_rejects_inactive_or_mismatched_identity(body):
    respx.get(URL + "/api/v1/identity/me").respond(200, json=body)
    with pytest.raises(InvalidTokenError):
        await RemoteHeaderAuthenticationAdapter(authority_url=URL).validate_headers(HEADERS)


@respx.mock
@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, text="not json"), httpx.Response(200, json={})],
)
async def test_authority_failure_never_grants_access(response):
    respx.get(URL + "/api/v1/identity/me").mock(return_value=response)
    with pytest.raises(AuthorizationEvaluationError):
        await RemoteHeaderAuthenticationAdapter(authority_url=URL).validate_headers(HEADERS)


@respx.mock
async def test_pat_validator_rechecks_central_authority():
    respx.get(URL + "/api/v1/identity/me").mock(
        side_effect=[
            httpx.Response(200, json=IDENTITY),
            httpx.Response(401),
            httpx.ConnectError("offline"),
        ]
    )
    validator = RemotePATValidator(authority_url=URL, repo=AsyncMock())
    assert await validator.is_valid("pat")
    assert not await validator.is_valid("pat")
    with pytest.raises(AuthorizationEvaluationError):
        await validator.is_valid("pat")


@respx.mock
async def test_pat_creation_forwards_scopes_and_checks_ownership():
    principal = Principal("alice", "a@test", "acme", ["volundr:developer"])
    body = {
        "id": str(uuid4()),
        "owner_id": "alice",
        "tenant_id": "acme",
        "name": "build",
        "created_at": datetime.now(UTC).isoformat(),
        "scopes": ["forge:session:create"],
        "token": "signed-token",
    }
    route = respx.post(URL + "/api/v1/tokens").respond(201, json=body)
    service = RemotePATService(authority_url=URL)
    metadata, token = await service.create(
        principal, "build", subject_token="browser", scopes=["forge:session:create"]
    )
    assert token == "signed-token"
    assert metadata.scopes == ("forge:session:create",)
    assert route.calls[0].request.headers["authorization"] == "Bearer browser"
    route.respond(201, json={**body, "tenant_id": "other"})
    with pytest.raises(AuthorizationDeniedError):
        await service.create(principal, "build", subject_token="browser")


@pytest.mark.parametrize("adapter", [RemotePATService, RemoteHeaderAuthenticationAdapter])
def test_requires_tls(adapter):
    with pytest.raises(ValueError):
        adapter(authority_url="http://identity.test")
