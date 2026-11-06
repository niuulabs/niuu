"""Tests for the live-preview router (``niuu.preview_proxy``).

The router is driven the way production composes it: ``PreviewRouterMiddleware``
on a FastAPI app whose identity adapter reads Envoy's ``x-auth-*`` headers, with a
real ``SkuldPortRegistry`` (ownership guard, room-role resolver, credential
issuer). The upstream Skuld is a ``MockTransport`` for HTTP and a real local
WebSocket server for the bridge.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from identity.adapters.identity import EnvoyHeaderAuthenticationAdapter
from niuu.config import NiuuSettings
from niuu.config_models import SessionPreviewConfig
from niuu.domain.models import Principal
from niuu.ports.session_proxy import SessionProxyTarget
from niuu.preview_proxy import (
    PreviewAddress,
    PreviewRouter,
    PreviewRouterMiddleware,
    parse_preview_host,
)
from niuu.session_proxy import PreviewCredentialIssuerMissingError, SkuldPortRegistry

SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"
DOMAIN = "preview.niuu.test"
HOST = f"3000--{SESSION}.{DOMAIN}"
CREDENTIAL = "minted-router-credential"

OWNER_HEADERS = {
    "x-auth-user-id": "user-1",
    "x-auth-tenant": "tenant-1",
    "x-auth-roles": "volundr:developer",
}


class TestParsePreviewHost:
    def test_reads_port_and_session(self) -> None:
        assert parse_preview_host(HOST, DOMAIN) == PreviewAddress(port=3000, session_id=SESSION)

    def test_host_with_a_port_suffix(self) -> None:
        address = parse_preview_host(f"5173--{SESSION}.localhost:8080", "localhost")
        assert address == PreviewAddress(port=5173, session_id=SESSION)

    def test_uppercase_host_and_domain(self) -> None:
        address = parse_preview_host(
            f"3000--{SESSION.upper()}.PREVIEW.Niuu.Test", "Preview.NIUU.test"
        )
        assert address == PreviewAddress(port=3000, session_id=SESSION)

    def test_surrounding_dots_and_spaces_in_the_domain_and_host(self) -> None:
        assert parse_preview_host(f"  {HOST}  ", f".{DOMAIN}.") is not None

    @pytest.mark.parametrize("port", ["0", "65536", "99999", "000000"])
    def test_port_outside_1_to_65535_is_not_a_preview(self, port: str) -> None:
        assert parse_preview_host(f"{port}--{SESSION}.{DOMAIN}", DOMAIN) is None

    def test_port_bounds_are_inclusive(self) -> None:
        assert parse_preview_host(f"1--{SESSION}.{DOMAIN}", DOMAIN).port == 1
        assert parse_preview_host(f"65535--{SESSION}.{DOMAIN}", DOMAIN).port == 65535

    def test_extra_labels_in_front_are_rejected(self) -> None:
        assert parse_preview_host(f"evil.3000--{SESSION}.{DOMAIN}", DOMAIN) is None

    def test_extra_labels_between_are_rejected(self) -> None:
        assert parse_preview_host(f"3000--{SESSION}.extra.{DOMAIN}", DOMAIN) is None

    def test_wrong_domain(self) -> None:
        assert parse_preview_host(f"3000--{SESSION}.other.test", DOMAIN) is None

    def test_the_bare_domain_and_a_suffix_lookalike(self) -> None:
        assert parse_preview_host(DOMAIN, DOMAIN) is None
        assert parse_preview_host(f"3000--{SESSION}.x{DOMAIN}", DOMAIN) is None

    def test_session_must_be_a_uuid(self) -> None:
        assert parse_preview_host(f"3000--not-a-uuid.{DOMAIN}", DOMAIN) is None
        assert parse_preview_host(f"3000--{SESSION[:-1]}.{DOMAIN}", DOMAIN) is None

    def test_missing_separator(self) -> None:
        assert parse_preview_host(f"3000-{SESSION}.{DOMAIN}", DOMAIN) is None

    def test_empty_domain_matches_nothing(self) -> None:
        assert parse_preview_host(HOST, "") is None

    def test_empty_host(self) -> None:
        assert parse_preview_host("", DOMAIN) is None


class TestRawPath:
    def test_the_browsers_raw_path_is_kept(self) -> None:
        from niuu.preview_proxy import _raw_path

        assert _raw_path({"raw_path": b"/@vite/client%2Fx", "path": "/decoded"}) == (
            "/@vite/client%2Fx"
        )

    def test_without_a_raw_path_the_decoded_path_is_requoted(self) -> None:
        from niuu.preview_proxy import _raw_path

        assert _raw_path({"path": "/a b/@vite/é"}) == "/a%20b/@vite/%C3%A9"
        assert _raw_path({}) == "/"


class TestSessionPreviewConfig:
    def test_disabled_by_default_needs_no_domain(self) -> None:
        config = SessionPreviewConfig()
        assert config.enabled is False
        assert config.domain == ""

    def test_enabled_without_a_domain_names_the_remedy(self) -> None:
        with pytest.raises(ValidationError, match="session_preview.domain is empty"):
            SessionPreviewConfig(enabled=True)

    def test_enabled_with_only_dots_and_spaces_is_still_empty(self) -> None:
        with pytest.raises(ValidationError, match="disable live preview"):
            SessionPreviewConfig(enabled=True, domain="  .. ")

    def test_domain_is_normalised(self) -> None:
        config = SessionPreviewConfig(enabled=True, domain="  .Preview.Example.COM. ")
        assert config.domain == "preview.example.com"

    def test_disabled_domain_is_normalised_too(self) -> None:
        assert SessionPreviewConfig(domain="Foo.").domain == "foo"

    @pytest.mark.parametrize("ttl", [9, 901])
    def test_credential_ttl_is_bounded(self, ttl: int) -> None:
        with pytest.raises(ValidationError):
            SessionPreviewConfig(credential_ttl_seconds=ttl)

    def test_connect_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SessionPreviewConfig(upstream_connect_timeout_seconds=0)

    def test_the_local_host_serves_previews_on_localhost_by_default(self) -> None:
        config = NiuuSettings().host.session_preview
        assert config.enabled is True
        assert config.domain == "localhost"


class TestPreviewCredential:
    def _principal(self) -> Principal:
        return Principal(user_id="u", email="", tenant_id="t", roles=[])

    async def test_the_wired_issuer_mints_for_the_session_and_principal(self, tmp_path) -> None:
        registry = SkuldPortRegistry(state_file=tmp_path / "s.json")
        calls: list[tuple[str, Principal]] = []

        async def issuer(session_id: str, principal: Principal) -> str:
            calls.append((session_id, principal))
            return "token-1"

        registry.set_preview_credential_issuer(issuer)
        principal = self._principal()

        assert await registry.preview_credential(SESSION, principal) == "token-1"
        assert calls == [(SESSION, principal)]

    async def test_issuer_wins_even_with_dev_identity(self, tmp_path) -> None:
        registry = SkuldPortRegistry(state_file=tmp_path / "s.json", dev_identity=True)

        async def issuer(session_id: str, principal: Principal) -> str:
            return "token-2"

        registry.set_preview_credential_issuer(issuer)

        assert await registry.preview_credential(SESSION, self._principal()) == "token-2"

    async def test_dev_identity_without_an_issuer_needs_no_credential(self, tmp_path) -> None:
        registry = SkuldPortRegistry(state_file=tmp_path / "s.json", dev_identity=True)

        assert await registry.preview_credential(SESSION, self._principal()) == ""

    async def test_no_issuer_and_no_dev_identity_raises_with_the_remedy(self, tmp_path) -> None:
        registry = SkuldPortRegistry(state_file=tmp_path / "s.json", dev_identity=False)

        with pytest.raises(PreviewCredentialIssuerMissingError) as raised:
            await registry.preview_credential(SESSION, self._principal())

        message = str(raised.value)
        assert "enable workload_identity" in message
        assert "forge:session:preview" in message
        assert "disable session_preview" in message


# --------------------------------------------------------------------------- router


class _Env:
    """A wired preview router: app, registry, and what the issuer/guard saw."""

    def __init__(self, tmp_path, *, provider_ready: bool = True) -> None:
        self.registry = SkuldPortRegistry(state_file=tmp_path / "state.json")
        self.attach_allowed = True
        self.role: str | None = "owner"
        self.issuer_calls: list[tuple[str, Principal]] = []
        self.guard_calls: list[tuple[Any, ...]] = []
        self.upstream: list[httpx.Request] = []
        self.upstream_handler = self._default_upstream

        async def guard(session_id, user_id, tenant_id, roles) -> bool:
            self.guard_calls.append((session_id, user_id, tenant_id, roles))
            return self.attach_allowed

        async def role_resolver(session_id, user_id, tenant_id, roles):
            return self.role

        async def issuer(session_id: str, principal: Principal) -> str:
            self.issuer_calls.append((session_id, principal))
            return CREDENTIAL

        self.registry.set_ownership_guard(guard)
        self.registry.set_room_role_resolver(role_resolver)
        self.registry.set_preview_credential_issuer(issuer)

        self.config = SessionPreviewConfig(enabled=True, domain=DOMAIN)
        self.router = PreviewRouter(self.registry, self.config)
        self.router._client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))
        self.app = FastAPI()
        self.app.state.identity = EnvoyHeaderAuthenticationAdapter()
        self.app.add_middleware(
            PreviewRouterMiddleware,
            domain=DOMAIN,
            router_provider=lambda: self.router if provider_ready else None,
        )

        @self.app.get("/platform")
        async def platform() -> dict[str, str]:
            return {"served_by": "wrapped app"}

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.upstream.append(request)
        await request.aread()
        return await self.upstream_handler(request)

    @staticmethod
    async def _default_upstream(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Body(b"ok"))

    def client(self, host: str = HOST) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url=f"http://{host}"
        )


class _Body(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk


@pytest.fixture
def env(tmp_path):
    return _Env(tmp_path)


class TestRouterPassThrough:
    async def test_non_preview_hosts_reach_the_wrapped_app(self, env) -> None:
        async with env.client("app.niuu.test") as client:
            response = await client.get("/platform")

        assert response.json() == {"served_by": "wrapped app"}
        assert env.upstream == []

    async def test_a_lookalike_host_is_not_a_preview(self, env) -> None:
        async with env.client(f"3000--{SESSION}.other.test") as client:
            response = await client.get("/platform")

        assert response.json() == {"served_by": "wrapped app"}

    async def test_lifespan_scopes_reach_the_wrapped_app(self, env) -> None:
        seen: list[str] = []

        async def inner(scope, receive, send) -> None:
            seen.append(scope["type"])

        middleware = PreviewRouterMiddleware(inner, domain=DOMAIN, router_provider=lambda: None)
        await middleware({"type": "lifespan"}, None, None)

        assert seen == ["lifespan"]


class TestRouterRefusals:
    async def test_no_principal_is_401(self, env) -> None:
        async with env.client() as client:
            response = await client.get("/")

        assert response.status_code == 401
        assert response.text == "Sign in to open this preview."
        assert env.upstream == []
        assert env.issuer_calls == []

    async def test_identity_not_configured_is_401(self, env) -> None:
        env.app.state.identity = None
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 401

    async def test_a_principal_without_a_user_id_is_401(self, env, monkeypatch) -> None:
        async def anonymous(connection):
            return Principal(user_id="", email="", tenant_id="", roles=[])

        monkeypatch.setattr("niuu.preview_proxy._proxy_principal", anonymous)
        async with env.client() as client:
            response = await client.get("/")

        assert response.status_code == 401

    async def test_a_user_who_may_not_attach_is_403(self, env) -> None:
        env.registry.register(SESSION, 9100)
        env.attach_allowed = False
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 403
        assert response.text == "This preview belongs to a session you do not own."
        assert env.upstream == []
        assert env.issuer_calls == []
        assert env.guard_calls == [(SESSION, "user-1", "tenant-1", ("volundr:developer",))]

    @pytest.mark.parametrize("role", ["viewer", "approver", None])
    async def test_a_room_participant_who_is_not_the_owner_is_403(self, env, role) -> None:
        env.registry.register(SESSION, 9100)
        env.role = role
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 403
        assert response.text == "Only the session's owner can open its preview."
        assert env.upstream == []
        assert env.issuer_calls == []

    async def test_a_session_with_no_target_is_404(self, env) -> None:
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 404
        assert response.text == "This session is not running."
        assert env.issuer_calls == []

    async def test_router_not_wired_yet_is_503_never_unguarded(self, tmp_path) -> None:
        env = _Env(tmp_path, provider_ready=False)
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 503
        assert "starting" in response.text
        assert env.upstream == []

    async def test_a_missing_credential_issuer_surfaces_as_an_error(self, env) -> None:
        env.registry._preview_credential_issuer = None
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            with pytest.raises(PreviewCredentialIssuerMissingError):
                await client.get("/", headers=OWNER_HEADERS)
        assert env.upstream == []


class TestRouterForwardsHttp:
    async def test_platform_credentials_stay_out_and_the_router_credential_goes_in(
        self, env
    ) -> None:
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            response = await client.get(
                "/@vite/client?x=1&y=%20z",
                headers={
                    **OWNER_HEADERS,
                    "authorization": "Bearer browser-access-token",
                    "cookie": "session=secret; app=1",
                    "x-auth-email": "a@b.test",
                    "x-envoy-original-path": "/x",
                    "x-niuu-room-role": "owner",
                    "forwarded": "for=1.2.3.4",
                    "x-forwarded-host": "forged.example",
                    "x-forwarded-port": "1",
                    "accept": "text/html",
                    "x-app-header": "kept",
                    "connection": "keep-alive",
                },
            )

        assert response.status_code == 200
        assert response.text == "ok"
        (request,) = env.upstream
        assert request.url.host == "127.0.0.1"
        assert request.url.port == 9100
        assert request.url.raw_path == b"/preview/3000/@vite/client?x=1&y=%20z"
        assert request.headers.get_list("authorization") == [f"Bearer {CREDENTIAL}"]
        assert "cookie" not in request.headers
        assert not [name for name in request.headers if name.startswith("x-auth-")]
        assert not [name for name in request.headers if name.startswith("x-envoy-")]
        assert "x-niuu-room-role" not in request.headers
        assert "forwarded" not in request.headers
        assert "x-forwarded-port" not in request.headers
        assert request.headers.get_list("x-forwarded-host") == [HOST]
        assert request.headers.get_list("x-forwarded-proto") == ["http"]
        assert request.headers["accept"] == "text/html"
        assert request.headers["x-app-header"] == "kept"
        assert request.headers["host"] == "127.0.0.1:9100"
        assert env.issuer_calls[0][0] == SESSION
        assert env.issuer_calls[0][1].user_id == "user-1"

    async def test_forwarded_proto_from_the_edge_is_trusted_over_the_hop_scheme(self, env) -> None:
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            await client.get("/", headers={**OWNER_HEADERS, "x-forwarded-proto": "https, http"})
            await client.get("/", headers={**OWNER_HEADERS, "x-forwarded-proto": "javascript"})

        assert [r.headers["x-forwarded-proto"] for r in env.upstream] == ["https", "http"]

    async def test_the_root_path_is_forwarded_as_preview_port_slash(self, env) -> None:
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            await client.get("/", headers=OWNER_HEADERS)

        assert env.upstream[0].url.raw_path == b"/preview/3000/"

    async def test_a_dev_identity_registry_sends_no_authorization(self, tmp_path) -> None:
        env = _Env(tmp_path)
        env.registry._dev_identity = True
        env.registry._preview_credential_issuer = None
        env.registry.register(SESSION, 9100)
        async with env.client() as client:
            await client.get("/", headers=OWNER_HEADERS)

        assert "authorization" not in env.upstream[0].headers

    async def test_request_and_response_bodies_stream(self, env) -> None:
        env.registry.register(SESSION, 9100)

        async def upstream(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert request.content == b"part-1part-2"
            return httpx.Response(201, stream=_Body(b"chunk-a", b"chunk-b", b"chunk-c"))

        env.upstream_handler = upstream

        async def parts():
            yield b"part-1"
            yield b"part-2"

        async with env.client() as client:
            response = await client.post("/upload", headers=OWNER_HEADERS, content=parts())

        assert response.status_code == 201
        assert response.content == b"chunk-achunk-bchunk-c"

    async def test_several_set_cookie_headers_survive_and_lose_their_domain(self, env) -> None:
        env.registry.register(SESSION, 9100)

        async def upstream(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers=[
                    ("set-cookie", "sid=1; Domain=niuu.world; Path=/; Secure; HttpOnly"),
                    ("set-cookie", "theme=dark; Path=/; SameSite=Lax"),
                    ("set-cookie", "t=3; DOMAIN=.niuu.world; Max-Age=60"),
                    ("x-app", "yes"),
                ],
                stream=_Body(b"x"),
            )

        env.upstream_handler = upstream
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.headers.get_list("set-cookie") == [
            "sid=1; Path=/; Secure; HttpOnly",
            "theme=dark; Path=/; SameSite=Lax",
            "t=3; Max-Age=60",
        ]
        assert response.headers["x-app"] == "yes"

    async def test_an_unreachable_skuld_is_502(self, env) -> None:
        env.registry.register(SESSION, 9100)

        async def upstream(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        env.upstream_handler = upstream
        async with env.client() as client:
            response = await client.get("/", headers=OWNER_HEADERS)

        assert response.status_code == 502
        assert "not reachable yet" in response.text

    async def test_a_remote_session_goes_through_its_service_host(self, env) -> None:
        async def resolver(session_id: str) -> SessionProxyTarget | None:
            return SessionProxyTarget(
                service_url=f"http://skuld-{session_id}.ns.svc.cluster.local:8080/",
                connect_host="10.1.2.3",
                connect_port=8080,
            )

        env.registry.set_target_resolver(resolver)
        async with env.client() as client:
            await client.get("/app?q=1", headers=OWNER_HEADERS)

        (request,) = env.upstream
        assert str(request.url) == "http://10.1.2.3:8080/preview/3000/app?q=1"
        assert request.headers["host"] == f"skuld-{SESSION}.ns.svc.cluster.local:8080"
        assert request.headers["authorization"] == f"Bearer {CREDENTIAL}"

    async def test_aclose_closes_the_http_client(self, env) -> None:
        await env.router.aclose()
        assert env.router._client.is_closed


# --------------------------------------------------------------------------- websocket


class _WsEnv:
    """Drive a WebSocket scope straight into the ASGI app."""

    def __init__(self, env: _Env) -> None:
        self.env = env
        self.inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.event = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        return await self.inbox.get()

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        self.event.set()

    async def wait_for(self, kind: str, count: int = 1) -> None:
        async with asyncio.timeout(5):
            while sum(1 for m in self.sent if m["type"] == kind) < count:
                self.event.clear()
                await self.event.wait()

    def scope(
        self,
        *,
        host: str = HOST,
        path: str = "/@vite/client",
        query: bytes = b"t=1",
        headers: dict[str, str] | None = None,
        subprotocols: list[str] | None = None,
    ) -> dict[str, Any]:
        merged = {"host": host, **OWNER_HEADERS, **(headers or {})}
        return {
            "type": "websocket",
            "asgi": {"version": "3.0"},
            "scheme": "wss",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "root_path": "",
            "headers": [(k.encode(), v.encode()) for k, v in merged.items()],
            "subprotocols": subprotocols or [],
            "client": ("203.0.113.9", 4444),
            "server": ("preview", 443),
        }

    async def run(self, scope: dict[str, Any]) -> None:
        await self.env.app(scope, self.receive, self.send)


class TestRouterWebsocketRefusals:
    @pytest.mark.parametrize(
        ("mutate", "reason"),
        [
            ("anonymous", "Sign in to open this preview."),
            ("not_owner_of_session", "belongs to a session you do not own"),
            ("viewer", "Only the session's owner"),
            ("no_target", "This session is not running."),
        ],
    )
    async def test_refusals_close_1008_before_accept(self, env, mutate, reason) -> None:
        headers: dict[str, str] | None = None
        if mutate != "no_target":
            env.registry.register(SESSION, 9100)
        if mutate == "anonymous":
            env.app.state.identity = EnvoyHeaderAuthenticationAdapter(user_id_header="x-nope")
        if mutate == "not_owner_of_session":
            env.attach_allowed = False
        if mutate == "viewer":
            env.role = "viewer"
        ws = _WsEnv(env)
        await ws.run(ws.scope(headers=headers))

        (message,) = ws.sent
        assert message["type"] == "websocket.close"
        assert message["code"] == 1008
        assert reason in message["reason"]
        assert env.issuer_calls == []

    async def test_router_not_wired_closes_1008(self, tmp_path) -> None:
        env = _Env(tmp_path, provider_ready=False)
        ws = _WsEnv(env)
        await ws.run(ws.scope())

        (message,) = ws.sent
        assert message["type"] == "websocket.close"
        assert message["code"] == 1008
        assert "starting" in message["reason"]


@pytest.fixture
async def skuld_ws():
    """A local WebSocket server standing in for the session's Skuld."""
    handshakes: list[dict[str, Any]] = []

    async def handler(connection: ServerConnection) -> None:
        handshakes.append(
            {
                "path": connection.request.path,
                "headers": connection.request.headers,
                "subprotocol": connection.subprotocol,
            }
        )
        try:
            async for message in connection:
                await connection.send(message)
        except ConnectionClosed:
            return

    def select_subprotocol(connection: ServerConnection, offered: list[str]) -> str | None:
        return "vite-hmr" if "vite-hmr" in offered else None

    async with serve(handler, "127.0.0.1", 0, select_subprotocol=select_subprotocol) as server:
        yield server.sockets[0].getsockname()[1], handshakes


class TestRouterWebsocketBridge:
    async def test_frames_subprotocol_and_headers(self, env, skuld_ws) -> None:
        port, handshakes = skuld_ws
        env.registry.register(SESSION, port)
        ws = _WsEnv(env)
        scope = ws.scope(
            headers={
                "authorization": "Bearer browser-access-token",
                "cookie": "session=secret",
                "origin": f"https://{HOST}",
                "sec-websocket-key": "abc",
                "x-envoy-peer": "p",
            },
            subprotocols=["vite-hmr"],
        )
        ws.inbox.put_nowait({"type": "websocket.connect"})
        task = asyncio.create_task(ws.run(scope))
        await ws.wait_for("websocket.accept")
        ws.inbox.put_nowait({"type": "websocket.receive", "text": "hot update"})
        ws.inbox.put_nowait({"type": "websocket.receive", "bytes": b"\x01\x02\x03"})
        await ws.wait_for("websocket.send", count=2)
        ws.inbox.put_nowait({"type": "websocket.disconnect", "code": 1000})
        await asyncio.wait_for(task, 5)

        assert ws.sent[0] == {"type": "websocket.accept", "subprotocol": "vite-hmr"}
        assert ws.sent[1:3] == [
            {"type": "websocket.send", "text": "hot update"},
            {"type": "websocket.send", "bytes": b"\x01\x02\x03"},
        ]
        (handshake,) = handshakes
        assert handshake["path"] == "/preview/3000/@vite/client?t=1"
        assert handshake["subprotocol"] == "vite-hmr"
        headers = handshake["headers"]
        assert headers.get_all("authorization") == [f"Bearer {CREDENTIAL}"]
        assert "cookie" not in headers
        assert "x-auth-user-id" not in headers
        assert "x-envoy-peer" not in headers
        assert headers["x-forwarded-host"] == HOST
        assert headers["x-forwarded-proto"] == "https"
        assert headers["origin"] == f"https://{HOST}"

    async def test_a_dead_skuld_closes_the_browser_leg_1011(self, env) -> None:
        env.registry.register(SESSION, 1)  # nothing listens on port 1
        ws = _WsEnv(env)
        ws.inbox.put_nowait({"type": "websocket.connect"})
        await ws.run(ws.scope(query=b""))

        assert ws.sent == [{"type": "websocket.close", "code": 1011}]

    async def test_a_remote_session_dials_the_gateway_with_its_service_host(
        self, env, monkeypatch
    ) -> None:
        async def resolver(session_id: str) -> SessionProxyTarget | None:
            return SessionProxyTarget(
                service_url="http://skuld-x.ns.svc.cluster.local:8080/",
                connect_host="10.1.2.3",
                connect_port=8080,
            )

        env.registry.set_target_resolver(resolver)
        dialled: dict[str, Any] = {}

        async def fake_bridge(scope, receive, send, **kwargs) -> None:
            dialled.update(kwargs)

        monkeypatch.setattr("niuu.preview_proxy.bridge_websocket", fake_bridge)
        ws = _WsEnv(env)
        await ws.run(ws.scope(query=b"a=b"))

        assert (
            dialled["url"] == "ws://skuld-x.ns.svc.cluster.local:8080/preview/3000/@vite/client?a=b"
        )
        assert dialled["connect_kwargs"] == {"host": "10.1.2.3", "port": 8080}
        assert dialled["open_timeout_seconds"] == env.config.upstream_connect_timeout_seconds
