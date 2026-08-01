"""Tests for configured Codex authentication providers."""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from skuld.codex_auth import (
    CodexAuthProviderError,
    HostCodexAuthProvider,
    VolundrCodexAuthProvider,
)


@pytest.mark.asyncio
async def test_host_provider_leaves_existing_codex_auth_untouched() -> None:
    assert await HostCodexAuthProvider().get_tokens() is None
    assert await HostCodexAuthProvider().get_tokens(force_refresh=True) is None


@pytest.mark.asyncio
async def test_volundr_provider_uses_current_token_then_requests_one_rotation() -> None:
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        access_token = "access-token-2" if body["force_refresh"] else "access-token-1"
        return httpx.Response(
            200,
            json={
                "access_token": access_token,
                "chatgpt_account_id": "account-1",
                "chatgpt_plan_type": "pro",
                "expires_in": 3600,
            },
        )

    async with httpx.AsyncClient(
        base_url="https://volundr.internal",
        transport=httpx.MockTransport(handler),
    ) as client:
        provider = VolundrCodexAuthProvider(
            http_client_provider=lambda: _return(client),
            credential_name="codex-user",
            credential_field="auth.json",
        )
        current = await provider.get_tokens()
        refreshed = await provider.get_tokens(force_refresh=True)

    assert current.access_token == "access-token-1"
    assert refreshed.access_token == "access-token-2"
    assert refreshed.account_id == "account-1"
    assert refreshed.plan_type == "pro"
    assert requests == [
        {
            "credential_name": "codex-user",
            "credential_field": "auth.json",
            "force_refresh": False,
            "previous_access_token_sha256": "",
        },
        {
            "credential_name": "codex-user",
            "credential_field": "auth.json",
            "force_refresh": True,
            "previous_access_token_sha256": hashlib.sha256(b"access-token-1").hexdigest(),
        },
    ]


@pytest.mark.asyncio
async def test_volundr_provider_surfaces_reconnect_failure() -> None:
    async with httpx.AsyncClient(
        base_url="https://volundr.internal",
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={"detail": "Codex authentication requires reconnection"},
            )
        ),
    ) as client:
        provider = VolundrCodexAuthProvider(http_client_provider=lambda: _return(client))

        with pytest.raises(CodexAuthProviderError, match="requires reconnection"):
            await provider.get_tokens()


async def _return(client: httpx.AsyncClient) -> httpx.AsyncClient:
    return client
