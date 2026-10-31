"""The built-in forge MCP, session env and capability instructions reach every engine.

Each test builds the transport through ``Broker._create_transport`` — the real
composition path — and asserts the engine-facing contract (argv, config files,
env, protocol params) without launching a model.
"""

from __future__ import annotations

import json
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.session_tools import (
    BROKER_TOKEN_FILE_ENV,
    PRESENT_FILE_URL_ENV,
    claude_cli_args,
)
from skuld.transports.tool_shims import ensure_codex_tool_shims

ADAPTERS = {
    "tmux": "skuld.transports.tmux_interactive.TmuxInteractiveTransport",
    "sdk": "skuld.transports.sdk.SDKTransport",
    "subprocess": "skuld.transports.subprocess.SubprocessTransport",
    "persistent": "skuld.transports.persistent_subprocess.PersistentSubprocessTransport",
    "sdk_websocket": "skuld.transports.sdk_websocket.SdkWebSocketTransport",
    "codex_ws": "skuld.transports.codex_ws.CodexWebSocketTransport",
    "codex": "skuld.transports.codex.CodexSubprocessTransport",
    "grok": "skuld.transports.grok.GrokACPTransport",
}
SESSION_ID = "0b6c1d7e-3f0a-4a8e-9a57-6a9c2f1d0e11"


def _broker(
    tmp_path: Path, adapter: str = "sdk", *, system_prompt: str = "", **overrides: Any
) -> Broker:
    forge_mcp = {"runtime_dir": str(tmp_path / "rt"), **overrides.pop("forge_mcp", {})}
    settings = SkuldSettings(
        session={
            "id": SESSION_ID,
            "workspace_dir": str(tmp_path / "ws"),
            "model": "m",
            "system_prompt": system_prompt,
        },
        transport_adapter=ADAPTERS[adapter],
        port=18765,
        host="0.0.0.0",
        forge_mcp=forge_mcp,
        **overrides,
    )
    (tmp_path / "ws").mkdir(exist_ok=True)
    return Broker(settings=settings)


def _forge_server(servers: list[dict[str, Any]]) -> dict[str, Any]:
    [forge] = [server for server in servers if server["name"] == "forge"]
    return forge


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestBrokerComposition:
    def test_forge_stdio_server_is_injected(self, tmp_path) -> None:
        broker = _broker(tmp_path, mcp_servers=[{"name": "mimir", "command": "m"}])
        kwargs = broker._build_transport_kwargs()
        servers = kwargs["mcp_servers"]
        assert [server["name"] for server in servers] == ["mimir", "forge"]
        forge = _forge_server(servers)
        assert forge["command"] == sys.executable
        args = forge["args"]
        assert args[:3] == ["-P", "-m", "skuld.forge_mcp"]
        assert args[args.index("--broker-url") + 1] == "http://127.0.0.1:18765"
        token_file = Path(args[args.index("--token-file") + 1])
        assert token_file == tmp_path / "rt" / "forge-mcp.token"
        assert token_file.read_text() in broker._session_runtime.authorization
        tools = kwargs["session_tools"]
        assert tools.env == {
            PRESENT_FILE_URL_ENV: "http://127.0.0.1:18765/api/present-file",
            BROKER_TOKEN_FILE_ENV: str(token_file),
        }
        assert "notify" in tools.instructions and "present-file" in tools.instructions
        # The agent owns the "done" signal now that Forge no longer notifies on turn end.
        assert "Forge sends nothing when a turn ends" in tools.instructions
        assert "reply ready" not in tools.instructions

    def test_skill_is_materialized_outside_the_workspace(self, tmp_path) -> None:
        tools = _broker(tmp_path)._session_tools()
        [plugin] = tools.claude_plugin_dirs
        [codex_root] = tools.codex_skill_roots
        assert plugin.is_relative_to(tmp_path / "rt")
        assert json.loads((plugin / ".claude-plugin" / "plugin.json").read_text())["name"] == (
            "forge"
        )
        skill = plugin / "skills" / "forge-notify" / "SKILL.md"
        assert skill.read_text().startswith("---\nname: forge-notify\n")
        assert (codex_root / "forge-notify" / "SKILL.md").read_text() == skill.read_text()
        assert _mode(skill) == 0o600
        assert not any((tmp_path / "ws").iterdir())

    def test_a_launch_spec_forge_entry_overrides_the_builtin(self, tmp_path) -> None:
        custom = {"name": "forge", "type": "http", "url": "https://forge.example/mcp/forge"}
        broker = _broker(tmp_path, mcp_servers=[custom])
        kwargs = broker._build_transport_kwargs()
        assert kwargs["mcp_servers"] == [custom]
        tools = kwargs["session_tools"]
        assert "notify" not in tools.instructions and "present-file" in tools.instructions
        assert tools.claude_plugin_dirs == ()

    def test_opt_out(self, tmp_path) -> None:
        broker = _broker(tmp_path, forge_mcp={"enabled": False})
        kwargs = broker._build_transport_kwargs()
        assert kwargs["mcp_servers"] == []
        assert "notify" not in kwargs["session_tools"].instructions
        assert broker._effective_mcp_server_names() == []

    @pytest.mark.parametrize(
        ("host", "url"),
        [
            ("0.0.0.0", "http://127.0.0.1:18765"),
            ("127.0.0.1", "http://127.0.0.1:18765"),
            ("10.1.2.3", "http://10.1.2.3:18765"),
            ("fd00::5", "http://[fd00::5]:18765"),
        ],
    )
    def test_loopback_url_follows_the_bind_host(self, tmp_path, host, url) -> None:
        broker = _broker(tmp_path)
        broker._settings.host = host
        assert broker._loopback_base_url() == url

    def test_frozen_binary_uses_the_platform_subcommand(self, monkeypatch) -> None:
        from skuld.forge_mcp.integration import forge_mcp_command

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert forge_mcp_command() == [sys.executable, "platform", "forge-mcp"]

    @pytest.mark.parametrize("adapter", sorted(set(ADAPTERS) - {"grok"}))
    def test_every_mcp_engine_receives_session_tools(self, tmp_path, adapter) -> None:
        broker = _broker(tmp_path, adapter)
        transport = broker._create_transport()
        assert transport._session_tools is broker._session_tools()

    async def test_shutdown_revokes_the_secret(self, tmp_path) -> None:
        broker = _broker(tmp_path)
        token_file = broker._session_runtime.token_file
        broker._revoke_session_runtime()
        assert not token_file.exists()


class TestClaude:
    def test_tmux_argv_config_hooks_and_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SKULD__EXTERNAL_API_TOKEN", "svc-secret")
        broker = _broker(tmp_path, "tmux")
        transport = broker._create_transport()
        tools = broker._session_tools()

        argv = transport._interactive_argv()

        config_path = Path(argv[argv.index("--mcp-config") + 1])
        assert config_path == tmp_path / "rt" / "claude-mcp.json"
        assert _mode(config_path) == 0o600
        forge = json.loads(config_path.read_text())["mcpServers"]["forge"]
        assert forge["type"] == "stdio" and "skuld.forge_mcp" in forge["args"]
        assert argv[argv.index("--plugin-dir") + 1] == str(tools.claude_plugin_dirs[0])
        prompt = argv[argv.index("--append-system-prompt") + 1]
        assert "present-file" in prompt and "`notify` tool" in prompt
        assert not any(arg.startswith("{") for arg in argv), "no inline JSON in argv"

        transport._write_hook_settings()
        settings_path = Path(argv[argv.index("--settings") + 1])
        assert settings_path.is_relative_to(tmp_path / "rt")
        assert _mode(settings_path) == 0o600
        hooks = json.loads(settings_path.read_text())["hooks"]
        hook = next(iter(hooks.values()))[0]["hooks"][0]
        assert hook["headers"] == {"Authorization": tools.loopback_authorization}
        assert hook["url"] == "http://127.0.0.1:18765/api/claude/hooks"

        env = transport._spawn_env()
        assert env[PRESENT_FILE_URL_ENV] == tools.env[PRESENT_FILE_URL_ENV]
        assert env[BROKER_TOKEN_FILE_ENV] == tools.env[BROKER_TOKEN_FILE_ENV]
        assert "SKULD__EXTERNAL_API_TOKEN" not in env
        assert tools.loopback_authorization.removeprefix("Bearer ") not in json.dumps(env)

    async def test_agent_sdk_options(self, tmp_path, monkeypatch) -> None:
        broker = _broker(tmp_path, "sdk", system_prompt="Role.")
        transport = broker._create_transport()
        captured: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, options: Any) -> None:
                captured["options"] = options

            async def __aenter__(self) -> FakeClient:
                return self

        monkeypatch.setattr("skuld.transports.sdk.ClaudeSDKClient", FakeClient)

        await transport._connect_client()

        options = captured["options"]
        assert options.mcp_servers == str(tmp_path / "rt" / "claude-mcp.json")
        assert "forge" in json.loads(Path(options.mcp_servers).read_text())["mcpServers"]
        assert options.plugins == [
            {"type": "local", "path": str(broker._session_tools().claude_plugin_dirs[0])}
        ]
        assert options.system_prompt.endswith("Role.")
        assert "`notify` tool" in options.system_prompt
        assert options.env[BROKER_TOKEN_FILE_ENV].endswith("forge-mcp.token")

    @pytest.mark.parametrize("adapter", ["persistent", "subprocess", "sdk_websocket"])
    async def test_cli_subprocess_transports(self, tmp_path, adapter) -> None:
        broker = _broker(tmp_path, adapter)
        transport = broker._create_transport()
        module = ADAPTERS[adapter].rsplit(".", 1)[0]
        process = MagicMock(stdout=None, stderr=None, stdin=None, pid=1, returncode=0)
        with patch(f"{module}.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn:
            spawn.return_value = process
            if adapter == "persistent":
                await transport._spawn()
            elif adapter == "subprocess":
                try:
                    await transport._send_message_once("hi")
                except Exception:  # noqa: BLE001 - the fake process has no stdout to parse
                    pass
            else:
                await transport._spawn()
        argv = list(spawn.call_args.args)
        env = spawn.call_args.kwargs["env"]
        assert argv[argv.index("--mcp-config") + 1].endswith("claude-mcp.json")
        assert "--plugin-dir" in argv
        assert "`notify` tool" in argv[argv.index("--append-system-prompt") + 1]
        assert env[PRESENT_FILE_URL_ENV].endswith("/api/present-file")

    def test_resume_keeps_capability_text_but_not_the_session_prompt(self, tmp_path) -> None:
        tools = _broker(tmp_path)._session_tools()
        args = claude_cli_args(
            tools,
            mcp_servers=[],
            legacy_mcp_config=None,
            system_prompt="first-run only",
            include_system_prompt=False,
        )
        prompt = args[args.index("--append-system-prompt") + 1]
        assert "present-file" in prompt and "first-run only" not in prompt
        assert "--mcp-config" not in args  # no servers configured → no config file

    def test_without_session_tools_the_legacy_shape_is_kept(self) -> None:
        args = claude_cli_args(
            None,
            mcp_servers=[{"name": "x", "command": "c"}],
            legacy_mcp_config='{"mcpServers": {}}',
            system_prompt="p",
            include_system_prompt=True,
        )
        assert args == ["--append-system-prompt", "p", "--mcp-config", '{"mcpServers": {}}']


class TestCodex:
    async def test_app_server_gets_mcp_env_skills_and_developer_instructions(
        self, tmp_path
    ) -> None:
        broker = _broker(tmp_path, "codex_ws")
        transport = broker._create_transport()
        transport._process_owner = None  # no real child to adopt
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
        assert f'mcp_servers.forge.command="{sys.executable}"' in overrides
        assert any(o.startswith("mcp_servers.forge.args=[") for o in overrides)
        assert "mcp_servers.forge.tool_timeout_sec=120.0" in overrides
        env = spawn.call_args.kwargs["env"]
        assert env[BROKER_TOKEN_FILE_ENV].endswith("forge-mcp.token")

        rpc = AsyncMock(side_effect=[{"userAgent": "codex"}, {}, {"thread": {"id": "t-1"}}])
        transport._send_rpc = rpc
        transport._send_notification = AsyncMock()
        transport._authenticate_codex = AsyncMock()
        transport.on_event(AsyncMock())
        await transport._handshake()
        methods = [call.args[0] for call in rpc.await_args_list]
        assert methods == ["initialize", "skills/extraRoots/set", "thread/start"]
        roots = rpc.await_args_list[1].args[1]["extraRoots"]
        assert roots == [str(broker._session_tools().codex_skill_roots[0])]
        thread = rpc.await_args_list[2].args[1]
        assert "`notify` tool" in thread["developerInstructions"]
        assert "baseInstructions" not in thread  # never replace Codex's own prompt

    async def test_exec_gets_developer_instructions(self, tmp_path) -> None:
        broker = _broker(tmp_path, "codex")
        transport = broker._create_transport()
        await transport.start()
        process = MagicMock(stdout=None, stderr=None, returncode=0)
        with patch(
            "skuld.transports.codex.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ) as spawn:
            try:
                await transport.send_message("hi")
            except Exception:  # noqa: BLE001 - the fake process has no stdout
                pass
        argv = list(spawn.call_args.args)
        overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
        [instructions] = [o for o in overrides if o.startswith("developer_instructions=")]
        assert "`notify` tool" in instructions
        assert any(o.startswith("mcp_servers.forge.command=") for o in overrides)
        assert spawn.call_args.kwargs["env"][PRESENT_FILE_URL_ENV].endswith("/api/present-file")


class TestGrok:
    async def test_session_new_carries_acp_mcp_servers(self, tmp_path) -> None:
        broker = _broker(tmp_path, "grok")
        transport = broker._create_transport()
        transport._mcp_capabilities = {"http": True, "sse": True}
        transport._acp_send = AsyncMock(return_value={"sessionId": "g-1"})
        await transport._acp_new_session()
        params = transport._acp_send.await_args.args[1]
        [forge] = params["mcpServers"]
        assert forge["name"] == "forge" and forge["command"] == sys.executable
        assert "skuld.forge_mcp" in forge["args"] and forge["env"] == []


class _Recorder(BaseHTTPRequestHandler):
    seen: list[tuple[str | None, dict[str, Any]]] = []

    def log_message(self, *_args: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        authorization = self.headers.get("authorization")
        type(self).seen.append((authorization, body))
        status = 200 if authorization == "Bearer shim-secret" else 401
        data = json.dumps({"file_id": "pf_x"} if status == 200 else {"detail": "no"}).encode()
        self.send_response(status)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def test_present_file_shim_sends_the_loopback_secret(tmp_path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        token_file = tmp_path / "forge-mcp.token"
        token_file.write_text("shim-secret\n")
        url = f"http://127.0.0.1:{server.server_address[1]}/api/present-file"
        bin_dir, env = ensure_codex_tool_shims(
            str(tmp_path / "ws"),
            session_env={PRESENT_FILE_URL_ENV: url, BROKER_TOKEN_FILE_ENV: str(token_file)},
        )
        assert env[PRESENT_FILE_URL_ENV] == url
        report = tmp_path / "report.md"
        report.write_text("# r")
        shim = str(bin_dir / "present-file")

        ok = subprocess.run(
            [shim, str(report), "--caption", "c"],
            env={"PATH": "/usr/bin:/bin", **env},
            capture_output=True,
            text=True,
            timeout=30,
        )
        without_token = {k: v for k, v in env.items() if k != BROKER_TOKEN_FILE_ENV}
        denied = subprocess.run(
            [shim, str(report)],
            env={"PATH": "/usr/bin:/bin", **without_token},
            capture_output=True,
            text=True,
            timeout=30,
        )
        missing = subprocess.run(
            [shim, str(report)],
            env={"PATH": "/usr/bin:/bin", **env, BROKER_TOKEN_FILE_ENV: str(tmp_path / "gone")},
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()

    assert ok.returncode == 0, ok.stderr
    assert _Recorder.seen[0] == (
        "Bearer shim-secret",
        {"path": str(report), "caption": "c"},
    )
    assert "shim-secret" not in ok.stdout + ok.stderr
    assert denied.returncode == 1 and _Recorder.seen[1][0] is None
    assert missing.returncode == 3 and "broker token" in missing.stderr
