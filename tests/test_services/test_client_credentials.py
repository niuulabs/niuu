"""Client-credentials minting: the provider token endpoint is an explicitly mocked boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from volundr.domain.models import TeamInstallMethod, TeamInstallSpec
from volundr.domain.services.client_credentials import (
    ClientCredentialsError,
    ClientCredentialsMinter,
)

TOKEN_URL = "https://auth.atlassian.com/oauth/token"
SECRET = "svc-secret-value"

SPEC = TeamInstallSpec(
    method=TeamInstallMethod.CLIENT_CREDENTIALS,
    token_url=TOKEN_URL,
    token_params={"audience": "api.atlassian.com"},
    required_config=("cloud_id",),
)
VALUES = {"client_id": "svc-id", "client_secret": SECRET}


def _minter() -> ClientCredentialsMinter:
    return ClientCredentialsMinter(request_timeout=5.0)


@pytest.mark.asyncio
@respx.mock
async def test_mint_returns_the_values_plus_a_token_and_expiry() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
    )

    minted = await _minter().mint(SPEC, VALUES)

    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "grant_type": "client_credentials",
        "client_id": "svc-id",
        "client_secret": SECRET,
        "audience": "api.atlassian.com",
    }
    assert minted["client_id"] == "svc-id"
    assert minted["client_secret"] == SECRET
    assert minted["access_token"] == "tok"
    expires_at = datetime.fromisoformat(minted["expires_at"])
    assert (
        datetime.now(UTC) + timedelta(minutes=55)
        < expires_at
        < datetime.now(UTC) + timedelta(minutes=65)
    )
    assert "access_token" not in VALUES


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_without_leaking_the_secret() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            401, json={"error": "access_denied", "error_description": "bad"}
        )
    )

    with pytest.raises(ClientCredentialsError) as caught:
        await _minter().mint(SPEC, VALUES)

    assert "401" in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.asyncio
@respx.mock
async def test_response_without_access_token_raises() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"expires_in": 3600}))

    with pytest.raises(ClientCredentialsError, match="access_token"):
        await _minter().mint(SPEC, VALUES)


@pytest.mark.asyncio
@respx.mock
async def test_response_without_expiry_raises() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json={"access_token": "tok"}))

    with pytest.raises(ClientCredentialsError, match="expires_in"):
        await _minter().mint(SPEC, VALUES)


@pytest.mark.asyncio
@respx.mock
async def test_non_json_error_body_still_raises_typed() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(502, text="bad gateway"))

    with pytest.raises(ClientCredentialsError, match="502"):
        await _minter().mint(SPEC, VALUES)


@pytest.mark.asyncio
@respx.mock
async def test_unreachable_endpoint_raises_typed() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(ClientCredentialsError, match="could not be reached"):
        await _minter().mint(SPEC, VALUES)


@pytest.mark.asyncio
async def test_missing_client_secret_raises_before_any_request() -> None:
    with pytest.raises(ClientCredentialsError, match="client_secret"):
        await _minter().mint(SPEC, {"client_id": "svc-id"})


@pytest.mark.asyncio
async def test_wrong_method_and_missing_token_url_raise() -> None:
    oauth = TeamInstallSpec(method=TeamInstallMethod.OAUTH)
    with pytest.raises(ClientCredentialsError, match="does not use client credentials"):
        await _minter().mint(oauth, VALUES)
    no_url = TeamInstallSpec(method=TeamInstallMethod.CLIENT_CREDENTIALS)
    with pytest.raises(ClientCredentialsError, match="token_url"):
        await _minter().mint(no_url, VALUES)


@pytest.mark.asyncio
@respx.mock
async def test_injected_client_factory_is_used() -> None:
    route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_in": 60})
    )
    timeouts: list[float] = []

    def factory(timeout: float) -> httpx.AsyncClient:
        timeouts.append(timeout)
        return httpx.AsyncClient(timeout=timeout)

    await ClientCredentialsMinter(request_timeout=7.0, client_factory=factory).mint(SPEC, VALUES)

    assert timeouts == [7.0]
    assert route.called
