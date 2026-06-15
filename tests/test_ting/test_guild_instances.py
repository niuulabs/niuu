"""Tests for Ting's Guild instance registry client."""

from __future__ import annotations

import httpx
import pytest
import respx
from starlette.requests import Request

from niuu.domain.models import Principal
from ting.adapters.guild_instances import GuildInstanceRegistryClient
from ting.adapters.inbound.auth import extract_bearer_token


@pytest.mark.asyncio
@respx.mock
async def test_list_volundr_targets_forwards_principal_and_bearer_token() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"authorization", b"Bearer request-jwt")],
        }
    )
    assert extract_bearer_token(request) == "request-jwt"

    route = respx.get("https://guild.test/api/v1/niuu/instances").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "volundr-1",
                    "kind": "volundr",
                    "slug": "volundr-1",
                    "name": "Volundr One",
                    "baseUrl": "https://volundr.test",
                    "visibility": "system",
                    "ownerId": None,
                    "tenantId": None,
                    "enabled": True,
                    "isDefault": True,
                    "config": {},
                    "tags": ["gpu"],
                    "createdAt": "2026-06-14T00:00:00+00:00",
                    "updatedAt": "2026-06-14T00:00:00+00:00",
                }
            ],
        )
    )
    client = GuildInstanceRegistryClient("https://guild.test")

    try:
        targets = await client.list_volundr_targets(
            Principal(
                user_id="user-1",
                email="user-1@example.com",
                tenant_id="tenant-a",
                roles=["volundr:developer"],
            )
        )
    finally:
        await client.close()

    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer request-jwt"
    assert sent.headers["x-auth-user-id"] == "user-1"
    assert sent.headers["x-auth-tenant"] == "tenant-a"
    assert sent.url.params["kind"] == "volundr"
    assert sent.url.params["enabledOnly"] == "true"
    assert targets[0].id == "volundr-1"
    assert targets[0].tags == ["gpu"]
