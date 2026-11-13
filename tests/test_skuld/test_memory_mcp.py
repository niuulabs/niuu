"""The built-in ``memory`` MCP server: settings, injection, broker routes and stdio profile.

The gateway is a fake served through ``httpx.MockTransport`` (broker side) or a loopback
HTTP server standing in for the broker (stdio side). Nothing here starts a model.
"""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import ValidationError

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.forge_mcp.stdio import (
    INTERNAL_ERROR,
    BrokerClient,
    StdioServer,
    main,
)
from skuld.forge_mcp.stdio import MEMORY_PROFILE as STDIO_MEMORY_PROFILE
from skuld.memory_mcp.gateway import ContextPack, MemoryContextPackError, MemoryGatewayClient
from skuld.memory_mcp.routes import register_memory_mcp_routes
from skuld.transports.session_tools import claude_cli_args

SESSION_ID = "0b6c1d7e-3f0a-4a8e-9a57-6a9c2f1d0e11"
GATEWAY = "http://gateway.test/api/v1/mimir"
FORGE_CREDENTIAL = "forge-session-credential-do-not-leak"
INSTRUCTIONS = "Your memory has layers. Read the entity card first."
TOOLS = [
    {"name": "memory_ask", "description": "Answer from every layer.", "inputSchema": {}},
    {"name": "memory_search", "description": "Ranked hits.", "inputSchema": {}},
]
SECRET = "loopback-secret"


def _settings(tmp_path: Path, **overrides: Any) -> SkuldSettings:
    memory = {"enabled": True, "gateway_url": GATEWAY, **overrides.pop("memory", {})}
    forge_mcp = {
        "runtime_dir": str(tmp_path / "rt"),
        "token": FORGE_CREDENTIAL,
        **overrides.pop("forge_mcp", {}),
    }
    (tmp_path / "ws").mkdir(parents=True, exist_ok=True)
    return SkuldSettings(
        session={
            "id": SESSION_ID,
            "workspace_dir": str(tmp_path / "ws"),
            "model": "m",
        },
        transport_adapter=overrides.pop(
            "transport_adapter", "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
        ),
        port=18765,
        host="0.0.0.0",
        memory=memory,
        forge_mcp=forge_mcp,
        **overrides,
    )


PACK_TEXT = "Entity: Checkout service. Open thread: retry policy undecided."


def _broker(tmp_path: Path, *, pack: bool = True, **overrides: Any) -> Broker:
    """A broker; by default the context pack startup() would have fetched is already in."""
    broker = Broker(settings=_settings(tmp_path, **overrides))
    if pack:
        broker._memory_context_pack = ContextPack(text=PACK_TEXT)
    return broker


# ------------------------------------------------------------------ settings


class TestSettings:
    def test_disabled_by_default_and_needs_nothing(self) -> None:
        settings = SkuldSettings()
        assert settings.memory.enabled is False
        assert settings.memory.gateway_url == ""
        assert settings.memory.server_name == "memory"

    def test_enabled_without_a_gateway_url_is_fatal_and_names_the_variable(self) -> None:
        with pytest.raises(ValidationError, match="SKULD__MEMORY__GATEWAY_URL"):
            SkuldSettings(memory={"enabled": True}, forge_mcp={"token": FORGE_CREDENTIAL})

    def test_enabled_without_a_forge_credential_is_fatal_and_names_the_remedy(self) -> None:
        with pytest.raises(ValidationError, match="SKULD__FORGE_MCP__TOKEN"):
            SkuldSettings(memory={"enabled": True, "gateway_url": GATEWAY})

    def test_the_credential_may_come_from_the_token_file(self, tmp_path: Path) -> None:
        token_file = tmp_path / "token"
        token_file.write_text(FORGE_CREDENTIAL + "\n")
        settings = SkuldSettings(
            memory={"enabled": True, "gateway_url": GATEWAY},
            forge_mcp={"token_file": str(token_file)},
        )
        assert settings.forge_mcp.token == FORGE_CREDENTIAL

    def test_an_unreadable_token_file_is_still_fatal(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="token_file"):
            SkuldSettings(
                memory={"enabled": True, "gateway_url": GATEWAY},
                forge_mcp={"token_file": str(tmp_path / "absent")},
            )

    def test_the_gateway_url_must_be_http(self) -> None:
        with pytest.raises(ValidationError, match="http"):
            SkuldSettings(
                memory={"enabled": True, "gateway_url": "gateway.test/mimir"},
                forge_mcp={"token": FORGE_CREDENTIAL},
            )

    def test_timeouts_must_nest_so_the_gateway_failure_reaches_the_model(self) -> None:
        with pytest.raises(ValidationError, match="gateway_request_timeout_s"):
            SkuldSettings(
                memory={
                    "enabled": True,
                    "gateway_url": GATEWAY,
                    "gateway_request_timeout_s": 200,
                    "broker_request_timeout_s": 150,
                },
                forge_mcp={"token": FORGE_CREDENTIAL},
            )
        with pytest.raises(ValidationError, match="broker_request_timeout_s"):
            SkuldSettings(
                memory={
                    "enabled": True,
                    "gateway_url": GATEWAY,
                    "broker_request_timeout_s": 150,
                    "tool_timeout_s": 100,
                },
                forge_mcp={"token": FORGE_CREDENTIAL},
            )

    def test_the_server_name_must_differ_from_the_forge_server(self) -> None:
        with pytest.raises(ValidationError, match="different names"):
            SkuldSettings(
                memory={"enabled": True, "gateway_url": GATEWAY, "server_name": "forge"},
                forge_mcp={"token": FORGE_CREDENTIAL},
            )

    def test_environment_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKULD__MEMORY__ENABLED", "true")
        monkeypatch.setenv("SKULD__MEMORY__GATEWAY_URL", GATEWAY)
        monkeypatch.setenv("SKULD__FORGE_MCP__TOKEN", FORGE_CREDENTIAL)
        settings = SkuldSettings()
        assert settings.memory.enabled is True
        assert settings.memory.gateway_url == GATEWAY

    def test_disabled_ignores_an_empty_url(self) -> None:
        assert SkuldSettings(memory={"enabled": False}).memory.gateway_url == ""


# ------------------------------------------------------------------ injection


def _server(servers: list[dict[str, Any]], name: str) -> dict[str, Any]:
    [found] = [server for server in servers if server["name"] == name]
    return found


class TestInjection:
    def test_memory_is_added_beside_forge_only_when_enabled(self, tmp_path: Path) -> None:
        enabled = _broker(tmp_path)
        servers = enabled._build_transport_kwargs()["mcp_servers"]
        assert [server["name"] for server in servers] == ["forge", "memory"]
        assert enabled._effective_mcp_server_names() == ["forge", "memory"]

        disabled = _broker(tmp_path / "off", memory={"enabled": False, "gateway_url": ""})
        off_servers = disabled._build_transport_kwargs()["mcp_servers"]
        assert [server["name"] for server in off_servers] == ["forge"]

    def test_the_forge_server_is_unchanged_by_memory(self, tmp_path: Path) -> None:
        with_memory = _server(
            _broker(tmp_path / "a")._build_transport_kwargs()["mcp_servers"], "forge"
        )
        without = _server(
            _broker(
                tmp_path / "b", memory={"enabled": False, "gateway_url": ""}
            )._build_transport_kwargs()["mcp_servers"],
            "forge",
        )
        assert with_memory["args"][:3] == ["-P", "-m", "skuld.forge_mcp"]
        assert "--server" not in with_memory["args"]
        assert {k: v for k, v in with_memory.items() if k != "args"} == {
            k: v for k, v in without.items() if k != "args"
        }
        # The runtime dir differs per broker above; compare shape, not paths.
        assert [a for a in with_memory["args"] if not a.startswith("/")] == [
            a for a in without["args"] if not a.startswith("/")
        ]

    def test_the_memory_server_is_the_shared_stdio_process_with_approval(
        self, tmp_path: Path
    ) -> None:
        broker = _broker(tmp_path)
        memory = _server(broker._build_transport_kwargs()["mcp_servers"], "memory")
        args = memory["args"]
        assert memory["type"] == "stdio" and memory["command"] == sys.executable
        assert args[:3] == ["-P", "-m", "skuld.forge_mcp"]
        assert args[args.index("--server") + 1] == "memory"
        assert args[args.index("--broker-url") + 1] == "http://127.0.0.1:18765"
        assert Path(args[args.index("--token-file") + 1]) == tmp_path / "rt" / "forge-mcp.token"
        assert args[args.index("--timeout") + 1] == "150.0"
        assert memory["default_tools_approval_mode"] == "approve"
        assert memory["startup_timeout_sec"] == 20.0
        assert memory["tool_timeout_sec"] == 180.0

    def test_the_credential_is_in_no_arg_and_no_env_of_the_server(self, tmp_path: Path) -> None:
        broker = _broker(tmp_path)
        memory = _server(broker._build_transport_kwargs()["mcp_servers"], "memory")
        assert FORGE_CREDENTIAL not in json.dumps(memory)
        assert "env" not in memory

    def test_the_credential_never_reaches_the_agent_process_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKULD__FORGE_MCP__TOKEN", FORGE_CREDENTIAL)
        broker = _broker(tmp_path)
        env = broker._create_transport()._spawn_env()
        assert FORGE_CREDENTIAL not in json.dumps(env)
        assert not any(name.endswith("FORGE_MCP__TOKEN") for name in env)

    def test_the_frozen_binary_runs_the_same_subcommand(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        memory = _server(_broker(tmp_path)._build_transport_kwargs()["mcp_servers"], "memory")
        assert memory["args"][:3] == ["platform", "forge-mcp", "--server"]
        assert memory["args"][3] == "memory"

    def test_memory_alone_when_forge_mcp_is_off(self, tmp_path: Path) -> None:
        broker = _broker(tmp_path, forge_mcp={"enabled": False})
        assert broker._effective_mcp_server_names() == ["memory"]

    def test_a_launch_spec_entry_of_the_same_name_replaces_the_builtin(
        self, tmp_path: Path
    ) -> None:
        custom = {"name": "memory", "type": "http", "url": "https://memory.example/mcp"}
        broker = _broker(tmp_path, mcp_servers=[custom])
        servers = broker._build_transport_kwargs()["mcp_servers"]
        assert custom in servers
        assert [server["name"] for server in servers] == ["memory", "forge"]

    def test_claude_gets_the_memory_server_in_its_mcp_config(self, tmp_path: Path) -> None:
        broker = _broker(tmp_path)
        argv = broker._create_transport()._interactive_argv()
        config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text())
        memory = config["mcpServers"]["memory"]
        assert memory["type"] == "stdio"
        assert "--server" in memory["args"] and "memory" in memory["args"]
        assert FORGE_CREDENTIAL not in json.dumps(config)

    async def test_codex_gets_the_memory_server_with_approval(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        broker = _broker(
            tmp_path, transport_adapter="skuld.transports.codex_ws.CodexWebSocketTransport"
        )
        transport = broker._create_transport()
        transport._process_owner = None
        process = MagicMock(stdout=None, stderr=None, pid=4)
        with (
            patch(
                "skuld.transports.codex_ws.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as spawn,
            patch("skuld.transports.codex_ws.resolve_codex_cli", return_value="codex"),
            patch("skuld.transports.codex_ws._drain_stream", new=AsyncMock()),
        ):
            await transport._spawn_app_server()
        argv = list(spawn.call_args.args)
        overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
        assert 'mcp_servers.memory.default_tools_approval_mode="approve"' in overrides
        assert f'mcp_servers.memory.command="{sys.executable}"' in overrides
        assert 'mcp_servers.forge.default_tools_approval_mode="approve"' in overrides
        assert FORGE_CREDENTIAL not in json.dumps(overrides)
        assert FORGE_CREDENTIAL not in json.dumps(spawn.call_args.kwargs["env"])


# ------------------------------------------------------------------ broker routes


class FakeGateway:
    """The memory gateway (contract C2), recording what the broker sent."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.listing: Callable[[httpx.Request], httpx.Response] = lambda _r: httpx.Response(
            200, json={"instructions": INSTRUCTIONS, "tools": TOOLS}
        )
        self.call: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"echo": json.loads(request.content)},
                "isError": False,
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/v1/mimir/memory/tools" and request.method == "GET":
            return self.listing(request)
        if request.url.path.startswith("/api/v1/mimir/memory/tools/") and request.method == "POST":
            return self.call(request)
        return httpx.Response(404, json={"detail": "no route"})


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


def _routes_broker(tmp_path: Path, gateway: FakeGateway, **overrides: Any) -> Broker:
    broker = _broker(tmp_path, **overrides)
    broker._memory_gateway_cache = MemoryGatewayClient(
        base_url=GATEWAY,
        token=FORGE_CREDENTIAL,
        timeout_s=5,
        transport=httpx.MockTransport(gateway.handler),
    )
    return broker


def _app(broker: Broker) -> FastAPI:
    app = FastAPI()

    def guard(request: Request) -> None:
        if not broker._session_runtime.accepts(request.headers.get("authorization")):
            raise HTTPException(401)

    register_memory_mcp_routes(app, lambda: broker, dependencies=[Depends(guard)])
    return app


def _client(broker: Broker, *, authorized: bool = True) -> httpx.AsyncClient:
    headers = {"authorization": broker._session_runtime.authorization} if authorized else {}
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(broker)), base_url="http://broker", headers=headers
    )


class TestRoutes:
    async def test_listing_returns_the_gateways_instructions_and_tools(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            response = await client.get("/api/memory-mcp/tools")
        assert response.status_code == 200
        assert response.json() == {"instructions": INSTRUCTIONS, "tools": TOOLS}
        [request] = gateway.requests
        assert str(request.url) == f"{GATEWAY}/memory/tools"
        assert request.headers["authorization"] == f"Bearer {FORGE_CREDENTIAL}"

    async def test_a_call_forwards_the_arguments_with_the_session_credential(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            response = await client.post(
                "/api/memory-mcp/tools/memory_ask", json={"question": "who owns billing?"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["isError"] is False
        assert body["structuredContent"]["echo"] == {"question": "who owns billing?"}
        [request] = gateway.requests
        assert request.method == "POST"
        assert str(request.url) == f"{GATEWAY}/memory/tools/memory_ask"
        assert request.headers["authorization"] == f"Bearer {FORGE_CREDENTIAL}"

    async def test_the_credential_is_never_in_what_the_agent_side_receives(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            listing = await client.get("/api/memory-mcp/tools")
            call = await client.post("/api/memory-mcp/tools/memory_ask", json={})
        assert FORGE_CREDENTIAL not in listing.text + call.text
        assert "authorization" not in {key.lower() for key in call.headers}

    async def test_a_rule_refusal_passes_through_as_the_gateways_error_result(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        refusal = {
            "content": [{"type": "text", "text": "rule write_prefixes: decisions/ only"}],
            "isError": True,
        }
        gateway.call = lambda _r: httpx.Response(200, json=refusal)
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            response = await client.post("/api/memory-mcp/tools/memory_write_page", json={})
        assert response.status_code == 200
        assert response.json() == refusal

    async def test_the_loopback_secret_is_required(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker, authorized=False) as client:
            assert (await client.get("/api/memory-mcp/tools")).status_code == 401
            assert (
                await client.post("/api/memory-mcp/tools/memory_ask", json={})
            ).status_code == 401
        assert gateway.requests == []

    async def test_disabled_memory_serves_nothing(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway, memory={"enabled": False, "gateway_url": ""})
        async with _client(broker) as client:
            assert (await client.get("/api/memory-mcp/tools")).status_code == 404
            assert (
                await client.post("/api/memory-mcp/tools/memory_ask", json={})
            ).status_code == 404
        assert gateway.requests == []

    @pytest.mark.parametrize("status", [401, 403])
    async def test_a_refused_credential_is_a_clear_tool_error(
        self, tmp_path: Path, gateway: FakeGateway, status: int
    ) -> None:
        gateway.call = lambda _r: httpx.Response(status, json={"detail": "scope missing"})
        gateway.listing = lambda _r: httpx.Response(status, json={"detail": "scope missing"})
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            call = await client.post("/api/memory-mcp/tools/memory_ask", json={})
            listing = await client.get("/api/memory-mcp/tools")
        body = call.json()
        assert body["isError"] is True
        text = body["content"][0]["text"]
        assert "refused this session's memory credential" in text
        assert GATEWAY in text and str(status) in text and "scope missing" in text
        assert FORGE_CREDENTIAL not in text
        assert listing.status_code == 502
        assert "refused this session's memory credential" in listing.json()["detail"]

    async def test_an_unreachable_gateway_names_its_url_and_is_never_an_empty_list(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        def down(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        gateway.call = down
        gateway.listing = down
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            call = await client.post("/api/memory-mcp/tools/memory_ask", json={})
            listing = await client.get("/api/memory-mcp/tools")
        assert call.json()["isError"] is True
        assert GATEWAY in call.json()["content"][0]["text"]
        # The transport's own error text stays in the broker log, not in the answer.
        assert "could not be reached" in call.json()["content"][0]["text"]
        assert "connection refused" not in call.json()["content"][0]["text"]
        assert listing.status_code == 502
        assert GATEWAY in listing.json()["detail"]
        assert "tools" not in listing.json()

    async def test_a_gateway_timeout_names_the_url(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        def slow(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        gateway.call = slow
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            call = await client.post("/api/memory-mcp/tools/memory_ask", json={})
        assert call.json()["isError"] is True
        assert GATEWAY in call.json()["content"][0]["text"]

    async def test_a_gateway_server_error_is_a_tool_error_with_its_status(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        gateway.call = lambda _r: httpx.Response(502, json={"detail": "layer org unreachable"})
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            call = await client.post("/api/memory-mcp/tools/memory_ask", json={})
        text = call.json()["content"][0]["text"]
        assert call.json()["isError"] is True
        assert "502" in text and "layer org unreachable" in text and GATEWAY in text

    async def test_an_unknown_tool_is_a_404(self, tmp_path: Path, gateway: FakeGateway) -> None:
        gateway.call = lambda _r: httpx.Response(404, json={"detail": "nope"})
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            response = await client.post("/api/memory-mcp/tools/memory_nope", json={})
        assert response.status_code == 404
        assert "memory_nope" in response.json()["detail"]

    async def test_a_listing_without_instructions_or_tools_fails(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            gateway.listing = lambda _r: httpx.Response(200, json={"tools": TOOLS})
            no_instructions = await client.get("/api/memory-mcp/tools")
            gateway.listing = lambda _r: httpx.Response(
                200, json={"instructions": INSTRUCTIONS, "tools": []}
            )
            no_tools = await client.get("/api/memory-mcp/tools")
            gateway.listing = lambda _r: httpx.Response(200, text="<html>not json</html>")
            not_json = await client.get("/api/memory-mcp/tools")
        assert no_instructions.status_code == 502
        assert "without instructions" in no_instructions.json()["detail"]
        assert no_tools.status_code == 502
        assert "no tools" in no_tools.json()["detail"]
        assert not_json.status_code == 502
        assert "invalid JSON" in not_json.json()["detail"]

    async def test_a_malformed_call_result_is_an_error_not_an_empty_success(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            gateway.call = lambda _r: httpx.Response(200, json={"isError": False})
            no_content = await client.post("/api/memory-mcp/tools/memory_ask", json={})
            gateway.call = lambda _r: httpx.Response(200, json=["x"])
            not_object = await client.post("/api/memory-mcp/tools/memory_ask", json={})
        for response in (no_content, not_object):
            assert response.json()["isError"] is True
            assert GATEWAY in response.json()["content"][0]["text"]

    async def test_bad_arguments_are_a_400_and_never_reach_the_gateway(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            garbled = await client.post("/api/memory-mcp/tools/memory_ask", content=b"{nope")
            listy = await client.post("/api/memory-mcp/tools/memory_ask", json=[1])
            empty = await client.post("/api/memory-mcp/tools/memory_ask")
        assert garbled.status_code == 400 and listy.status_code == 400
        assert empty.status_code == 200  # no body is no arguments
        assert len(gateway.requests) == 1

    async def test_the_gateway_client_is_closed_with_the_broker(
        self, tmp_path: Path, gateway: FakeGateway
    ) -> None:
        broker = _routes_broker(tmp_path, gateway)
        async with _client(broker) as client:
            await client.get("/api/memory-mcp/tools")
        cached = broker._memory_gateway_cache
        assert cached is not None and cached._client is not None
        await broker._close_memory_gateway()
        assert broker._memory_gateway_cache is None
        assert cached._client is None

    def test_the_broker_builds_its_gateway_client_from_settings(self, tmp_path: Path) -> None:
        broker = _broker(tmp_path, memory={"gateway_url": GATEWAY + "/"})
        client = broker._memory_gateway()
        assert client.base_url == GATEWAY
        assert broker._memory_gateway() is client


# ------------------------------------------------------------------ context pack at start

PACK_PATH = "/api/v1/mimir/memory/context-pack"


def _pack_body(**overrides: Any) -> dict[str, Any]:
    return {"text": PACK_TEXT, **overrides}


class PackGateway:
    """A fake gateway serving only the context pack (and the tools listing)."""

    def __init__(self, response: Callable[[httpx.Request], httpx.Response] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self.timeouts: list[Any] = []
        self.response = response or (lambda _r: httpx.Response(200, json=_pack_body()))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.timeouts.append(request.extensions.get("timeout"))
        if request.url.path == PACK_PATH and request.method == "GET":
            return self.response(request)
        return httpx.Response(404, json={"detail": "no route"})

    @property
    def pack_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == PACK_PATH]


def _unfetched(tmp_path: Path, fake: PackGateway, **overrides: Any) -> Broker:
    """A broker whose pack must come from ``fake`` through ``startup()``."""
    broker = _broker(tmp_path, pack=False, **overrides)
    broker._memory_gateway_cache = MemoryGatewayClient(
        base_url=GATEWAY,
        token=FORGE_CREDENTIAL,
        timeout_s=5,
        transport=httpx.MockTransport(fake.handler),
    )
    return broker


def _claude_prompt(broker: Broker) -> str:
    argv = broker._create_transport()._interactive_argv()
    return argv[argv.index("--append-system-prompt") + 1]


class TestContextPackAtStart:
    def test_the_pack_timeout_is_a_knob_and_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            SkuldSettings(
                memory={
                    "enabled": True,
                    "gateway_url": GATEWAY,
                    "context_pack_timeout_s": 0,
                },
                forge_mcp={"token": FORGE_CREDENTIAL},
            )

    async def test_startup_fetches_the_pack_with_the_session_credential(
        self, tmp_path: Path
    ) -> None:
        fake = PackGateway()
        broker = _unfetched(tmp_path, fake, memory={"context_pack_timeout_s": 7.5})
        await broker.startup()
        try:
            [request] = fake.pack_requests
            assert request.headers["authorization"] == f"Bearer {FORGE_CREDENTIAL}"
            assert fake.timeouts[0]["read"] == 7.5
            assert broker._transport is not None
            assert broker.startup_status()["startup_state"] == "ready"
        finally:
            await broker.shutdown()

    async def test_the_pack_reaches_claude_under_a_heading(self, tmp_path: Path) -> None:
        fake = PackGateway()
        broker = _unfetched(tmp_path, fake)
        await broker.startup()
        try:
            prompt = _claude_prompt(broker)
            assert "## Memory context at launch" in prompt
            assert PACK_TEXT in prompt
            assert prompt.index("## Memory context at launch") < prompt.index(PACK_TEXT)
            assert "present-file" in prompt  # the other capability text is still there
        finally:
            await broker.shutdown()

    async def test_a_resume_reuses_the_same_text_without_asking_again(self, tmp_path: Path) -> None:
        fake = PackGateway()
        broker = _unfetched(tmp_path, fake)
        await broker.startup()
        try:
            first = _claude_prompt(broker)
            # The gateway now says something else; a resumed harness must not see it.
            fake.response = lambda _r: httpx.Response(200, json=_pack_body(text="a different pack"))
            await broker._load_memory_context_pack()
            resumed = claude_cli_args(
                broker._session_tools(),
                mcp_servers=[],
                legacy_mcp_config=None,
                system_prompt="first run only",
                include_system_prompt=False,
            )
            prompt = resumed[resumed.index("--append-system-prompt") + 1]
            assert PACK_TEXT in prompt and "a different pack" not in prompt
            assert "first run only" not in prompt
            assert _claude_prompt(broker) == first
            assert len(fake.pack_requests) == 1
        finally:
            await broker.shutdown()

    async def test_the_pack_reaches_codex_app_server_developer_instructions(
        self, tmp_path: Path
    ) -> None:
        fake = PackGateway()
        broker = _unfetched(
            tmp_path, fake, transport_adapter="skuld.transports.codex_ws.CodexWebSocketTransport"
        )
        await broker.startup()
        try:
            transport = broker._transport
            assert transport is not None
            instructions = transport._developer_instructions()
            assert "## Memory context at launch" in instructions
            assert PACK_TEXT in instructions
            rpc = AsyncMock(side_effect=[{"userAgent": "codex"}, {}, {"thread": {"id": "t-1"}}])
            transport._send_rpc = rpc
            transport._send_notification = AsyncMock()
            transport._authenticate_codex = AsyncMock()
            transport.on_event(AsyncMock())
            await transport._handshake()
            thread = rpc.await_args_list[-1].args[1]
            assert PACK_TEXT in thread["developerInstructions"]
        finally:
            await broker.shutdown()

    async def test_the_pack_reaches_codex_exec_developer_instructions(self, tmp_path: Path) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        fake = PackGateway()
        broker = _unfetched(
            tmp_path, fake, transport_adapter="skuld.transports.codex.CodexSubprocessTransport"
        )
        await broker.startup()
        try:
            transport = broker._transport
            assert transport is not None
            await transport.start()
            process = MagicMock(stdout=None, stderr=None, returncode=0)
            with patch(
                "skuld.transports.codex.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=process,
            ) as spawn:
                try:
                    await transport.send_message("hi")
                except Exception:  # noqa: BLE001 - the fake process has no stdout to parse
                    pass
            argv = list(spawn.call_args.args)
            overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
            [instructions] = [o for o in overrides if o.startswith("developer_instructions=")]
            assert PACK_TEXT in instructions
            assert FORGE_CREDENTIAL not in json.dumps(argv)
            assert FORGE_CREDENTIAL not in json.dumps(spawn.call_args.kwargs["env"])
        finally:
            await broker.shutdown()

    async def test_the_credential_is_in_neither_claude_args_nor_the_agent_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKULD__FORGE_MCP__TOKEN", FORGE_CREDENTIAL)
        fake = PackGateway()
        broker = _unfetched(tmp_path, fake)
        await broker.startup()
        try:
            transport = broker._transport
            assert transport is not None
            assert FORGE_CREDENTIAL not in json.dumps(transport._interactive_argv())
            assert FORGE_CREDENTIAL not in json.dumps(transport._spawn_env())
            assert FORGE_CREDENTIAL not in _claude_prompt(broker)
        finally:
            await broker.shutdown()

    @pytest.mark.parametrize(
        ("response", "reason"),
        [
            (lambda _r: httpx.Response(500, json={"detail": "entity store down"}), "entity store"),
            (lambda _r: httpx.Response(403, json={"detail": "no forge:memory"}), "refused"),
            (lambda _r: httpx.Response(200, text="<html>"), "invalid JSON"),
            (lambda _r: httpx.Response(200, json={}), "text"),
            (lambda _r: httpx.Response(200, json=_pack_body(text=None)), "text"),
        ],
    )
    async def test_a_gateway_failure_stops_startup_naming_the_url_and_the_reason(
        self,
        tmp_path: Path,
        response: Callable[[httpx.Request], httpx.Response],
        reason: str,
    ) -> None:
        fake = PackGateway(response)
        broker = _unfetched(tmp_path, fake)
        await broker.startup()
        status = broker.startup_status()
        assert status["ready"] is False
        assert status["startup_state"] == "failed"
        assert "MemoryContextPackError" in status["error"]
        assert f"{GATEWAY}/memory/context-pack" in status["error"]
        assert reason in status["error"]
        assert broker._transport is None
        assert broker._memory_context_pack is None

    async def test_an_unreachable_gateway_stops_startup(self, tmp_path: Path) -> None:
        def refuse(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        broker = _unfetched(tmp_path, PackGateway(refuse))
        await broker.startup()
        status = broker.startup_status()
        assert status["startup_state"] == "failed"
        assert GATEWAY in status["error"] and "connection refused" in status["error"]
        assert broker._transport is None

    async def test_a_timeout_stops_startup_naming_the_url(self, tmp_path: Path) -> None:
        def slow(_request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("took too long")

        broker = _unfetched(tmp_path, PackGateway(slow))
        await broker.startup()
        status = broker.startup_status()
        assert status["startup_state"] == "failed"
        assert GATEWAY in status["error"] and "ReadTimeout" in status["error"]

    async def test_a_failed_pack_is_reported_to_forge_as_a_failed_start(
        self, tmp_path: Path
    ) -> None:
        import asyncio

        broker = _unfetched(
            tmp_path, PackGateway(lambda _r: httpx.Response(502, json={"detail": "bad gateway"}))
        )
        broker.volundr_api_url = "http://forge.test"
        broker._post_activity_payload = AsyncMock(return_value=True)  # type: ignore[method-assign]
        await broker.startup()
        for _ in range(5):
            await asyncio.sleep(0)
        reports = [call.args[0] for call in broker._post_activity_payload.await_args_list]
        [failed] = [report for report in reports if report["state"] == "error"]
        assert failed["metadata"]["startup_failed"] is True
        assert "context-pack" in failed["metadata"]["error"]

    async def test_an_empty_text_is_a_real_pack_and_adds_no_heading(self, tmp_path: Path) -> None:
        fake = PackGateway(lambda _r: httpx.Response(200, json=_pack_body(text="")))
        broker = _unfetched(tmp_path, fake)
        await broker.startup()
        try:
            assert broker.startup_status()["startup_state"] == "ready"
            assert broker._memory_context_pack is not None
            assert broker._memory_context_pack.text == ""
            assert "## Memory context at launch" not in _claude_prompt(broker)
        finally:
            await broker.shutdown()

    async def test_memory_disabled_fetches_nothing_and_adds_no_heading(
        self, tmp_path: Path
    ) -> None:
        fake = PackGateway()
        broker = _unfetched(tmp_path, fake, memory={"enabled": False, "gateway_url": ""})
        await broker.startup()
        try:
            assert fake.requests == []
            assert "Memory context" not in _claude_prompt(broker)
        finally:
            await broker.shutdown()

    def test_building_the_tools_before_the_pack_is_fetched_is_a_bug_not_an_empty_pack(
        self, tmp_path: Path
    ) -> None:
        broker = _broker(tmp_path, pack=False)
        with pytest.raises(RuntimeError, match="context pack was not fetched"):
            broker._session_tools()

    async def test_the_error_type_is_the_one_startup_catches(self, tmp_path: Path) -> None:
        broker = _unfetched(tmp_path, PackGateway(lambda _r: httpx.Response(500, text="boom")))
        with pytest.raises(MemoryContextPackError, match="Fix the memory gateway"):
            await broker._load_memory_context_pack()


# ------------------------------------------------------------------ stdio profile


class FakeBroker:
    """Serves /api/memory-mcp/tools like the broker, checking the loopback secret."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], str | None]] = []
        self.listing: tuple[int, Any] = (200, {"instructions": INSTRUCTIONS, "tools": TOOLS})
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                broker.calls.append(("GET", self.path, {}, self.headers.get("authorization")))
                if self.path != "/api/memory-mcp/tools":
                    self._send(404, {"detail": "wrong path"})
                    return
                self._send(*broker.listing)

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                arguments = json.loads(self.rfile.read(length) or b"{}")
                broker.calls.append(
                    ("POST", self.path, arguments, self.headers.get("authorization"))
                )
                name = self.path.rsplit("/", 1)[-1]
                if not self.path.startswith("/api/memory-mcp/tools/"):
                    self._send(404, {"detail": "wrong path"})
                    return
                if name != "memory_ask":
                    self._send(404, {"detail": f"unknown tool: {name}"})
                    return
                self._send(
                    200,
                    {
                        "content": [{"type": "text", "text": "answered"}],
                        "structuredContent": {"echo": arguments},
                        "isError": False,
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_broker() -> Iterator[FakeBroker]:
    broker = FakeBroker()
    yield broker
    broker.close()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "forge-mcp.token"
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    return path


def _serve(client: BrokerClient, *messages: object) -> list[Any]:
    stdin = io.BytesIO(b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    stdout = io.BytesIO()
    StdioServer(
        client, stdin=stdin, stdout=stdout, max_concurrency=2, profile=STDIO_MEMORY_PROFILE
    ).serve()
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _memory_client(fake: FakeBroker, token_file: Path) -> BrokerClient:
    return BrokerClient(fake.url, str(token_file), 5, tools_path="/api/memory-mcp/tools")


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
}


class TestStdioProfile:
    def test_initialize_serves_the_gateways_instructions_not_the_forge_ones(
        self, fake_broker: FakeBroker, token_file: Path
    ) -> None:
        [frame] = _serve(_memory_client(fake_broker, token_file), INIT)
        result = frame["result"]
        assert result["serverInfo"]["name"] == "memory"
        assert result["instructions"] == INSTRUCTIONS
        assert fake_broker.calls == [("GET", "/api/memory-mcp/tools", {}, f"Bearer {SECRET}")]

    def test_list_and_call_go_to_the_memory_routes_with_the_loopback_secret(
        self, fake_broker: FakeBroker, token_file: Path
    ) -> None:
        frames = _serve(
            _memory_client(fake_broker, token_file),
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "memory_ask", "arguments": {"question": "q"}},
            },
        )
        by_id = {frame["id"]: frame["result"] for frame in frames}
        assert by_id[2]["tools"] == TOOLS
        assert by_id[3]["isError"] is False
        assert by_id[3]["structuredContent"]["echo"] == {"question": "q"}
        assert (
            "POST",
            "/api/memory-mcp/tools/memory_ask",
            {"question": "q"},
            f"Bearer {SECRET}",
        ) in (fake_broker.calls)
        assert all(call[3] == f"Bearer {SECRET}" for call in fake_broker.calls)

    def test_initialize_fails_loudly_when_the_gateway_cannot_be_reached(
        self, fake_broker: FakeBroker, token_file: Path
    ) -> None:
        fake_broker.listing = (
            502,
            {"detail": f"the memory gateway at {GATEWAY} could not be reached"},
        )
        [frame] = _serve(_memory_client(fake_broker, token_file), INIT)
        assert "result" not in frame
        assert frame["error"]["code"] == INTERNAL_ERROR
        assert GATEWAY in frame["error"]["message"]

    def test_initialize_fails_when_the_listing_has_no_instructions(
        self, fake_broker: FakeBroker, token_file: Path
    ) -> None:
        for listing in ({"tools": TOOLS}, {"instructions": "  ", "tools": TOOLS}):
            fake_broker.listing = (200, listing)
            [frame] = _serve(_memory_client(fake_broker, token_file), INIT)
            assert frame["error"]["code"] == INTERNAL_ERROR
            assert "no instructions" in frame["error"]["message"]

    def test_initialize_fails_when_the_broker_is_down(self, token_file: Path) -> None:
        client = BrokerClient("http://127.0.0.1:9", str(token_file), 5, tools_path="/x")
        [frame] = _serve(client, INIT)
        assert frame["error"]["code"] == INTERNAL_ERROR
        assert "unreachable" in frame["error"]["message"]

    def test_a_failed_tool_call_is_a_tool_error_naming_the_memory_broker(
        self, token_file: Path
    ) -> None:
        down = BrokerClient("http://127.0.0.1:9", str(token_file), 5, tools_path="/x")
        [frame] = _serve(
            down,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory_ask", "arguments": {}},
            },
        )
        assert frame["result"]["isError"] is True
        assert frame["result"]["content"][0]["text"].startswith("The memory broker call failed")

    def test_an_unknown_tool_is_invalid_params(
        self, fake_broker: FakeBroker, token_file: Path
    ) -> None:
        [frame] = _serve(
            _memory_client(fake_broker, token_file),
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory_nope", "arguments": {}},
            },
        )
        assert "unknown tool: memory_nope" in frame["error"]["message"]

    def test_main_selects_the_memory_profile(
        self, fake_broker: FakeBroker, token_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(INIT).encode() + b"\n"))
        stdout = io.TextIOWrapper(io.BytesIO())
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", stdout)
        assert (
            main(
                [
                    "--server",
                    "memory",
                    "--broker-url",
                    fake_broker.url,
                    "--token-file",
                    str(token_file),
                    "--timeout",
                    "5",
                    "--max-concurrency",
                    "1",
                ]
            )
            == 0
        )
        stdout.flush()
        [frame] = [json.loads(line) for line in stdout.buffer.getvalue().splitlines()]
        assert frame["result"]["serverInfo"]["name"] == "memory"
        assert frame["result"]["instructions"] == INSTRUCTIONS

    def test_an_unknown_server_is_refused(self, token_file: Path) -> None:
        with pytest.raises(SystemExit):
            main(
                [
                    "--server",
                    "nope",
                    "--broker-url",
                    "http://127.0.0.1:9",
                    "--token-file",
                    str(token_file),
                    "--timeout",
                    "5",
                    "--max-concurrency",
                    "1",
                ]
            )


@pytest.mark.parametrize("name", ["../tools", "memory_ask/../x", "Memory_Ask", "a" * 65, ""])
async def test_a_tool_name_the_gateway_could_not_serve_is_refused_before_any_request(
    name: str,
) -> None:
    from skuld.memory_mcp.gateway import MemoryGatewayClient, UnknownMemoryToolError

    client = MemoryGatewayClient(
        base_url="http://gateway.invalid/api/v1/mimir", token="t", timeout_s=1.0
    )
    with pytest.raises(UnknownMemoryToolError):
        await client.call_tool(name, {})
