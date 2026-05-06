"""Unit tests for the MCP client adapter (NIU-430).

Tests cover:
- MCPLifecyclePhase and MCPServerState enums
- Protocol helpers (message construction, result parsing)
- Tool name normalisation and prefix generation
- MCPTool ToolPort implementation
- MCPServerClient lifecycle (connect, discover, call_tool, shutdown)
- MCPManager (multi-server startup, degraded mode, collision detection)
- Stdio transport error paths
- HTTP / SSE transport stubs
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ravn.adapters.mcp.client import (
    MCPServerClient,
    MCPServerHealth,
    make_tool_prefix,
    normalise_server_name,
)
from ravn.adapters.mcp.lifecycle import MCPLifecyclePhase, MCPServerState
from ravn.adapters.mcp.manager import MCPManager, _build_input_schema
from ravn.adapters.mcp.protocol import (
    MCPProtocolError,
    extract_result,
    make_initialize_request,
    make_initialized_notification,
    make_request,
    make_tool_call_request,
    make_tools_list_request,
    next_id,
    parse_tool_call_result,
    parse_tool_definitions,
)
from ravn.adapters.mcp.tool import MCPTool
from ravn.adapters.mcp.transport import MCPTransport, MCPTransportError
from ravn.config import MCPServerConfig
from ravn.domain.models import ToolResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeTransport(MCPTransport):
    """Controllable in-memory transport for tests."""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self._responses = iter(responses or [])
        self.started = False
        self.closed = False
        self.sent: list[dict[str, Any]] = []
        self._fail_start = False
        self._fail_send = False
        self._fail_receive = False

    async def start(self) -> None:
        if self._fail_start:
            raise MCPTransportError("start failed")
        self.started = True

    async def send(self, message: dict[str, Any]) -> None:
        if self._fail_send:
            raise MCPTransportError("send failed")
        self.sent.append(message)

    async def receive(self) -> dict[str, Any]:
        if self._fail_receive:
            raise MCPTransportError("receive failed")
        try:
            return next(self._responses)
        except StopIteration:
            raise MCPTransportError("no more responses")

    async def close(self) -> None:
        self.closed = True

    @property
    def is_alive(self) -> bool:
        return self.started and not self.closed


def _make_ok_response(request_id: int, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error_response(request_id: int, code: int, msg: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": msg}}


def _tool_def(name: str, description: str = "A tool") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    }


# ---------------------------------------------------------------------------
# Lifecycle enums
# ---------------------------------------------------------------------------


class TestLifecycleEnums:
    def test_lifecycle_phases_are_strings(self) -> None:
        assert MCPLifecyclePhase.CONFIG_LOAD == "config_load"
        assert MCPLifecyclePhase.READY == "ready"
        assert MCPLifecyclePhase.SHUTDOWN == "shutdown"

    def test_server_states_are_strings(self) -> None:
        assert MCPServerState.DISCONNECTED == "disconnected"
        assert MCPServerState.CONNECTED == "connected"
        assert MCPServerState.ERROR == "error"
        assert MCPServerState.AUTH_REQUIRED == "auth_required"
        assert MCPServerState.CONNECTING == "connecting"


# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------


class TestProtocolHelpers:
    def test_make_request_has_jsonrpc(self) -> None:
        req = make_request("ping")
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "ping"
        assert "id" in req

    def test_make_request_with_params(self) -> None:
        req = make_request("add", {"a": 1, "b": 2})
        assert req["params"] == {"a": 1, "b": 2}

    def test_make_request_without_params(self) -> None:
        req = make_request("ping")
        assert "params" not in req

    def test_next_id_increments(self) -> None:
        a = next_id()
        b = next_id()
        assert b > a

    def test_make_initialize_request(self) -> None:
        req = make_initialize_request()
        assert req["method"] == "initialize"
        assert req["params"]["clientInfo"]["name"] == "ravn"
        assert "protocolVersion" in req["params"]

    def test_make_initialized_notification_has_no_id(self) -> None:
        notif = make_initialized_notification()
        assert notif["method"] == "notifications/initialized"
        assert "id" not in notif

    def test_make_tools_list_request(self) -> None:
        req = make_tools_list_request()
        assert req["method"] == "tools/list"

    def test_make_tool_call_request(self) -> None:
        req = make_tool_call_request("search", {"q": "hello"})
        assert req["method"] == "tools/call"
        assert req["params"]["name"] == "search"
        assert req["params"]["arguments"] == {"q": "hello"}

    def test_extract_result_ok(self) -> None:
        result = extract_result({"jsonrpc": "2.0", "id": 1, "result": {"data": 42}})
        assert result == {"data": 42}

    def test_extract_result_raises_on_error(self) -> None:
        with pytest.raises(MCPProtocolError) as exc_info:
            extract_result({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}})
        assert exc_info.value.code == -32600

    def test_parse_tool_definitions_returns_list(self) -> None:
        tools = parse_tool_definitions({"tools": [_tool_def("search")]})
        assert len(tools) == 1
        assert tools[0]["name"] == "search"

    def test_parse_tool_definitions_empty(self) -> None:
        assert parse_tool_definitions({}) == []
        assert parse_tool_definitions(None) == []

    def test_parse_tool_call_result_text(self) -> None:
        result = {"content": [{"type": "text", "text": "hello"}], "isError": False}
        content, is_error = parse_tool_call_result(result)
        assert content == "hello"
        assert not is_error

    def test_parse_tool_call_result_error_flag(self) -> None:
        result = {"content": [{"type": "text", "text": "oops"}], "isError": True}
        _, is_error = parse_tool_call_result(result)
        assert is_error

    def test_parse_tool_call_result_image_block(self) -> None:
        result = {
            "content": [{"type": "image", "mimeType": "image/png"}],
            "isError": False,
        }
        content, _ = parse_tool_call_result(result)
        assert "image" in content

    def test_parse_tool_call_result_multiple_blocks(self) -> None:
        result = {
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ],
            "isError": False,
        }
        content, _ = parse_tool_call_result(result)
        assert "first" in content
        assert "second" in content

    def test_parse_tool_call_result_empty_content(self) -> None:
        content, _ = parse_tool_call_result({"content": [], "isError": False})
        assert content == ""

    def test_parse_tool_call_result_non_dict(self) -> None:
        content, is_error = parse_tool_call_result("raw string")
        assert content == "raw string"
        assert not is_error


# ---------------------------------------------------------------------------
# Server name normalisation
# ---------------------------------------------------------------------------


class TestServerNameNormalisation:
    def test_spaces_become_underscores(self) -> None:
        assert normalise_server_name("my server") == "my_server"

    def test_special_chars_stripped(self) -> None:
        assert normalise_server_name("my-server!") == "myserver"

    def test_lowercased(self) -> None:
        assert normalise_server_name("MyServer") == "myserver"

    def test_prefix_format(self) -> None:
        assert make_tool_prefix("linear") == "mcp__linear__"
        assert make_tool_prefix("My Server") == "mcp__my_server__"


# ---------------------------------------------------------------------------
# MCPTool
# ---------------------------------------------------------------------------


class TestMCPTool:
    def _make_client(self) -> MCPServerClient:
        t = FakeTransport()
        return MCPServerClient(name="test", transport=t)

    def test_name(self) -> None:
        client = self._make_client()
        tool = MCPTool(
            server_client=client,
            original_name="search",
            prefixed_name="mcp__test__search",
            description="Search",
            input_schema={"type": "object", "properties": {}},
        )
        assert tool.name == "mcp__test__search"

    def test_description(self) -> None:
        client = self._make_client()
        tool = MCPTool(
            server_client=client,
            original_name="search",
            prefixed_name="mcp__test__search",
            description="Finds things",
            input_schema={},
        )
        assert tool.description == "Finds things"

    def test_required_permission(self) -> None:
        client = self._make_client()
        tool = MCPTool(
            server_client=client,
            original_name="x",
            prefixed_name="mcp__t__x",
            description="x",
            input_schema={},
        )
        assert tool.required_permission == "mcp:call"

    def test_to_api_dict(self) -> None:
        client = self._make_client()
        schema = {"type": "object", "properties": {"q": {"type": "string"}}}
        tool = MCPTool(
            server_client=client,
            original_name="x",
            prefixed_name="mcp__t__x",
            description="desc",
            input_schema=schema,
        )
        api = tool.to_api_dict()
        assert api["name"] == "mcp__t__x"
        assert api["description"] == "desc"
        assert api["input_schema"] == schema

    @pytest.mark.asyncio
    async def test_execute_delegates_to_client(self) -> None:
        client = self._make_client()
        client._health.state = MCPServerState.CONNECTED

        expected = ToolResult(tool_call_id="", content="result text", is_error=False)
        client.call_tool = AsyncMock(return_value=expected)  # type: ignore[method-assign]

        tool = MCPTool(
            server_client=client,
            original_name="search",
            prefixed_name="mcp__test__search",
            description="Search",
            input_schema={},
        )
        result = await tool.execute({"query": "hello"})
        assert result.content == "result text"
        client.call_tool.assert_called_once_with("search", {"query": "hello"})


# ---------------------------------------------------------------------------
# MCPServerClient
# ---------------------------------------------------------------------------


def _make_healthy_client(
    tool_names: list[str] | None = None,
) -> tuple[MCPServerClient, FakeTransport]:
    """Create a client wired to a FakeTransport that simulates a successful handshake."""
    tool_names = tool_names or ["search", "create_issue"]
    tools_result = {"tools": [_tool_def(n) for n in tool_names]}
    resources_result = {"resources": []}

    responses = [
        # initialize → result
        _make_ok_response(1, {"protocolVersion": "2024-11-05", "serverInfo": {"name": "test"}}),
        # tools/list → result
        _make_ok_response(2, tools_result),
        # resources/list → result
        _make_ok_response(3, resources_result),
    ]
    # Responses are keyed by arrival order, but our counter started at some
    # offset — we can't predict the exact IDs, so FakeTransport ignores them.
    transport = FakeTransport(responses=responses)
    client = MCPServerClient(name="test", transport=transport)
    return client, transport


class TestMCPServerClientConnect:
    @pytest.mark.asyncio
    async def test_successful_connect(self) -> None:
        client, transport = _make_healthy_client(["search", "create_issue"])
        tools = await client.connect()
        assert client.is_healthy
        assert len(tools) == 2
        assert transport.started

    @pytest.mark.asyncio
    async def test_connect_phase_is_ready_after_success(self) -> None:
        client, _ = _make_healthy_client()
        await client.connect()
        assert client.health.phase == MCPLifecyclePhase.READY

    @pytest.mark.asyncio
    async def test_transport_start_failure_sets_error(self) -> None:
        transport = FakeTransport()
        transport._fail_start = True
        client = MCPServerClient(name="broken", transport=transport)
        tools = await client.connect()
        assert not client.is_healthy
        assert client.health.state == MCPServerState.ERROR
        assert tools == []

    @pytest.mark.asyncio
    async def test_handshake_send_failure_sets_error(self) -> None:
        transport = FakeTransport(responses=[])
        transport._fail_send = True
        client = MCPServerClient(name="broken", transport=transport)
        # start should succeed, but send during handshake fails
        transport._fail_start = False
        await client.connect()
        assert not client.is_healthy

    @pytest.mark.asyncio
    async def test_handshake_receive_failure_sets_error(self) -> None:
        transport = FakeTransport(responses=[])
        transport._fail_receive = True
        client = MCPServerClient(name="broken", transport=transport)
        await client.connect()
        assert not client.is_healthy

    @pytest.mark.asyncio
    async def test_protocol_error_in_handshake_sets_error(self) -> None:
        error_response = _make_error_response(99, -32600, "invalid request")
        transport = FakeTransport(responses=[error_response])
        client = MCPServerClient(name="broken", transport=transport)
        await client.connect()
        assert not client.is_healthy

    @pytest.mark.asyncio
    async def test_tool_discovery_failure_is_non_fatal(self) -> None:
        """If tool discovery fails, the server stays connected but returns no tools."""
        ok_handshake = _make_ok_response(1, {"protocolVersion": "2024-11-05", "serverInfo": {}})
        tool_error = _make_error_response(2, -32601, "method not found")
        resources_ok = _make_ok_response(3, {"resources": []})
        transport = FakeTransport(responses=[ok_handshake, tool_error, resources_ok])
        client = MCPServerClient(name="partial", transport=transport)
        tools = await client.connect()
        assert client.is_healthy  # Still connected
        assert tools == []

    @pytest.mark.asyncio
    async def test_resource_discovery_failure_is_non_fatal(self) -> None:
        ok_handshake = _make_ok_response(1, {"protocolVersion": "2024-11-05", "serverInfo": {}})
        tools_ok = _make_ok_response(2, {"tools": [_tool_def("ping")]})
        resources_error = _make_error_response(3, -32601, "method not found")
        transport = FakeTransport(responses=[ok_handshake, tools_ok, resources_error])
        client = MCPServerClient(name="noresources", transport=transport)
        tools = await client.connect()
        assert client.is_healthy
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_shutdown_closes_transport(self) -> None:
        client, transport = _make_healthy_client()
        await client.connect()
        await client.shutdown()
        assert transport.closed
        assert client.health.state == MCPServerState.DISCONNECTED


class TestMCPServerClientCallTool:
    @pytest.mark.asyncio
    async def test_call_tool_success(self) -> None:
        client, transport = _make_healthy_client()
        await client.connect()

        # Add a tool call response to the transport's queue.
        call_result = {"content": [{"type": "text", "text": "found it"}], "isError": False}
        transport._responses = iter([_make_ok_response(99, call_result)])

        result = await client.call_tool("search", {"query": "hello"})
        assert not result.is_error
        assert result.content == "found it"

    @pytest.mark.asyncio
    async def test_call_tool_when_not_connected(self) -> None:
        transport = FakeTransport()
        transport._fail_start = True
        client = MCPServerClient(name="broken", transport=transport)
        await client.connect()

        result = await client.call_tool("search", {})
        assert result.is_error
        assert "not connected" in result.content

    @pytest.mark.asyncio
    async def test_call_tool_transport_error_sets_health(self) -> None:
        client, transport = _make_healthy_client()
        await client.connect()

        transport._fail_send = True
        result = await client.call_tool("search", {})
        assert result.is_error
        assert client.health.state == MCPServerState.ERROR

    @pytest.mark.asyncio
    async def test_call_tool_server_error_response(self) -> None:
        client, transport = _make_healthy_client()
        await client.connect()

        transport._responses = iter([_make_error_response(99, -32000, "tool crashed")])
        result = await client.call_tool("crash", {})
        assert result.is_error
        assert "tool crashed" in result.content

    @pytest.mark.asyncio
    async def test_call_tool_is_error_flag_propagated(self) -> None:
        client, transport = _make_healthy_client()
        await client.connect()

        call_result = {"content": [{"type": "text", "text": "oops"}], "isError": True}
        transport._responses = iter([_make_ok_response(99, call_result)])

        result = await client.call_tool("broken_tool", {})
        assert result.is_error


# ---------------------------------------------------------------------------
# MCPManager
# ---------------------------------------------------------------------------


def _stdio_cfg(name: str, **kwargs: Any) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command="npx",
        args=["-y", f"@mcp/{name}"],
        **kwargs,
    )


def _http_cfg(name: str, url: str = "http://localhost:3000") -> MCPServerConfig:
    return MCPServerConfig(name=name, transport="http", url=url)


class TestMCPManager:
    @pytest.mark.asyncio
    async def test_no_configs_returns_empty(self) -> None:
        mgr = MCPManager(configs=[])
        tools = await mgr.start()
        assert tools == []

    @pytest.mark.asyncio
    async def test_disabled_server_skipped(self) -> None:
        cfg = _stdio_cfg("linear", enabled=False)
        mgr = MCPManager(configs=[cfg])
        tools = await mgr.start()
        assert tools == []

    @pytest.mark.asyncio
    async def test_successful_server_registers_tools(self) -> None:
        # Patch _build_transport to inject our FakeTransport.
        client, transport = _make_healthy_client(["search"])
        cfg = _stdio_cfg("linear")

        with patch("ravn.adapters.mcp.manager._build_transport", return_value=transport):
            mgr = MCPManager(configs=[cfg])
            tools = await mgr.start()

        assert len(tools) == 1
        assert tools[0].name == "mcp__linear__search"

    @pytest.mark.asyncio
    async def test_degraded_mode_partial_failure(self) -> None:
        """One server fails; the other's tools are still available."""
        # Server 1: healthy
        _, good_transport = _make_healthy_client(["ping"])
        cfg1 = _stdio_cfg("good")

        # Server 2: fails to start
        bad_transport = FakeTransport()
        bad_transport._fail_start = True
        cfg2 = _stdio_cfg("bad")

        def _select_transport(cfg: MCPServerConfig) -> FakeTransport:
            return good_transport if cfg.name == "good" else bad_transport

        with patch("ravn.adapters.mcp.manager._build_transport", side_effect=_select_transport):
            mgr = MCPManager(configs=[cfg1, cfg2])
            tools = await mgr.start()

        assert len(tools) == 1
        assert tools[0].name == "mcp__good__ping"
        states = mgr.server_states
        assert states["good"] == MCPServerState.CONNECTED
        assert states["bad"] == MCPServerState.ERROR

    @pytest.mark.asyncio
    async def test_collision_with_builtin_tool_skipped(self) -> None:
        _, transport = _make_healthy_client(["search"])
        cfg = _stdio_cfg("linear")

        with patch("ravn.adapters.mcp.manager._build_transport", return_value=transport):
            mgr = MCPManager(
                configs=[cfg],
                builtin_tool_names={"mcp__linear__search"},
            )
            tools = await mgr.start()

        assert tools == []

    @pytest.mark.asyncio
    async def test_shutdown_clears_clients(self) -> None:
        _, transport = _make_healthy_client(["ping"])
        cfg = _stdio_cfg("test")

        with patch("ravn.adapters.mcp.manager._build_transport", return_value=transport):
            mgr = MCPManager(configs=[cfg])
            await mgr.start()

        await mgr.shutdown()
        assert mgr.server_states == {}
        assert mgr.tools == []
        assert transport.closed

    @pytest.mark.asyncio
    async def test_multiple_servers_both_healthy(self) -> None:
        _, t1 = _make_healthy_client(["tool_a"])
        _, t2 = _make_healthy_client(["tool_b"])

        cfg1 = _stdio_cfg("server1")
        cfg2 = _stdio_cfg("server2")

        transports = iter([t1, t2])

        build = lambda _: next(transports)  # noqa: E731
        with patch("ravn.adapters.mcp.manager._build_transport", side_effect=build):
            mgr = MCPManager(configs=[cfg1, cfg2])
            tools = await mgr.start()

        names = {t.name for t in tools}
        assert "mcp__server1__tool_a" in names
        assert "mcp__server2__tool_b" in names

    @pytest.mark.asyncio
    async def test_tool_with_empty_name_skipped(self) -> None:
        """Tool definitions with empty or missing names are silently dropped."""
        ok_handshake = _make_ok_response(1, {"protocolVersion": "2024-11-05", "serverInfo": {}})
        tools_result = {"tools": [{"name": "", "description": "bad"}, _tool_def("good")]}
        tools_ok = _make_ok_response(2, tools_result)
        resources_ok = _make_ok_response(3, {"resources": []})
        transport = FakeTransport(responses=[ok_handshake, tools_ok, resources_ok])
        cfg = _stdio_cfg("test")

        with patch("ravn.adapters.mcp.manager._build_transport", return_value=transport):
            mgr = MCPManager(configs=[cfg])
            tools = await mgr.start()

        assert len(tools) == 1
        assert tools[0].name == "mcp__test__good"

    def test_server_states_empty_when_not_started(self) -> None:
        mgr = MCPManager(configs=[_stdio_cfg("x")])
        assert mgr.server_states == {}

    def test_tools_empty_when_not_started(self) -> None:
        mgr = MCPManager(configs=[_stdio_cfg("x")])
        assert mgr.tools == []

    @pytest.mark.asyncio
    async def test_shutdown_with_no_clients_is_noop(self) -> None:
        """shutdown() returns early without error when no clients are active."""
        mgr = MCPManager(configs=[])
        # Never called start() — _clients is empty; should not raise.
        await mgr.shutdown()
        assert mgr.tools == []

    @pytest.mark.asyncio
    async def test_resolve_auth_headers_unknown_type_returns_empty(self) -> None:
        """Unknown auth_type logs a warning and returns empty headers."""
        from ravn.adapters.mcp.manager import MCPManager
        from ravn.config import MCPAuthConfig, MCPServerConfig

        auth = MCPAuthConfig(auth_type="magic_cookie")
        cfg = MCPServerConfig(name="test", auth=auth)
        headers = await MCPManager._resolve_auth_headers(cfg)
        assert headers == {}

    @pytest.mark.asyncio
    async def test_auth_error_continues_without_auth(self) -> None:
        """When _resolve_auth_headers raises, the server still connects (degraded)."""
        client, transport = _make_healthy_client(["tool_x"])
        from ravn.config import MCPAuthConfig

        cfg = _stdio_cfg("authed", auth=MCPAuthConfig(auth_type="api_key"))

        with (
            patch("ravn.adapters.mcp.manager._build_transport", return_value=transport),
            patch.object(
                MCPManager,
                "_resolve_auth_headers",
                side_effect=RuntimeError("auth failed"),
            ),
        ):
            mgr = MCPManager(configs=[cfg])
            tools = await mgr.start()

        # Server still connects despite auth failure
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_resolve_auth_headers_api_key_type(self) -> None:
        """api_key auth type calls acquire_api_key and returns an auth header."""
        from unittest.mock import AsyncMock

        from ravn.adapters.mcp.manager import MCPManager
        from ravn.config import MCPAuthConfig, MCPServerConfig

        class _FakeToken:
            def auth_header_value(self) -> str:
                return "Bearer fake-token"

        auth = MCPAuthConfig(
            auth_type="api_key",
            api_key_env="MY_API_KEY",
            api_key_header="Authorization",
            api_key_prefix="Bearer",
        )
        cfg = MCPServerConfig(name="keyed", auth=auth)

        with patch(
            "ravn.adapters.mcp.auth.acquire_api_key",
            new=AsyncMock(return_value=_FakeToken()),
        ):
            headers = await MCPManager._resolve_auth_headers(cfg)

        assert headers == {"Authorization": "Bearer fake-token"}


# ---------------------------------------------------------------------------
# _build_input_schema helper
# ---------------------------------------------------------------------------


class TestBuildInputSchema:
    def test_uses_server_schema_when_present(self) -> None:
        tool_def = {"inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}
        schema = _build_input_schema(tool_def)
        assert schema["properties"]["q"]["type"] == "string"

    def test_falls_back_to_empty_object_schema(self) -> None:
        schema = _build_input_schema({"name": "tool"})
        assert schema == {"type": "object", "properties": {}}

    def test_non_dict_schema_replaced(self) -> None:
        schema = _build_input_schema({"inputSchema": "not a dict"})
        assert schema == {"type": "object", "properties": {}}


# ---------------------------------------------------------------------------
# Transport base helpers
# ---------------------------------------------------------------------------


class TestTransportHelpers:
    def test_encode_decode_roundtrip(self) -> None:
        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        encoded = MCPTransport._encode(msg)
        decoded = MCPTransport._decode(encoded)
        assert decoded == msg

    def test_decode_from_string(self) -> None:
        msg = {"jsonrpc": "2.0", "id": 1, "result": "ok"}
        import json

        decoded = MCPTransport._decode(json.dumps(msg))
        assert decoded == msg


# ---------------------------------------------------------------------------
# Stdio transport error paths (no subprocess spawning)
# ---------------------------------------------------------------------------


class TestStdioTransportErrors:
    @pytest.mark.asyncio
    async def test_send_before_start_raises(self) -> None:
        from ravn.adapters.mcp.stdio_transport import StdioTransport

        t = StdioTransport("echo", [])
        with pytest.raises(MCPTransportError, match="not started"):
            await t.send({"jsonrpc": "2.0"})

    @pytest.mark.asyncio
    async def test_receive_before_start_raises(self) -> None:
        from ravn.adapters.mcp.stdio_transport import StdioTransport

        t = StdioTransport("echo", [])
        with pytest.raises(MCPTransportError, match="not started"):
            await t.receive()

    @pytest.mark.asyncio
    async def test_is_alive_false_before_start(self) -> None:
        from ravn.adapters.mcp.stdio_transport import StdioTransport

        t = StdioTransport("echo", [])
        assert not t.is_alive

    @pytest.mark.asyncio
    async def test_start_raises_on_bad_command(self) -> None:
        from ravn.adapters.mcp.stdio_transport import StdioTransport

        t = StdioTransport("__no_such_command_xyz__", [])
        with pytest.raises(MCPTransportError):
            await t.start()

    @pytest.mark.asyncio
    async def test_close_is_safe_before_start(self) -> None:
        from ravn.adapters.mcp.stdio_transport import StdioTransport

        t = StdioTransport("echo", [])
        await t.close()  # Should not raise


# ---------------------------------------------------------------------------
# HTTP transport error paths
# ---------------------------------------------------------------------------


class TestHTTPTransportErrors:
    @pytest.mark.asyncio
    async def test_send_before_start_raises(self) -> None:
        from ravn.adapters.mcp.sse_transport import HTTPTransport

        t = HTTPTransport("http://localhost:9999")
        with pytest.raises(MCPTransportError, match="not started"):
            await t.send({"jsonrpc": "2.0"})

    @pytest.mark.asyncio
    async def test_receive_before_start_raises(self) -> None:
        from ravn.adapters.mcp.sse_transport import HTTPTransport

        t = HTTPTransport("http://localhost:9999")
        with pytest.raises(MCPTransportError, match="not started"):
            await t.receive()

    @pytest.mark.asyncio
    async def test_is_alive_false_before_start(self) -> None:
        from ravn.adapters.mcp.sse_transport import HTTPTransport

        t = HTTPTransport("http://localhost:9999")
        assert not t.is_alive

    @pytest.mark.asyncio
    async def test_start_with_httpx(self) -> None:
        from ravn.adapters.mcp.sse_transport import HTTPTransport

        t = HTTPTransport("http://localhost:9999")
        # httpx is available in the project
        await t.start()
        assert t.is_alive
        await t.close()

    @pytest.mark.asyncio
    async def test_close_is_safe_before_start(self) -> None:
        from ravn.adapters.mcp.sse_transport import HTTPTransport

        t = HTTPTransport("http://localhost:9999")
        await t.close()  # Should not raise


# ---------------------------------------------------------------------------
# MCPServerHealth dataclass
# ---------------------------------------------------------------------------


class TestMCPServerHealth:
    def test_defaults(self) -> None:
        h = MCPServerHealth()
        assert h.state == MCPServerState.DISCONNECTED
        assert h.phase == MCPLifecyclePhase.CONFIG_LOAD
        assert h.error == ""
        assert h.server_info == {}
