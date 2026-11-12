"""Forge session credentials on Bifröst's three model routes (``forge:model``).

Accepted on ``/v1/messages``, ``/v1/chat/completions`` and ``/v1/responses`` in every
auth mode, confirmed by Forge, charged to the owner Forge names, and nowhere else.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
import respx
from fastapi.testclient import TestClient
from httpx import ConnectError, ReadTimeout, Response

from bifrost.adapters.auth.forge_session import ForgeSessionModelAuth
from bifrost.adapters.memory_store import MemoryUsageStore
from bifrost.app import create_app
from bifrost.auth import AgentIdentity, AuthMode
from bifrost.config import BifrostConfig, ForgeSessionAuthConfig, ProviderConfig
from bifrost.ports.auth import AuthPort
from bifrost.translation.models import AnthropicResponse, TextBlock, UsageInfo
from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from niuu.forge_mcp.session_proof import ForgeSessionVerifier

FORGE = "http://forge.test"
SESSION = "0b9e1f7a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"
FORGE_SESSION_URL = f"{FORGE}/api/v1/forge/sessions/{SESSION}"
SECRET = "test-secret-key-that-is-at-least-32-bytes-long!"
OWNER = "owner-7"
TENANT = "tenant-9"

#: Request bodies for the three model routes.
MODEL_ROUTES: dict[str, dict[str, Any]] = {
    "/v1/messages": {
        "model": "claude-sonnet-4-6",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    },
    "/v1/chat/completions": {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"}],
    },
    "/v1/responses": {"model": "claude-sonnet-4-6", "input": "hi"},
}


def _credential(scopes: list[str] | None = None, **claims: Any) -> str:
    now = int(time.time())
    payload = {
        "sub": OWNER,
        "tenant_id": TENANT,
        "token_use": FORGE_SESSION_TOKEN_USE,
        "scopes": ["forge:notify", "forge:session:read", "forge:model"]
        if scopes is None
        else scopes,
        "workload_session_id": SESSION,
        "workload_launch_id": "launch-1",
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    return jwt.encode(payload, SECRET, algorithm="HS256")


def _forge_answer(**overrides: Any) -> dict[str, Any]:
    return {"id": SESSION, "owner_id": OWNER, "tenant_id": TENANT, "status": "running", **overrides}


def _completion() -> AnthropicResponse:
    return AnthropicResponse(
        id="msg",
        content=[TextBlock(text="hi")],
        model="claude-sonnet-4-6",
        stop_reason="end_turn",
        usage=UsageInfo(input_tokens=5, output_tokens=2),
    )


def _config(mode: AuthMode) -> BifrostConfig:
    kwargs: dict[str, Any] = {}
    if mode == AuthMode.PAT:
        kwargs = {"pat_secret": SECRET, "pat_revocation": {"enabled": False}}
    if mode == AuthMode.OIDC:
        kwargs = {
            "oidc_kwargs": {
                "issuers": [
                    {
                        "issuer": "https://idp.test",
                        "audiences": ["bifrost"],
                        "jwks_uri": "https://idp.test/jwks",
                    }
                ]
            }
        }
    return BifrostConfig(
        providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
        auth_mode=mode,
        forge_session={"base_url": FORGE, "timeout_seconds": 5},
        **kwargs,
    )


@contextmanager
def _client(config: BifrostConfig) -> Iterator[tuple[TestClient, MemoryUsageStore]]:
    store = MemoryUsageStore()
    with (
        patch("bifrost.app._build_usage_store", return_value=store),
        patch("bifrost.router.ModelRouter.complete", new_callable=AsyncMock) as complete,
    ):
        complete.return_value = _completion()
        with TestClient(create_app(config), raise_server_exceptions=False) as client:
            yield client, store


def _post(client: TestClient, route: str, token: str, **headers: str) -> Response:
    return client.post(
        route, json=MODEL_ROUTES[route], headers={"Authorization": f"Bearer {token}", **headers}
    )


class TestAcceptedOnEveryModelRouteInEveryMode:
    @pytest.mark.parametrize("mode", list(AuthMode))
    @pytest.mark.parametrize("route", list(MODEL_ROUTES))
    @respx.mock
    def test_confirmed_credential_is_charged_to_the_owner_forge_names(
        self, mode: AuthMode, route: str
    ) -> None:
        forge = respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        token = _credential()
        with _client(_config(mode)) as (client, store):
            response = _post(client, route, token)

            assert response.status_code == 200, response.text
            [call] = forge.calls
            assert call.request.headers["authorization"] == f"Bearer {token}"
            [record] = store._records
            assert (record.agent_id, record.tenant_id, record.session_id) == (
                OWNER,
                TENANT,
                SESSION,
            )

    @pytest.mark.parametrize("route", list(MODEL_ROUTES))
    @respx.mock
    def test_attribution_headers_never_name_the_charged_identity(self, route: str) -> None:
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        with _client(_config(AuthMode.OPEN)) as (client, store):
            response = _post(
                client,
                route,
                _credential(),
                **{
                    "X-Agent-Id": "someone-else",
                    "X-Tenant-Id": "other-tenant",
                    "X-Session-Id": "other-session",
                    "X-Saga-Id": "saga-1",
                    "X-Ravn-Agent-Id": "ravn-else",
                },
            )

            assert response.status_code == 200, response.text
            [record] = store._records
            assert (record.agent_id, record.tenant_id, record.session_id, record.saga_id) == (
                OWNER,
                TENANT,
                SESSION,
                "",
            )

    @respx.mock
    def test_the_identity_is_forges_answer_not_the_credentials_claims(self) -> None:
        respx.get(FORGE_SESSION_URL).mock(
            return_value=Response(200, json=_forge_answer(owner_id="real-owner", tenant_id="real"))
        )
        with _client(_config(AuthMode.PAT)) as (client, store):
            response = _post(
                client, "/v1/messages", _credential(sub="claimed-owner", tenant_id="claimed")
            )

            assert response.status_code == 200
            assert (store._records[0].agent_id, store._records[0].tenant_id) == (
                "real-owner",
                "real",
            )


class TestRefusals:
    @pytest.mark.parametrize("mode", list(AuthMode))
    @respx.mock
    def test_forge_refusal_is_401_naming_why(self, mode: AuthMode) -> None:
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(403, json={"detail": "stopped"}))
        with _client(_config(mode)) as (client, store):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert SESSION in response.json()["detail"]
            assert "403" in response.json()["detail"]
            assert store._records == []

    @respx.mock
    def test_credential_without_forge_model_is_403_and_never_reaches_forge(self) -> None:
        forge = respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        token = _credential(scopes=["forge:notify", "forge:session:read"])
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", token)

            assert response.status_code == 403
            assert "forge:model" in response.json()["detail"]
            assert not forge.called

    @respx.mock
    def test_forge_answering_for_another_session_is_refused(self) -> None:
        other = "11111111-2222-4333-8444-555555555555"
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer(id=other)))
        with _client(_config(AuthMode.OPEN)) as (client, store):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert other in response.json()["detail"]
            assert store._records == []

    @pytest.mark.parametrize("missing", ["owner_id", "tenant_id"])
    @respx.mock
    def test_forge_answer_without_an_owner_or_tenant_is_refused(self, missing: str) -> None:
        respx.get(FORGE_SESSION_URL).mock(
            return_value=Response(200, json=_forge_answer(**{missing: None}))
        )
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert missing in response.json()["detail"]

    @respx.mock
    def test_forge_answer_that_is_not_json_is_refused(self) -> None:
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, text="<html>"))
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert "not JSON" in response.json()["detail"]

    @respx.mock
    def test_forge_answer_that_is_not_an_object_is_refused(self) -> None:
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=["x"]))
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert "not an object" in response.json()["detail"]

    @pytest.mark.parametrize(
        "failure",
        [ConnectError("connection refused"), ReadTimeout("took too long")],
    )
    @respx.mock
    def test_forge_unreachable_is_503_naming_forge_never_admitted(self, failure: Exception) -> None:
        respx.get(FORGE_SESSION_URL).mock(side_effect=failure)
        with _client(_config(AuthMode.OPEN)) as (client, store):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 503
            assert FORGE in response.json()["detail"]
            assert store._records == []

    @respx.mock
    def test_forge_server_error_is_503(self) -> None:
        respx.get(FORGE_SESSION_URL).mock(return_value=Response(502))
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 503
            assert "502" in response.json()["detail"]

    @respx.mock
    def test_no_forge_base_url_refuses_401_naming_the_setting(self) -> None:
        config = BifrostConfig(
            providers={"anthropic": ProviderConfig(models=["claude-sonnet-4-6"])},
            auth_mode=AuthMode.OPEN,
        )
        with _client(config) as (client, store):
            response = _post(client, "/v1/messages", _credential())

            assert response.status_code == 401
            assert "forge_session.base_url" in response.json()["detail"]
            assert store._records == []

    @respx.mock
    def test_credential_naming_no_session_is_refused_without_asking_forge(self) -> None:
        forge = respx.get(url__regex=rf"{FORGE}/.*").mock(return_value=Response(200))
        with _client(_config(AuthMode.OPEN)) as (client, _):
            response = _post(client, "/v1/messages", _credential(workload_session_id="../admin"))

            assert response.status_code == 401
            assert "no valid session" in response.json()["detail"]
            assert not forge.called

    @respx.mock
    def test_every_request_asks_forge_so_a_stopped_session_loses_access(self) -> None:
        forge = respx.get(FORGE_SESSION_URL).mock(
            side_effect=[Response(200, json=_forge_answer()), Response(401)]
        )
        token = _credential()
        with _client(_config(AuthMode.OPEN)) as (client, _):
            assert _post(client, "/v1/messages", token).status_code == 200
            assert _post(client, "/v1/messages", token).status_code == 401
            assert forge.call_count == 2


class TestNowhereElse:
    @pytest.mark.parametrize(
        ("method", "route"),
        [
            ("get", "/v1/models"),
            ("get", "/v1/usage"),
            ("get", "/v1/cache/stats"),
            ("post", "/admin/reload-keys"),
            ("get", "/models"),
            ("get", "/models/claude-sonnet-4-6"),
            ("get", "/aliases"),
            ("get", "/api/tags"),
        ],
    )
    @respx.mock
    @pytest.mark.parametrize("mode", [AuthMode.PAT, AuthMode.OPEN])
    def test_other_routes_do_not_accept_a_session_credential(
        self, mode: AuthMode, method: str, route: str
    ) -> None:
        forge = respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        with _client(_config(mode)) as (client, _):
            response = client.request(
                method.upper(), route, headers={"Authorization": f"Bearer {_credential()}"}
            )

            if mode == AuthMode.PAT:
                # the configured adapter's own answer to a scoped token, as before
                assert response.status_code == 403
            assert not forge.called

    @respx.mock
    def test_ollama_compatible_routes_do_not_accept_a_session_credential(self) -> None:
        forge = respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        with _client(_config(AuthMode.PAT)) as (client, _):
            response = client.post(
                "/api/chat",
                json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "x"}]},
                headers={"Authorization": f"Bearer {_credential()}"},
            )

            assert response.status_code == 403
            assert not forge.called

    @respx.mock
    def test_pat_and_oidc_still_refuse_scoped_tokens_on_model_routes_that_are_not_session(
        self,
    ) -> None:
        build = _credential(token_use="valkyrie_build", scopes=["forge:session:create"])
        forge = respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
        with _client(_config(AuthMode.PAT)) as (client, _):
            response = _post(client, "/v1/messages", build)

            assert response.status_code == 403
            assert not forge.called


class RecordingInner(AuthPort):
    def __init__(self) -> None:
        self.seen = 0

    async def extract(self, request: Any) -> AgentIdentity:
        self.seen += 1
        return AgentIdentity(agent_id="inner")


class FakeRequest:
    def __init__(self, authorization: str | None) -> None:
        self.headers = {} if authorization is None else {"authorization": authorization}


class TestWrapperAdapter:
    async def test_non_session_requests_go_to_the_configured_adapter(self) -> None:
        inner = RecordingInner()
        adapter = ForgeSessionModelAuth(inner, verifier=None)

        for header in (None, "Basic abc", "Bearer not-a-jwt", f"Bearer {_pat_like()}"):
            identity = await adapter.extract(FakeRequest(header))  # type: ignore[arg-type]
            assert identity.agent_id == "inner"
        assert inner.seen == 4

    async def test_bearer_scheme_is_case_insensitive_and_trimmed(self) -> None:
        verifier = ForgeSessionVerifier(FORGE, timeout_seconds=5)
        adapter = ForgeSessionModelAuth(RecordingInner(), verifier)
        with respx.mock:
            respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
            identity = await adapter.extract(
                FakeRequest(f"bearer   {_credential()}  ")  # type: ignore[arg-type]
            )
        await verifier.close()

        assert identity == AgentIdentity(agent_id=OWNER, tenant_id=TENANT, session_id=SESSION)

    async def test_identity_carries_no_roles(self) -> None:
        verifier = ForgeSessionVerifier(FORGE, timeout_seconds=5)
        adapter = ForgeSessionModelAuth(RecordingInner(), verifier)
        with respx.mock:
            respx.get(FORGE_SESSION_URL).mock(return_value=Response(200, json=_forge_answer()))
            identity = await adapter.extract(FakeRequest(f"Bearer {_credential()}"))  # type: ignore[arg-type]
        await verifier.close()

        assert identity.roles == ()


def _pat_like() -> str:
    return jwt.encode({"sub": "u", "type": "pat"}, SECRET, algorithm="HS256")


class TestConfig:
    def test_defaults_leave_session_credentials_unaccepted(self) -> None:
        assert ForgeSessionAuthConfig().base_url == ""

    @pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
    def test_timeout_must_be_positive_and_finite(self, timeout: float) -> None:
        with pytest.raises(ValueError):
            ForgeSessionAuthConfig(base_url=FORGE, timeout_seconds=timeout)

    def test_base_url_must_be_http(self) -> None:
        with pytest.raises(ValueError, match="http://"):
            ForgeSessionAuthConfig(base_url="forge.internal:8080")

    def test_verifier_rejects_an_empty_url_and_a_bad_timeout(self) -> None:
        with pytest.raises(ValueError, match="Forge base URL"):
            ForgeSessionVerifier(" ", timeout_seconds=5)
        with pytest.raises(ValueError, match="timeout"):
            ForgeSessionVerifier(FORGE, timeout_seconds=0)
