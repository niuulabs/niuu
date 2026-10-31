"""The broker's credentials never reach an agent process, whatever the engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from skuld.transports.claude_env import claude_spawn_env
from skuld.transports.session_env import (
    BROKER_CREDENTIAL_VARS,
    is_broker_credential,
    scrub_broker_credentials,
    session_process_env,
)

BROKER_SECRETS = {
    "SKULD__EXTERNAL_API_TOKEN": "svc-token",
    "VOLUNDR_EXTERNAL_API_TOKEN": "legacy-token",
    "SKULD__FORGE_MCP__TOKEN": "mcp-token",
    "SKULD__TELEGRAM__BOT_TOKEN": "bot",
    "DATABASE__PASSWORD": "pg-password",
    "NIUU_WORKLOAD_IDENTITY_TOKEN_FILE": "/var/run/token",
    "IDENTITY__CLIENT_SECRET": "oidc",
}
ENGINE_AUTH = {
    "ANTHROPIC_API_KEY": "sk-ant",
    "OPENAI_API_KEY": "sk-oai",
    "XAI_API_KEY": "xai",
    "GITHUB_TOKEN": "gh",
    "CLAUDE_CODE_OAUTH_TOKEN": "oauth",
}
PLAIN = {"SKULD__SESSION__ID": "s-1", "PATH": "/usr/bin", "HOME": "/home/agent"}


@pytest.fixture
def broker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {**BROKER_SECRETS, **ENGINE_AUTH, **PLAIN}.items():
        monkeypatch.setenv(key, value)


def _assert_scrubbed(env: dict[str, str]) -> None:
    leaked = sorted(set(BROKER_SECRETS) & set(env))
    assert not leaked, f"broker credentials leaked into the session env: {leaked}"
    assert env["SKULD__SESSION__ID"] == "s-1"
    assert env["PATH"] == "/usr/bin"


class TestClassifier:
    @pytest.mark.parametrize("name", sorted(BROKER_SECRETS) + sorted(BROKER_CREDENTIAL_VARS))
    def test_platform_credentials(self, name: str) -> None:
        assert is_broker_credential(name)

    @pytest.mark.parametrize(
        "name",
        [*ENGINE_AUTH, "SKULD__SESSION__ID", "SKULD__CLAUDE_AUTH", "VOLUNDR__URL", "PATH"],
    )
    def test_engine_auth_and_plain_config_pass(self, name: str) -> None:
        assert not is_broker_credential(name)

    def test_scrub_is_a_copy(self) -> None:
        env = {"DATABASE__PASSWORD": "x", "PATH": "/bin"}
        assert scrub_broker_credentials(env) == {"PATH": "/bin"}
        assert env == {"DATABASE__PASSWORD": "x", "PATH": "/bin"}

    def test_session_process_env_reads_the_live_environment(self, broker_env: None) -> None:
        env = session_process_env()
        _assert_scrubbed(env)
        assert env["OPENAI_API_KEY"] == "sk-oai"


class TestClaudeEnv:
    def test_subscription_mode(self, broker_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SKULD__CLAUDE_AUTH", "subscription")
        env = claude_spawn_env()
        _assert_scrubbed(env)
        assert "ANTHROPIC_API_KEY" not in env

    def test_api_key_mode_keeps_engine_auth(
        self, broker_env: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SKULD__CLAUDE_AUTH", "api_key")
        env = claude_spawn_env()
        _assert_scrubbed(env)
        assert env["ANTHROPIC_API_KEY"] == "sk-ant"


class _SpawnedError(Exception):
    """Raised by the fake spawner once the child env has been captured."""


def _capturing_spawn(captured: list[dict[str, str]]) -> AsyncMock:
    async def fake_exec(*_args: object, **kwargs: object) -> None:
        env = kwargs.get("env")
        assert isinstance(env, dict)
        captured.append(env)
        raise _SpawnedError

    return AsyncMock(side_effect=fake_exec)


async def _spawn_env(module: str, start) -> dict[str, str]:
    captured: list[dict[str, str]] = []
    with patch(f"{module}.asyncio.create_subprocess_exec", _capturing_spawn(captured)):
        try:
            await start()
        except (_SpawnedError, RuntimeError):
            pass
    assert captured, f"{module} never spawned a process"
    return captured[-1]


class TestEngines:
    def test_codex_app_server_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.codex_ws import CodexWebSocketTransport

        transport = CodexWebSocketTransport(workspace_dir=str(tmp_path))
        _assert_scrubbed(transport._env)
        assert transport._env["OPENAI_API_KEY"] == "sk-oai"

    def test_codex_exec_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.codex import CodexSubprocessTransport

        _assert_scrubbed(CodexSubprocessTransport(str(tmp_path))._env)

    async def test_opencode_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.opencode import OpenCodeHttpTransport

        transport = OpenCodeHttpTransport(workspace_dir=str(tmp_path))
        _assert_scrubbed(await _spawn_env("skuld.transports.opencode", transport.start))

    async def test_grok_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.grok import GrokACPTransport

        transport = GrokACPTransport(workspace_dir=str(tmp_path), grok_bin="grok")
        _assert_scrubbed(await _spawn_env("skuld.transports.grok", transport.start))

    async def test_dsh_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.dsh import DshJsonRpcTransport

        transport = DshJsonRpcTransport(workspace_dir=str(tmp_path), dsh_api_key="deepseek")
        with (
            patch("skuld.transports.dsh.resolve_dsh_launch_args", return_value=["dsh"]),
            patch("skuld.transports.dsh.resolve_dsh_cordis_config", return_value="/c.yaml"),
        ):
            env = await _spawn_env("skuld.transports.dsh", transport.start)
        _assert_scrubbed(env)
        assert env["DEEPSEEK_API_KEY"] == "deepseek"

    async def test_remote_control_env(self, broker_env: None, tmp_path) -> None:
        from skuld.transports.remote_control import RemoteControlTransport

        transport = RemoteControlTransport(workspace_dir=str(tmp_path))
        env = await _spawn_env("skuld.transports.remote_control", transport.start)
        _assert_scrubbed(env)
        assert "ANTHROPIC_API_KEY" not in env


def test_every_engine_module_builds_env_through_the_scrubber() -> None:
    """Guard against a new ``dict(os.environ)`` spawn creeping back into a transport."""
    from pathlib import Path

    import skuld.transports as transports_pkg

    root = Path(transports_pkg.__file__).parent
    offenders = [
        path.name
        for path in root.glob("*.py")
        if path.name not in {"session_env.py", "owned_codex_process.py"}
        and ("dict(os.environ)" in path.read_text() or "**os.environ" in path.read_text())
    ]
    assert offenders == []
