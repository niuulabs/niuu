"""Tests for `niuu guild pair`, `niuu join`, `niuu leave` (mocked HTTP)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml
from typer.testing import CliRunner

from cli.api.guild import GuildAPIError
from cli.app import build_app
from cli.auth.credentials import StoredTokens
from cli.config import CLISettings
from cli.registry import PluginRegistry

runner = CliRunner()


def _build_test_app(settings: CLISettings | None = None):
    registry = PluginRegistry()
    app = build_app(settings=settings or CLISettings(), registry=registry)
    return app


class TestGuildPair:
    def test_requires_login(self) -> None:
        app = _build_test_app()
        with patch("cli.auth.credentials.CredentialStore.load", return_value=None):
            result = runner.invoke(app, ["guild", "pair", "https://guild.example.com"])
        assert result.exit_code == 1
        assert "not authenticated" in result.output.lower()

    def test_mints_and_prints_the_code(self) -> None:
        app = _build_test_app()
        tokens = StoredTokens(access_token="operator-token")
        minted = {"code": "the-pairing-code", "expiresAt": "2024-01-01T00:10:00Z"}
        with (
            patch("cli.auth.credentials.CredentialStore.load", return_value=tokens),
            patch("cli.api.guild.mint_pairing_code", AsyncMock(return_value=minted)),
        ):
            result = runner.invoke(app, ["guild", "pair", "https://guild.example.com"])
        assert result.exit_code == 0
        assert "the-pairing-code" in result.output

    def test_reports_guild_api_errors(self) -> None:
        app = _build_test_app()
        tokens = StoredTokens(access_token="operator-token")
        with (
            patch("cli.auth.credentials.CredentialStore.load", return_value=tokens),
            patch(
                "cli.api.guild.mint_pairing_code",
                AsyncMock(side_effect=GuildAPIError("Guild returned 403: not an admin")),
            ),
        ):
            result = runner.invoke(app, ["guild", "pair", "https://guild.example.com"])
        assert result.exit_code == 1
        assert "not an admin" in result.output


class TestJoin:
    def test_persists_node_id_and_guild_url(self, tmp_path: Path, monkeypatch) -> None:
        config_file = tmp_path / "config.yaml"
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        app = _build_test_app()
        join_result = {
            "nodeId": "node-123",
            "instances": [],
            "identity": {"mode": "oidc", "issuers": []},
        }
        fake_identity = AsyncMock()
        fake_identity.public_key_b64 = "cHVibGljLWtleQ=="
        with (
            patch("cli.auth.node_key.NodeIdentity.load_or_create", return_value=fake_identity),
            patch("cli.api.guild.join", AsyncMock(return_value=join_result)),
        ):
            result = runner.invoke(
                app,
                ["join", "https://guild.example.com", "--code", "the-code", "--name", "spark-1"],
            )

        assert result.exit_code == 0, result.output
        assert "node-123" in result.output
        saved = yaml.safe_load(config_file.read_text())
        assert saved["guild"] == {"url": "https://guild.example.com", "node_id": "node-123"}

    def test_reports_guild_api_errors(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NIUU_CONFIG", str(tmp_path / "config.yaml"))
        app = _build_test_app()
        fake_identity = AsyncMock()
        fake_identity.public_key_b64 = "cHVibGljLWtleQ=="
        with (
            patch("cli.auth.node_key.NodeIdentity.load_or_create", return_value=fake_identity),
            patch(
                "cli.api.guild.join",
                AsyncMock(side_effect=GuildAPIError("Guild returned 401: invalid code")),
            ),
        ):
            result = runner.invoke(app, ["join", "https://guild.example.com", "--code", "bad-code"])

        assert result.exit_code == 1
        assert "invalid code" in result.output


class TestLeave:
    def test_requires_a_prior_join(self) -> None:
        app = _build_test_app(CLISettings())
        result = runner.invoke(app, ["leave"])
        assert result.exit_code == 1
        assert "not joined" in result.output.lower()

    def test_leaves_and_clears_persisted_state(self, tmp_path: Path, monkeypatch) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump({"guild": {"url": "https://guild.example.com", "node_id": "node-123"}})
        )
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        settings = CLISettings()
        assert settings.guild.node_id == "node-123"
        app = _build_test_app(settings)
        fake_identity = AsyncMock()
        with (
            patch("cli.auth.node_key.NodeIdentity.load_or_create", return_value=fake_identity),
            patch("cli.api.guild.leave", AsyncMock(return_value=None)),
        ):
            result = runner.invoke(app, ["leave"])

        assert result.exit_code == 0, result.output
        saved = yaml.safe_load(config_file.read_text())
        assert saved["guild"] == {"url": "", "node_id": ""}
