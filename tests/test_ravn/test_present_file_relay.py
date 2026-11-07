"""present_file for CLI agents: the tool server presents through the daemon's relay."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ravn import cap_telemetry
from ravn.adapters.executors.cli import CliTransportExecutor
from ravn.adapters.tools.present_file import PresentFileTool
from ravn.adapters.tools.present_file_relay import (
    PresentFileRelayClient,
    PresentFileRelayError,
    PresentFileRelayServer,
)
from ravn.cli.daemon_runtime import _present_file_relay_socket, _start_present_file_relay
from ravn.config import PresentFileConfig, Settings
from ravn.domain.models import Session


class FakeMesh:
    def __init__(self, reply: dict | None = None, exc: Exception | None = None) -> None:
        self.reply = reply or {"status": "complete", "file_id": "pf_1", "version_label": "v1"}
        self.exc = exc
        self.sent: list[tuple[str, dict, float | None]] = []

    async def send(self, target_peer_id: str, message: dict, *, timeout_s=None) -> dict:
        self.sent.append((target_peer_id, message, timeout_s))
        if self.exc is not None:
            raise self.exc
        return dict(self.reply)


@pytest.fixture
async def relay():
    mesh = FakeMesh()
    server = PresentFileRelayServer(mesh, broker_peer_id="skuld-1", timeout_s=7.0)
    await server.start()
    try:
        yield server, mesh
    finally:
        await server.stop()


def _tool(socket_path: str, workspace: Path) -> PresentFileTool:
    return PresentFileTool(
        PresentFileRelayClient(socket_path),
        broker_peer_id="skuld-1",
        own_peer_id="flock-developer-analyst",
        workspace_root=workspace,
        timeout_s=7.0,
    )


async def test_a_tool_server_presents_through_the_daemons_mesh(relay, tmp_path):
    server, mesh = relay
    result = await _tool(server.socket_path, tmp_path).execute(
        {"content": "# Plan\n", "name": "plan.md", "artifact_key": "plan", "caption": "r1"}
    )

    assert not result.is_error
    assert json.loads(result.content) == {"file_id": "pf_1", "version_label": "v1"}
    [(peer, message, timeout)] = mesh.sent
    assert (peer, timeout) == ("skuld-1", 7.0), "the daemon bounds the broker call"
    assert message["type"] == "present_file"
    assert message["artifact_key"] == "plan"
    assert (tmp_path / message["path"]).read_text() == "# Plan\n"


async def test_the_relay_socket_is_private_and_removed_on_stop(tmp_path):
    server = PresentFileRelayServer(FakeMesh(), broker_peer_id="skuld-1", timeout_s=1.0)
    await server.start()
    socket_path = Path(server.socket_path)
    assert socket_path.is_socket()
    assert socket_path.parent.stat().st_mode & 0o077 == 0

    await server.stop()

    assert not socket_path.parent.exists()
    with pytest.raises(PresentFileRelayError, match="not running"):
        _ = server.socket_path


@pytest.mark.parametrize(
    ("request_line", "says"),
    [
        ({"target": "skuld-1", "message": {"type": "task_dispatch"}}, "only 'present_file'"),
        ({"target": "other-peer", "message": {"type": "present_file"}}, "only to the session"),
        (["not", "an", "object"], "not a JSON object"),
    ],
)
async def test_the_relay_forwards_nothing_but_present_file_to_the_broker(relay, request_line, says):
    server, mesh = relay
    client = PresentFileRelayClient(server.socket_path)
    if isinstance(request_line, dict):
        reply = await client.send(request_line["target"], request_line["message"])
    else:
        import asyncio

        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(json.dumps(request_line).encode() + b"\n")
        await writer.drain()
        reply = json.loads(await reader.readline())
        writer.close()

    assert reply["status"] == "error" and says in reply["error"]
    assert mesh.sent == []


async def test_a_broker_timeout_is_counted_in_the_daemon_and_fails_the_tool(tmp_path):
    server = PresentFileRelayServer(
        FakeMesh(exc=TimeoutError()), broker_peer_id="skuld-1", timeout_s=3.0
    )
    await server.start()
    before = cap_telemetry.cap_hit_counts().get("present_file.timeout_s", 0)
    try:
        result = await _tool(server.socket_path, tmp_path).execute(
            {"content": "x", "name": "plan.md"}
        )
    finally:
        await server.stop()

    assert result.is_error and "present_file.timeout_s=3s" in result.content
    assert cap_telemetry.cap_hit_counts()["present_file.timeout_s"] == before + 1


async def test_a_failed_broker_request_fails_the_tool_with_its_reason(tmp_path):
    server = PresentFileRelayServer(
        FakeMesh(exc=RuntimeError("peer skuld-1 not found")),
        broker_peer_id="skuld-1",
        timeout_s=3.0,
    )
    await server.start()
    try:
        result = await _tool(server.socket_path, tmp_path).execute(
            {"content": "x", "name": "plan.md"}
        )
    finally:
        await server.stop()

    assert result.is_error and "peer skuld-1 not found" in result.content


async def test_an_unreachable_relay_raises(tmp_path):
    client = PresentFileRelayClient(str(tmp_path / "missing.sock"))

    with pytest.raises(PresentFileRelayError, match="no socket is there"):
        client.require_reachable()
    with pytest.raises(PresentFileRelayError, match="cannot reach"):
        await client.send("skuld-1", {"type": "present_file"})


def _ravn_tools_args(**build_kwargs) -> list[str]:
    agent = CliTransportExecutor(
        transport_adapter="skuld.transports.codex_ws.CodexWebSocketTransport"
    ).build(
        channel=MagicMock(),
        system_prompt="Plan.",
        session=Session(),
        model="gpt-5.6",
        max_iterations=3,
        checkpoint_port=None,
        task_id="task-present",
        persona="developer-analyst",
        workspace_dir="/tmp/workspace",
        permission_mode="read_only",
        tools=[SimpleNamespace(name="present_file")],
        mcp_servers=[],
        **build_kwargs,
    )
    (server,) = agent._transport_kwargs["mcp_servers"]
    return server["args"]


def test_a_cli_agent_offered_present_file_gets_its_daemons_relay() -> None:
    args = _ravn_tools_args(present_file_relay="/tmp/ravn-present-x/present-file.sock")

    assert args[args.index("--present-file-relay") + 1] == ("/tmp/ravn-present-x/present-file.sock")
    assert "--present-file-relay" not in _ravn_tools_args()


async def test_the_daemon_starts_the_relay_on_its_mesh_only_when_configured(tmp_path):
    mesh = FakeMesh()
    assert await _start_present_file_relay(_settings(tmp_path, enabled=False), mesh) is None

    relay = await _start_present_file_relay(_settings(tmp_path), mesh)
    try:
        result = await _tool(relay.socket_path, tmp_path).execute(
            {"content": "x", "name": "plan.md"}
        )
    finally:
        await relay.stop()

    assert not result.is_error
    assert mesh.sent[0][0] == "skuld-1"


def test_the_daemon_hands_the_relay_only_to_a_task_offered_present_file() -> None:
    relay = SimpleNamespace(socket_path="/tmp/ravn-present-x/present-file.sock")
    presenting = [SimpleNamespace(name="read_file"), SimpleNamespace(name="present_file")]

    assert _present_file_relay_socket(presenting, relay) == relay.socket_path
    assert _present_file_relay_socket([SimpleNamespace(name="read_file")], relay) == ""
    with pytest.raises(RuntimeError, match="relay is not running"):
        _present_file_relay_socket(presenting, None)


def _settings(tmp_path: Path, *, enabled: bool = True) -> Settings:
    settings = Settings()
    settings.present_file = PresentFileConfig(enabled=enabled, broker_peer_id="skuld-1")
    settings.mesh.own_peer_id = "flock-developer-analyst"
    settings.permission.workspace_root = str(tmp_path)
    return settings


def _serve_tool_mcp(settings: Settings, relay_socket: str) -> list:
    from ravn.cli import commands as commands_mod

    served: dict = {}

    def server_for(tools, **_kwargs):
        served["tools"] = tools
        server = MagicMock()

        async def run_stdio() -> None:
            return None

        server.run_stdio = run_stdio
        return server

    with (
        patch.object(commands_mod, "Settings", return_value=settings),
        patch.object(commands_mod, "_configure_logging"),
        patch.object(commands_mod.ProjectConfig, "discover", return_value=MagicMock()),
        patch.object(commands_mod, "_resolve_profile", return_value=None),
        patch.object(
            commands_mod,
            "_resolve_persona",
            return_value=MagicMock(allowed_tools=["read_file", "present_file"]),
        ),
        patch.object(commands_mod, "_build_tool_mcp_tools", return_value=[]),
        patch.object(commands_mod, "_tool_mcp_notification_outbox", return_value=None),
        patch("ravn.adapters.mcp.tool_port_server.ToolPortMcpServer", side_effect=server_for),
    ):
        commands_mod.tool_mcp(
            config="",
            persona="developer-analyst",
            profile="",
            notification_channel="",
            present_file_relay=relay_socket,
        )
    return served["tools"]


async def test_the_tool_server_offers_present_file_through_the_relay(relay, tmp_path):
    server, mesh = relay
    import asyncio

    tools = await asyncio.to_thread(_serve_tool_mcp, _settings(tmp_path), server.socket_path)

    [tool] = tools
    assert tool.name == "present_file"
    result = await tool.execute({"content": "# Plan", "name": "plan.md", "artifact_key": "plan"})
    assert not result.is_error
    assert mesh.sent[0][1]["peer_id"] == "flock-developer-analyst"


def test_the_tool_server_fails_when_the_relay_is_not_there(tmp_path):
    with pytest.raises(PresentFileRelayError, match="no socket is there"):
        _serve_tool_mcp(_settings(tmp_path), str(tmp_path / "gone.sock"))


def test_the_tool_server_fails_when_its_config_does_not_enable_present_file(tmp_path):
    with pytest.raises(RuntimeError, match="present_file.enabled is false"):
        _serve_tool_mcp(_settings(tmp_path, enabled=False), str(tmp_path / "relay.sock"))


async def test_a_request_that_is_not_json_is_refused(relay):
    import asyncio

    server, mesh = relay
    reader, writer = await asyncio.open_unix_connection(server.socket_path)
    writer.write(b"not json\n")
    await writer.drain()
    reply = json.loads(await reader.readline())
    writer.close()

    assert reply == {"status": "error", "error": "the relay request is not JSON"}
    assert mesh.sent == []


@pytest.mark.parametrize(
    ("answer", "says"),
    [
        (b"", "closed without answering"),
        (b"{oops\n", "malformed JSON"),
        (b"[1]\n", "non-object"),
    ],
)
async def test_a_broken_relay_answer_raises(answer, says):
    import asyncio
    import shutil
    import tempfile

    directory = tempfile.mkdtemp(dir="/tmp")
    socket_path = f"{directory}/relay.sock"

    async def answer_badly(reader, writer) -> None:
        await reader.readline()
        writer.write(answer)
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(answer_badly, path=socket_path)
    try:
        with pytest.raises(PresentFileRelayError, match=says):
            await PresentFileRelayClient(socket_path).send("skuld-1", {"type": "present_file"})
    finally:
        server.close()
        await server.wait_closed()
        shutil.rmtree(directory)
