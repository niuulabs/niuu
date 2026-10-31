"""A Forge node chooses how unattended Claude sessions run (``claude_permission_mode``).

Thor and DGX Spark set YOLO (``bypassPermissions``) in their node config; every other
node runs Claude's ``auto`` mode, which is also the default.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from skuld.broker import Broker
from skuld.claude_permission import (
    DEFAULT_CLAUDE_PERMISSION_MODE,
    resolve_claude_permission_mode,
)
from skuld.config import SkuldSettings
from skuld.transports.persistent_subprocess import PersistentSubprocessTransport
from skuld.transports.remote_control import RemoteControlTransport
from skuld.transports.subprocess import SubprocessTransport
from skuld.transports.tmux_interactive import TmuxInteractiveTransport

TMUX = "skuld.transports.tmux_interactive.TmuxInteractiveTransport"
CODEX_WS = "skuld.transports.codex_ws.CodexWebSocketTransport"


@pytest.fixture(autouse=True)
def _no_yaml_config(monkeypatch):
    """Real node config files on the test host must not leak into these tests."""
    monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])
    monkeypatch.delenv("SKULD__CLAUDE_PERMISSION_MODE", raising=False)


def _mode(argv: list[str]) -> str:
    return argv[argv.index("--permission-mode") + 1]


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


class TestResolve:
    def test_default_is_auto(self) -> None:
        assert DEFAULT_CLAUDE_PERMISSION_MODE == "auto"
        assert SkuldSettings().claude_permission_mode == "auto"

    @pytest.mark.parametrize(
        ("value", "mode"),
        [
            ("auto", "auto"),
            ("Auto", "auto"),
            (" BYPASSPERMISSIONS ", "bypassPermissions"),
            ("acceptedits", "acceptEdits"),
        ],
    )
    def test_accepts_unattended_modes_in_any_case(self, value: str, mode: str) -> None:
        assert resolve_claude_permission_mode(value) == mode

    @pytest.mark.parametrize("bad", ["", "yolo", "plan", "manual", "dontAsk", "default"])
    def test_rejects_modes_an_unattended_session_cannot_use(self, bad: str) -> None:
        with pytest.raises(ValueError, match="claude_permission_mode"):
            resolve_claude_permission_mode(bad)


class TestNodeConfig:
    def test_node_config_file_selects_bypass(self, tmp_path: Path, monkeypatch) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("claude_permission_mode: bypassPermissions\n")
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config])

        assert SkuldSettings().claude_permission_mode == "bypassPermissions"

    def test_env_overrides_node_config_file(self, tmp_path: Path, monkeypatch) -> None:
        config = tmp_path / "config.yaml"
        config.write_text("claude_permission_mode: bypassPermissions\n")
        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [config])
        monkeypatch.setenv("SKULD__CLAUDE_PERMISSION_MODE", "auto")

        assert SkuldSettings().claude_permission_mode == "auto"

    def test_a_typo_does_not_stop_other_engines(self, tmp_path: Path) -> None:
        """Only Claude transports validate the mode; a Codex broker on the node still starts."""
        transport = _broker(tmp_path, CODEX_WS, claude_permission_mode="Autoo")._create_transport()

        assert type(transport).__name__ == "CodexWebSocketTransport"

    def test_a_typo_stops_the_claude_broker_with_the_remedy(self, tmp_path: Path) -> None:
        broker = _broker(tmp_path, TMUX, claude_permission_mode="Autoo")

        with pytest.raises(ValueError, match="set one of acceptEdits, auto, bypassPermissions"):
            broker._create_transport()


class TestTransports:
    def test_tmux_passes_the_node_mode(self, tmp_path: Path) -> None:
        transport = TmuxInteractiveTransport(
            str(tmp_path), skip_permissions=True, claude_permission_mode="bypassPermissions"
        )

        assert _mode(transport._interactive_argv()) == "bypassPermissions"

    def test_tmux_defaults_to_auto(self, tmp_path: Path) -> None:
        transport = TmuxInteractiveTransport(str(tmp_path), skip_permissions=True)

        assert _mode(transport._interactive_argv()) == "auto"

    def test_tmux_without_skip_permissions_passes_no_mode(self, tmp_path: Path) -> None:
        transport = TmuxInteractiveTransport(
            str(tmp_path), skip_permissions=False, claude_permission_mode="bypassPermissions"
        )

        assert "--permission-mode" not in transport._interactive_argv()

    def test_persistent_subprocess_passes_the_node_mode(self) -> None:
        transport = PersistentSubprocessTransport(
            "/tmp", skip_permissions=True, claude_permission_mode="bypassPermissions"
        )

        assert _mode(transport._build_command()) == "bypassPermissions"

    def test_subprocess_validates_the_mode(self) -> None:
        with pytest.raises(ValueError, match="claude_permission_mode"):
            SubprocessTransport("/tmp", skip_permissions=True, claude_permission_mode="plan")

    def test_remote_control_follows_the_node_mode(self) -> None:
        transport = RemoteControlTransport(
            "/tmp", skip_permissions=True, claude_permission_mode="bypassPermissions"
        )

        assert _mode(transport._build_command()) == "bypassPermissions"

    def test_remote_control_override_still_wins(self) -> None:
        transport = RemoteControlTransport(
            "/tmp",
            skip_permissions=True,
            claude_permission_mode="auto",
            remote_control_permission_mode="plan",
        )

        assert _mode(transport._build_command()) == "plan"


class TestBrokerComposition:
    def test_broker_hands_the_node_mode_to_the_tmux_transport(self, tmp_path: Path) -> None:
        transport = _broker(
            tmp_path, TMUX, claude_permission_mode="bypassPermissions"
        )._create_transport()

        assert isinstance(transport, TmuxInteractiveTransport)
        assert _mode(transport._interactive_argv()) == "bypassPermissions"

    def test_broker_warns_when_the_node_left_the_default(self, tmp_path: Path, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="skuld"):
            _broker(tmp_path, TMUX)._create_transport()

        assert "Claude permission mode: auto (default; set claude_permission_mode" in caplog.text

    def test_broker_logs_a_node_choice(self, tmp_path: Path, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="skuld"):
            _broker(tmp_path, TMUX, claude_permission_mode="bypassPermissions")._create_transport()

        assert "Claude permission mode: bypassPermissions (set by this node)" in caplog.text

    def test_codex_broker_does_not_log_a_claude_mode(self, tmp_path: Path, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="skuld"):
            _broker(tmp_path, CODEX_WS)._create_transport()

        assert "Claude permission mode" not in caplog.text
