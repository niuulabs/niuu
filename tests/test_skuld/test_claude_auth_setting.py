"""``claude_auth`` and friends are Skuld settings, handed to the Claude transports.

SKULD__CLAUDE_AUTH / SKULD__CLAUDE_OAUTH_TOKEN_FILE / SKULD__TMUX_REMOTE_CONTROL stay the
deploy overrides, but only through the settings model: an unknown value is refused when the
settings load, and the transports receive the parsed value rather than reading the
environment themselves.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from skuld.broker import Broker
from skuld.config import SkuldSettings
from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
from skuld.transports.remote_control import RemoteControlTransport
from skuld.transports.sdk import SDKTransport
from skuld.transports.sdk_websocket import SdkWebSocketTransport
from skuld.transports.subprocess import SubprocessTransport
from skuld.transports.tmux_interactive import TmuxInteractiveTransport

TMUX = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
SDK = "skuld.transports.sdk.SDKTransport"
PERSISTENT = "skuld.transports.persistent_subprocess.PersistentSubprocessTransport"
SUBPROCESS = "skuld.transports.subprocess.SubprocessTransport"
SDK_WS = "skuld.transports.sdk_websocket.SdkWebSocketTransport"
REMOTE_CONTROL = "skuld.transports.remote_control.RemoteControlTransport"


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch):
    """No node config file and none of the knobs under test leak in from the host."""
    monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])
    for name in (
        "SKULD__CLAUDE_AUTH",
        "SKULD__CLAUDE_OAUTH_TOKEN_FILE",
        "SKULD__TMUX_REMOTE_CONTROL",
    ):
        monkeypatch.delenv(name, raising=False)


def _broker(tmp_path: Path, adapter: str, **settings) -> Broker:
    (tmp_path / "ws").mkdir(exist_ok=True)
    return Broker(
        settings=SkuldSettings(
            session={"id": "s-1", "workspace_dir": str(tmp_path / "ws"), "model": "m"},
            transport_adapter=adapter,
            skip_permissions=True,
            port=18765,
            host="127.0.0.1",
            forge_mcp={"runtime_dir": str(tmp_path / "rt")},
            **settings,
        )
    )


class TestSettings:
    def test_defaults(self) -> None:
        settings = SkuldSettings()
        assert settings.claude_auth == "subscription"
        assert settings.claude_oauth_token_file is None
        assert settings.tmux_remote_control is True

    def test_env_overrides_parse_into_the_settings(self, monkeypatch) -> None:
        monkeypatch.setenv("SKULD__CLAUDE_AUTH", "api_key")
        monkeypatch.setenv("SKULD__TMUX_REMOTE_CONTROL", "0")
        settings = SkuldSettings()
        assert settings.claude_auth == "api_key"
        assert settings.tmux_remote_control is False

    def test_token_file_env_parses_into_the_settings(self, monkeypatch) -> None:
        monkeypatch.setenv("SKULD__CLAUDE_OAUTH_TOKEN_FILE", "/run/secrets/claude-token")
        assert SkuldSettings().claude_oauth_token_file == "/run/secrets/claude-token"

    @pytest.mark.parametrize("value", ["whatever", "API_KEY", " api_key ", ""])
    def test_unknown_claude_auth_from_env_is_refused(self, monkeypatch, value) -> None:
        monkeypatch.setenv("SKULD__CLAUDE_AUTH", value)
        with pytest.raises(ValidationError, match="claude_auth"):
            SkuldSettings()

    def test_unknown_claude_auth_argument_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="claude_auth"):
            SkuldSettings(claude_auth="oauth")

    @pytest.mark.parametrize("emitted", ["subscription", "api_key"])
    def test_values_the_integrations_contributor_emits_are_accepted(
        self, monkeypatch, emitted
    ) -> None:
        """volundr's IntegrationContributor puts exactly these two values in SKULD__CLAUDE_AUTH."""
        monkeypatch.setenv("SKULD__CLAUDE_AUTH", emitted)
        assert SkuldSettings().claude_auth == emitted


@pytest.mark.parametrize(
    ("adapter", "klass"),
    [
        (SDK, SDKTransport),
        (PERSISTENT, PersistentSubprocessTransport),
        (SUBPROCESS, SubprocessTransport),
        (SDK_WS, SdkWebSocketTransport),
        (TMUX, TmuxInteractiveTransport),
    ],
)
class TestTransportsReceiveTheSettings:
    def test_claude_auth_and_token_file_reach_the_transport(self, tmp_path, adapter, klass) -> None:
        transport = _broker(
            tmp_path,
            adapter,
            claude_auth="api_key",
            claude_oauth_token_file="/test-only/token",
        )._create_transport()
        assert isinstance(transport, klass)
        assert transport._claude_oauth_token_file == "/test-only/token"
        # The mode lands wherever the transport keeps it; the spawn env proves it below.
        mode = getattr(transport, "_claude_auth", None) or transport._claude_auth_mode
        assert mode == "api_key"


class TestChildEnvFollowsTheSetting:
    ENV = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-platform"}

    def test_api_key_setting_keeps_the_platform_key_for_sdk(self, tmp_path) -> None:
        transport = _broker(tmp_path, SDK, claude_auth="api_key")._create_transport()
        with patch.dict(os.environ, self.ENV, clear=True):
            env = transport._spawn_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-platform"

    def test_default_setting_strips_the_platform_key_for_sdk(self, tmp_path) -> None:
        transport = _broker(tmp_path, SDK)._create_transport()
        with patch.dict(os.environ, self.ENV, clear=True):
            env = transport._spawn_env()
        assert "ANTHROPIC_API_KEY" not in env

    def test_api_key_setting_keeps_the_platform_key_for_tmux(self, tmp_path) -> None:
        transport = _broker(tmp_path, TMUX, claude_auth="api_key")._create_transport()
        with patch.dict(os.environ, self.ENV, clear=True):
            env = transport._spawn_env()
        assert env["ANTHROPIC_API_KEY"] == "sk-platform"

    async def test_api_key_setting_keeps_the_platform_key_for_the_spawned_cli(
        self, tmp_path
    ) -> None:
        captured: dict = {}

        async def capture(*_command, **kwargs):
            captured["env"] = kwargs["env"]
            raise RuntimeError("synthetic spawn intercepted")

        transport = _broker(tmp_path, PERSISTENT, claude_auth="api_key")._create_transport()
        with (
            patch.dict(os.environ, self.ENV, clear=True),
            patch("asyncio.create_subprocess_exec", side_effect=capture),
            pytest.raises(RuntimeError, match="synthetic spawn intercepted"),
        ):
            await transport._spawn()
        assert captured["env"]["ANTHROPIC_API_KEY"] == "sk-platform"


class TestTmuxRemoteControlSetting:
    def test_setting_off_drops_the_remote_control_flag(self, tmp_path) -> None:
        transport = _broker(tmp_path, TMUX, tmux_remote_control=False)._create_transport()
        assert "--remote-control" not in transport._interactive_argv()

    def test_default_adds_the_remote_control_flag(self, tmp_path) -> None:
        transport = _broker(tmp_path, TMUX)._create_transport()
        assert "--remote-control" in transport._interactive_argv()

    def test_api_key_setting_forces_remote_control_off(self, tmp_path) -> None:
        transport = _broker(
            tmp_path, TMUX, claude_auth="api_key", tmux_remote_control=True
        )._create_transport()
        assert "--remote-control" not in transport._interactive_argv()


class TestRemoteControlTransport:
    async def test_token_file_setting_refuses_the_launch(self, tmp_path) -> None:
        transport = _broker(
            tmp_path, REMOTE_CONTROL, claude_oauth_token_file="/test-only/token"
        )._create_transport()
        assert isinstance(transport, RemoteControlTransport)
        with pytest.raises(ValueError, match="for inference, not Remote Control"):
            await transport.start()
