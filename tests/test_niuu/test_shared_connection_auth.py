"""Cross-cluster connection lookups retain the caller's authenticated identity."""

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
import respx
from starlette.requests import Request

from niuu.adapters.http_integrations import HTTPIntegrationRepository
from niuu.adapters.inbound.auth_context import extract_bearer_token


def caller(token):
    extract_bearer_token(
        Request(
            {
                "type": "http",
                "query_string": b"",
                "headers": [(b"authorization", f"Bearer {token}".encode())] if token else [],
            }
        )
    )


def repository():
    return HTTPIntegrationRepository(
        "https://connections.test",
        api_prefix="/api/v1/integrations",
        auth_adapter="niuu.adapters.outbound.http_auth.RequestBearerTokenAuthAdapter",
    )


@respx.mock
async def test_concurrent_launches_forward_only_their_own_bearer():
    observed = []

    def reply(request):
        observed.append(request.headers["authorization"])
        assert "x-auth-user-id" not in request.headers
        return httpx.Response(200, json=[])

    respx.get("https://connections.test/api/v1/integrations").mock(side_effect=reply)
    repo = repository()

    async def launch(token):
        caller(token)
        await asyncio.sleep(0)
        await repo.list_connections(token)

    await asyncio.gather(launch("user-one"), launch("user-two"))
    assert set(observed) == {"Bearer user-one", "Bearer user-two"}
    await repo.close()


@respx.mock
async def test_missing_auth_cannot_launch_with_anonymous_or_forged_headers():
    caller(None)
    repo = repository()
    with pytest.raises(RuntimeError, match="authenticated caller"):
        await repo.list_connections("owner")
    await repo.close()


@respx.mock
async def test_selected_connection_is_resolved_from_shared_api():
    caller("caller-token")
    now = datetime.now(UTC).isoformat()
    record = dict(
        id="claude",
        owner_id="owner",
        integration_type="ai_provider",
        adapter="",
        credential_name="claude-subscription",
        enabled=True,
        config={},
        slug="claude-code",
        created_at=now,
        updated_at=now,
    )
    route = respx.get("https://connections.test/api/v1/integrations/claude").mock(
        return_value=httpx.Response(200, json=record)
    )
    repo = repository()
    result = await repo.get_connection("claude")
    assert result.credential_name == "claude-subscription"
    assert result.owner_id == "owner"
    assert route.calls[0].request.headers["authorization"] == "Bearer caller-token"
    await repo.close()


@respx.mock
async def test_shared_lookup_cannot_relabel_another_users_connections():
    caller("caller-token")
    respx.get("https://connections.test/api/v1/integrations").mock(
        return_value=httpx.Response(200, json=[{"owner_id": "different-user"}])
    )
    repo = repository()
    with pytest.raises(PermissionError, match="owner"):
        await repo.list_connections("launching-user")
    await repo.close()
