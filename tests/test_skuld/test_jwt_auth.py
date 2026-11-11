"""Tests for Skuld JWT extraction and the broker's Forge credential."""

import base64
import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from skuld.broker import (
    Broker,
    _decode_jwt_claims,
    _extract_bearer_token,
    _extract_token_from_websocket,
)
from skuld.config import SkuldSettings
from skuld.event_log import (
    EventLogRejectedError,
    ForgeCredentialError,
    _EventLogHeadUnavailableError,
)


def _make_jwt(claims: dict) -> str:
    """Build a fake JWT (header.payload.signature) with the given claims."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=")
    signature = base64.urlsafe_b64encode(b"fake-sig").rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.{signature.decode()}"


class TestDecodeJwtClaims:
    """Tests for _decode_jwt_claims helper."""

    def test_valid_jwt(self):
        claims = {"sub": "user-123", "email": "test@example.com", "tenant_id": "t1"}
        token = _make_jwt(claims)
        result = _decode_jwt_claims(token)
        assert result["sub"] == "user-123"
        assert result["email"] == "test@example.com"

    def test_invalid_token_returns_empty(self):
        assert _decode_jwt_claims("not-a-jwt") == {}

    def test_empty_string_returns_empty(self):
        assert _decode_jwt_claims("") == {}

    def test_malformed_base64_returns_empty(self):
        assert _decode_jwt_claims("a.!!!invalid!!!.c") == {}


class TestExtractBearerToken:
    """Tests for _extract_bearer_token helper."""

    def test_bearer_token(self):
        assert _extract_bearer_token({"authorization": "Bearer abc123"}) == "abc123"

    def test_bearer_case_insensitive(self):
        assert _extract_bearer_token({"authorization": "bearer abc123"}) == "abc123"

    def test_no_auth_header(self):
        assert _extract_bearer_token({}) is None

    def test_non_bearer_auth(self):
        assert _extract_bearer_token({"authorization": "Basic abc123"}) is None


class TestExtractTokenFromWebSocket:
    """Tests for _extract_token_from_websocket helper."""

    def _make_ws(self, headers: dict | None = None, query_params: dict | None = None):
        ws = MagicMock()
        ws.headers = headers or {}
        ws.query_params = query_params or {}
        return ws

    def test_authorization_header_preferred(self):
        ws = self._make_ws(
            headers={"authorization": "Bearer header-token"},
            query_params={"access_token": "query-token"},
        )
        assert _extract_token_from_websocket(ws) == "header-token"

    def test_envoy_query_param(self):
        ws = self._make_ws(query_params={"token": "query-token"})
        assert _extract_token_from_websocket(ws) == "query-token"

    def test_legacy_query_param_fallback(self):
        ws = self._make_ws(query_params={"access_token": "query-token"})
        assert _extract_token_from_websocket(ws) == "query-token"

    def test_websocket_subprotocol_preferred_over_query_param(self):
        ws = self._make_ws(
            headers={"sec-websocket-protocol": "volundr.bearer.protocol-token"},
            query_params={"access_token": "query-token"},
        )
        assert _extract_token_from_websocket(ws) == "protocol-token"

    def test_no_token_returns_none(self):
        ws = self._make_ws()
        assert _extract_token_from_websocket(ws) is None


class TestBrokerForgeCredential:
    """The broker's Forge credential comes from configuration alone, and fails loudly."""

    @pytest.fixture
    def settings(self, tmp_path):
        return SkuldSettings(
            session={"id": "test-session", "workspace_dir": str(tmp_path)},
            transport="subprocess",
            volundr_api_url="http://volundr-api:8080",
            workload_identity={"token_file": str(tmp_path / "no-workload-token")},
        )

    @pytest.fixture
    def test_broker(self, settings):
        return Broker(settings=settings)

    @pytest.fixture
    def projected(self, tmp_path, test_broker):
        token_file = tmp_path / "workload-token"
        token_file.write_text("projected-proof", encoding="utf-8")
        test_broker._settings.workload_identity.token_file = str(token_file)
        return token_file

    @staticmethod
    def _exchange(monkeypatch, handler):
        real = httpx.AsyncClient

        def make(*args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(handler))
            return real(*args, **kwargs)

        monkeypatch.setattr("skuld.event_log.httpx.AsyncClient", make)

    def test_no_browser_login_is_kept(self, test_broker):
        assert not hasattr(test_broker, "_user_jwt")
        assert not hasattr(test_broker, "_update_jwt_from_websocket")

    @pytest.mark.asyncio
    async def test_no_configured_credential_sends_none(self, test_broker, monkeypatch):
        self._exchange(monkeypatch, lambda request: pytest.fail("no exchange expected"))

        await test_broker._refresh_workload_token()

        assert test_broker._workload_jwt is None
        assert test_broker._build_auth_headers() == {}

    @pytest.mark.asyncio
    async def test_external_token_is_the_credential(self, test_broker, projected, monkeypatch):
        test_broker._settings.external_api_token = "service-token"
        self._exchange(monkeypatch, lambda request: pytest.fail("no exchange expected"))

        client = await test_broker._get_http_client()

        assert client.headers["authorization"] == "Bearer service-token"
        await client.aclose()

    @pytest.mark.asyncio
    async def test_workload_exchange_is_the_credential(self, test_broker, projected, monkeypatch):
        expires = time.time() + 600

        def handler(request):
            assert json.loads(request.content)["token"] == "projected-proof"
            return httpx.Response(200, json={"token": "workload-jwt", "expiresAt": expires})

        self._exchange(monkeypatch, handler)

        client = await test_broker._get_http_client()

        assert client.headers["authorization"] == "Bearer workload-jwt"
        assert test_broker._workload_jwt_expires_at == expires
        assert await test_broker._get_http_client() is client
        await client.aclose()

    @pytest.mark.asyncio
    async def test_refused_exchange_raises_with_the_remedy(
        self, test_broker, projected, monkeypatch
    ):
        self._exchange(monkeypatch, lambda request: httpx.Response(403, text="no mapping"))

        with pytest.raises(ForgeCredentialError) as caught:
            await test_broker._get_http_client()

        assert caught.value.transient is False
        assert "no mapping" in str(caught.value)
        assert "external_api_token" in str(caught.value)
        assert test_broker._http_client is None

    @pytest.mark.parametrize("status", [500, 503, 408, 429])
    @pytest.mark.asyncio
    async def test_exchange_outage_is_transient(self, test_broker, projected, monkeypatch, status):
        self._exchange(monkeypatch, lambda request: httpx.Response(status))

        with pytest.raises(ForgeCredentialError) as caught:
            await test_broker._refresh_workload_token()

        assert caught.value.transient is True

    @pytest.mark.asyncio
    async def test_unreachable_exchange_is_transient(self, test_broker, projected, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        self._exchange(monkeypatch, handler)

        with pytest.raises(ForgeCredentialError) as caught:
            await test_broker._refresh_workload_token()

        assert caught.value.transient is True

    @pytest.mark.parametrize(
        "answer", [{"token": "workload-jwt"}, {"expiresAt": 9999999999}, ["not", "an", "object"]]
    )
    @pytest.mark.asyncio
    async def test_answer_without_token_and_expiry_raises(
        self, test_broker, projected, monkeypatch, answer
    ):
        self._exchange(monkeypatch, lambda request: httpx.Response(200, json=answer))

        with pytest.raises(ForgeCredentialError) as caught:
            await test_broker._refresh_workload_token()

        assert caught.value.transient is False
        assert test_broker._workload_jwt is None

    @pytest.mark.asyncio
    async def test_non_json_answer_raises(self, test_broker, projected, monkeypatch):
        self._exchange(monkeypatch, lambda request: httpx.Response(200, text="<html>"))

        with pytest.raises(ForgeCredentialError, match="no JSON") as caught:
            await test_broker._refresh_workload_token()

        assert caught.value.transient is False

    @pytest.mark.asyncio
    async def test_empty_projected_token_raises(self, test_broker, projected):
        projected.write_text("", encoding="utf-8")

        with pytest.raises(ForgeCredentialError, match="is empty"):
            await test_broker._refresh_workload_token()

    def test_headers_without_an_exchange_raise(self, test_broker, projected):
        with pytest.raises(ForgeCredentialError):
            test_broker._build_auth_headers()

    @pytest.mark.asyncio
    async def test_refreshed_workload_jwt_replaces_the_client(
        self, test_broker, projected, monkeypatch
    ):
        tokens = iter(["first-jwt", "second-jwt"])
        self._exchange(
            monkeypatch,
            lambda request: httpx.Response(
                200, json={"token": next(tokens), "expiresAt": time.time() + 600}
            ),
        )
        first = await test_broker._get_http_client()
        test_broker._workload_jwt_expires_at = 0.0

        second = await test_broker._get_http_client()

        assert second is not first
        assert second.headers["authorization"] == "Bearer second-jwt"
        await second.aclose()


class TestSessionHttpClient:
    """The session's own record is written with its session credential when it has one."""

    def _broker(self, tmp_path, token: str) -> Broker:
        return Broker(
            settings=SkuldSettings(
                session={"id": "test-session", "workspace_dir": str(tmp_path)},
                transport="subprocess",
                volundr_api_url="http://volundr-api:8080",
                external_api_token="service-token",
                workload_identity={"token_file": str(tmp_path / "no-workload-token")},
                forge_mcp={"token": token, "runtime_dir": str(tmp_path / "rt")},
            )
        )

    @pytest.mark.asyncio
    async def test_session_credential_carries_the_sessions_own_calls(self, tmp_path):
        broker = self._broker(tmp_path, "session-credential")

        session_client = await broker._get_session_http_client()
        broker_client = await broker._get_http_client()

        assert session_client is not broker_client
        assert session_client.headers["authorization"] == "Bearer session-credential"
        assert broker_client.headers["authorization"] == "Bearer service-token"
        assert await broker._get_session_http_client() is session_client
        await broker.shutdown()
        assert broker._session_http_client is None

    @pytest.mark.asyncio
    async def test_without_a_session_credential_the_broker_credential_is_used(self, tmp_path):
        broker = self._broker(tmp_path, "")

        assert await broker._get_session_http_client() is await broker._get_http_client()
        await broker._http_client.aclose()


class TestEventLogCredentialFailures:
    """A credential the event log cannot get stops it; an outage is retried, loudly."""

    @pytest.fixture
    def broker(self, tmp_path):
        return Broker(
            settings=SkuldSettings(
                session={"id": "test-session", "workspace_dir": str(tmp_path)},
                transport="subprocess",
                volundr_api_url="http://volundr-api:8080",
                event_log_flush_interval_ms=1,
                workload_identity={"token_file": str(tmp_path / "no-workload-token")},
            )
        )

    @pytest.mark.asyncio
    async def test_permanent_failure_ends_the_worker(self, broker):
        broker._flush_event_log = AsyncMock(
            side_effect=ForgeCredentialError("refused", transient=False)
        )

        with pytest.raises(ForgeCredentialError):
            await broker._event_log_flush_loop()

    @pytest.mark.asyncio
    async def test_outage_is_logged_at_error_and_retried(self, broker, caplog):
        calls = 0

        async def flush():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ForgeCredentialError("exchange unreachable", transient=True)
            broker._event_log_stopping = True

        broker._flush_event_log = flush

        with caplog.at_level(logging.ERROR, logger="skuld.broker"):
            await broker._event_log_flush_loop()

        assert calls == 2
        assert "exchange unreachable" in caplog.text

    @pytest.mark.parametrize(
        ("transient", "expected"),
        [(True, _EventLogHeadUnavailableError), (False, EventLogRejectedError)],
    )
    @pytest.mark.asyncio
    async def test_head_fetch_maps_credential_failures(self, broker, transient, expected):
        broker._get_session_http_client = AsyncMock(
            side_effect=ForgeCredentialError("no credential", transient=transient)
        )

        with pytest.raises(expected, match="no credential"):
            await broker._fetch_event_log_head()
