"""Völundr Codex token broker client: caching, refresh, and loud failures."""

from __future__ import annotations

import asyncio
import hashlib
import json

import httpx
import pytest
import respx

from bifrost.adapters.codex_token_broker import (
    CodexCredentials,
    CodexSubscriptionError,
    CodexTokenBroker,
)
from bifrost.ports.provider import ProviderError

BROKER_URL = "http://volundr.test"
TOKEN_URL = f"{BROKER_URL}/api/v1/internal/credentials/codex/tokens"
SKEW = 60.0


class FakeAuth:
    """An HttpAuthPort whose rejection can be re-minted (or not)."""

    def __init__(self, *, headers: dict[str, str] | None = None, can_invalidate: bool = False):
        self._headers = {"Authorization": "Bearer wl-1"} if headers is None else headers
        self._can_invalidate = can_invalidate
        self.invalidations = 0

    def headers(self) -> dict[str, str]:
        return dict(self._headers)

    def invalidate(self) -> bool:
        self.invalidations += 1
        self._headers = {"Authorization": "Bearer wl-2"}
        return self._can_invalidate


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _tokens(access: str = "tok-1", *, expires_in: int = 1300) -> dict:
    return {
        "access_token": access,
        "chatgpt_account_id": "acct-1",
        "expires_in": expires_in,
        "chatgpt_plan_type": "pro",
    }


def _broker(auth: FakeAuth | None = None, clock: Clock | None = None) -> CodexTokenBroker:
    return CodexTokenBroker(
        base_url=BROKER_URL + "/",
        auth=auth or FakeAuth(),
        credential_name="codex-credentials",
        credential_field="auth.json",
        token_path="/api/v1/internal/credentials/codex/tokens",
        timeout=5.0,
        refresh_skew_seconds=SKEW,
        clock=clock or Clock(),
    )


def _body(call: respx.models.Call) -> dict:
    return json.loads(call.request.content)


async def test_fetches_with_the_configured_credential_and_auth(respx_mock):
    route = respx_mock.post(TOKEN_URL).respond(200, json=_tokens())

    credentials = await _broker().credentials()

    assert credentials.access_token == "tok-1"
    assert credentials.account_id == "acct-1"
    assert credentials.plan_type == "pro"
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer wl-1"
    assert _body(route.calls.last) == {
        "credential_name": "codex-credentials",
        "credential_field": "auth.json",
        "force_refresh": False,
        "previous_access_token_sha256": "",
    }


async def test_token_is_cached_until_shortly_before_expiry(respx_mock):
    clock = Clock()
    route = respx_mock.post(TOKEN_URL).respond(200, json=_tokens(expires_in=1000))
    broker = _broker(clock=clock)

    await broker.credentials()
    clock.now += 1000 - SKEW - 1
    await broker.credentials()
    assert route.call_count == 1

    clock.now += 2
    await broker.credentials()
    assert route.call_count == 2


async def test_concurrent_callers_share_one_fetch(respx_mock):
    route = respx_mock.post(TOKEN_URL).respond(200, json=_tokens())
    broker = _broker()

    results = await asyncio.gather(*(broker.credentials() for _ in range(5)))

    assert route.call_count == 1
    assert {r.access_token for r in results} == {"tok-1"}


async def test_rejected_token_forces_a_refresh_naming_the_old_token(respx_mock):
    route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json=_tokens("old")),
            httpx.Response(200, json=_tokens("new")),
        ]
    )
    broker = _broker()
    old = await broker.credentials()

    new = await broker.credentials(rejected=old)

    assert new.access_token == "new"
    assert _body(route.calls.last) == {
        "credential_name": "codex-credentials",
        "credential_field": "auth.json",
        "force_refresh": True,
        "previous_access_token_sha256": hashlib.sha256(b"old").hexdigest(),
    }
    assert (await broker.credentials()).access_token == "new"


async def test_rejection_of_an_already_replaced_token_reuses_the_replacement(respx_mock):
    route = respx_mock.post(TOKEN_URL).respond(200, json=_tokens("new"))
    broker = _broker()
    current = await broker.credentials()
    stale = CodexCredentials("stale", "acct-1", "pro", expires_at=0.0)

    assert await broker.credentials(rejected=stale) == current
    assert route.call_count == 1


async def test_409_says_the_subscription_needs_reconnecting(respx_mock):
    respx_mock.post(TOKEN_URL).respond(409, json={"detail": "refresh token revoked"})

    with pytest.raises(CodexSubscriptionError) as excinfo:
        await _broker().credentials()

    message = str(excinfo.value)
    assert "needs reconnecting in Völundr" in message
    assert "refresh token revoked" in message
    assert "Reconnect the Codex credential" in message
    assert isinstance(excinfo.value, ProviderError)


@pytest.mark.parametrize("status", [401, 500, 503])
async def test_other_broker_errors_are_loud(respx_mock, status):
    respx_mock.post(TOKEN_URL).respond(status, json={"detail": "broker says no"})

    with pytest.raises(CodexSubscriptionError, match=f"answered {status}.*broker says no"):
        await _broker().credentials()


async def test_broker_error_without_json_quotes_the_body(respx_mock):
    respx_mock.post(TOKEN_URL).respond(502, text="bad gateway")

    with pytest.raises(CodexSubscriptionError, match="answered 502 .bad gateway"):
        await _broker().credentials()


async def test_unreachable_broker_is_loud(respx_mock):
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(CodexSubscriptionError, match="unreachable.*broker_base_url"):
        await _broker().credentials()


@pytest.mark.parametrize(
    "payload",
    [
        {"access_token": "t", "chatgpt_account_id": "a"},
        {"access_token": "t", "chatgpt_account_id": "a", "expires_in": "soon"},
        ["not", "an", "object"],
    ],
)
async def test_malformed_broker_response_is_loud(respx_mock, payload):
    respx_mock.post(TOKEN_URL).respond(200, json=payload)

    with pytest.raises(CodexSubscriptionError, match="usable"):
        await _broker().credentials()


async def test_non_json_broker_response_is_loud(respx_mock):
    respx_mock.post(TOKEN_URL).respond(200, text="<html>")

    with pytest.raises(CodexSubscriptionError, match="usable"):
        await _broker().credentials()


@pytest.mark.parametrize(
    "payload",
    [
        _tokens() | {"access_token": ""},
        _tokens() | {"chatgpt_account_id": ""},
        {"chatgpt_account_id": "a", "expires_in": 5},
        {"access_token": "t", "expires_in": 5},
    ],
)
async def test_empty_token_or_account_is_loud(respx_mock, payload):
    respx_mock.post(TOKEN_URL).respond(200, json=payload)

    with pytest.raises(CodexSubscriptionError, match="empty"):
        await _broker().credentials()


async def test_auth_adapter_without_headers_is_loud(respx_mock):
    route = respx_mock.post(TOKEN_URL).respond(200, json=_tokens())

    with pytest.raises(CodexSubscriptionError, match="produced no headers"):
        await _broker(FakeAuth(headers={})).credentials()

    assert route.call_count == 0


async def test_rejected_workload_credential_is_reminted_once_when_possible(respx_mock):
    route = respx_mock.post(TOKEN_URL).mock(
        side_effect=[httpx.Response(401), httpx.Response(200, json=_tokens())]
    )
    auth = FakeAuth(can_invalidate=True)

    credentials = await _broker(auth).credentials()

    assert credentials.access_token == "tok-1"
    assert auth.invalidations == 1
    assert [c.request.headers["authorization"] for c in route.calls] == [
        "Bearer wl-1",
        "Bearer wl-2",
    ]


async def test_rejected_workload_credential_fails_when_it_cannot_be_reminted(respx_mock):
    route = respx_mock.post(TOKEN_URL).respond(403, json={"detail": "forbidden"})

    with pytest.raises(CodexSubscriptionError, match="answered 403"):
        await _broker(FakeAuth(can_invalidate=False)).credentials()

    assert route.call_count == 1


async def test_close_releases_the_client():
    broker = _broker()

    await broker.close()

    assert broker._client.is_closed
