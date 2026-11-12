"""Confirming a ``forge_session`` credential by presenting it to Forge."""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
import pytest

from niuu.domain.services.token_scope import FORGE_SESSION_TOKEN_USE
from niuu.forge_mcp.session_proof import (
    ConfirmedSession,
    ForgeSessionVerifier,
    ForgeUnavailableError,
    SessionRefusedError,
    session_id_of,
)

SESSION = "0b9e1f7a-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


def _credential(**claims: Any) -> str:
    now = int(time.time())
    payload = {
        "sub": "owner-1",
        "token_use": FORGE_SESSION_TOKEN_USE,
        "scopes": ["forge:model"],
        "workload_session_id": SESSION,
        "iat": now,
        "exp": now + 600,
        **claims,
    }
    return jwt.encode(payload, "k" * 32, algorithm="HS256")


def _answer(**overrides: Any) -> dict[str, Any]:
    return {"id": SESSION, "owner_id": "owner-1", "tenant_id": "tenant-1", **overrides}


def _verifier(handler: Any, base_url: str = "http://forge.test/") -> ForgeSessionVerifier:
    return ForgeSessionVerifier(base_url, timeout_seconds=3, transport=httpx.MockTransport(handler))


class TestSessionIdOf:
    def test_reads_the_bound_session(self) -> None:
        assert session_id_of(_credential()) == SESSION

    def test_normalises_the_case(self) -> None:
        assert session_id_of(_credential(workload_session_id=SESSION.upper())) == SESSION

    @pytest.mark.parametrize("bad", [None, "", "../x", "not-a-uuid", 7])
    def test_a_missing_or_malformed_session_is_refused(self, bad: Any) -> None:
        with pytest.raises(SessionRefusedError, match="no valid session"):
            session_id_of(_credential(workload_session_id=bad))

    @pytest.mark.parametrize("token", ["", "not-a-jwt"])
    def test_a_credential_that_is_not_a_jwt_is_refused(self, token: str) -> None:
        with pytest.raises(SessionRefusedError, match="not a Forge session credential"):
            session_id_of(token)

    def test_another_kind_of_credential_is_refused(self) -> None:
        with pytest.raises(SessionRefusedError, match="not a Forge session credential"):
            session_id_of(_credential(token_use="valkyrie_build"))


class TestConfirm:
    async def test_presents_the_credential_to_forges_session_route(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=_answer())

        verifier = _verifier(handler)
        token = _credential()
        confirmed = await verifier.confirm(token)
        await verifier.close()

        assert confirmed == ConfirmedSession(SESSION, "owner-1", "tenant-1")
        [request] = seen
        assert request.method == "GET"
        assert str(request.url) == f"http://forge.test/api/v1/forge/sessions/{SESSION}"
        assert request.headers["authorization"] == f"Bearer {token}"

    async def test_reuses_one_client_and_close_is_idempotent(self) -> None:
        verifier = _verifier(lambda _r: httpx.Response(200, json=_answer()))
        await verifier.confirm(_credential())
        await verifier.confirm(_credential())
        await verifier.close()
        await verifier.close()

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
    async def test_a_forge_refusal_names_the_status_and_the_session(self, status: int) -> None:
        verifier = _verifier(lambda _r: httpx.Response(status, json={"detail": "no"}))
        with pytest.raises(SessionRefusedError, match=f"{SESSION}.*{status}"):
            await verifier.confirm(_credential())
        await verifier.close()

    @pytest.mark.parametrize("status", [500, 502, 503])
    async def test_a_forge_server_error_is_unavailable_not_refused(self, status: int) -> None:
        verifier = _verifier(lambda _r: httpx.Response(status))
        with pytest.raises(ForgeUnavailableError, match=str(status)):
            await verifier.confirm(_credential())
        await verifier.close()

    async def test_an_unreachable_forge_names_the_url_and_the_cause(self) -> None:
        def refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        verifier = _verifier(refuse)
        with pytest.raises(ForgeUnavailableError, match=r"forge\.test.*ConnectError"):
            await verifier.confirm(_credential())
        await verifier.close()

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            ({"owner_id": "o", "tenant_id": "t"}, "answered for session ''"),
            (_answer(id="other"), "answered for session 'other'"),
            (_answer(owner_id=""), "no owner_id"),
            (_answer(owner_id=7), "no owner_id"),
            (_answer(tenant_id=None), "no tenant_id"),
            (["x"], "not an object"),
        ],
    )
    async def test_an_answer_that_does_not_identify_the_session_is_refused(
        self, body: Any, reason: str
    ) -> None:
        verifier = _verifier(lambda _r: httpx.Response(200, json=body))
        with pytest.raises(SessionRefusedError, match=reason):
            await verifier.confirm(_credential())
        await verifier.close()

    async def test_the_session_id_match_ignores_case(self) -> None:
        verifier = _verifier(lambda _r: httpx.Response(200, json=_answer(id=SESSION.upper())))
        assert (await verifier.confirm(_credential())).session_id == SESSION
        await verifier.close()

    async def test_a_credential_that_names_no_session_never_reaches_forge(self) -> None:
        calls: list[httpx.Request] = []
        verifier = _verifier(lambda r: calls.append(r) or httpx.Response(200, json=_answer()))
        with pytest.raises(SessionRefusedError):
            await verifier.confirm(_credential(workload_session_id="../../admin"))
        assert calls == []
        await verifier.close()
