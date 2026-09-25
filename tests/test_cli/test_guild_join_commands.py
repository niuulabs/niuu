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


def _joinable_settings() -> CLISettings:
    settings = CLISettings()
    settings.server.external_host = "spark-1.lan"
    return settings


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
            patch("cli.api.guild.mint_pairing_code", AsyncMock(return_value=minted)) as mint_mock,
        ):
            result = runner.invoke(app, ["guild", "pair", "https://guild.example.com"])
        assert result.exit_code == 0
        assert "the-pairing-code" in result.output
        # Defaults both consents to False unless the operator asks for them.
        assert mint_mock.await_args.kwargs["allow_plaintext"] is False
        assert mint_mock.await_args.kwargs["allow_untrusted_node_auth"] is False

    def test_passes_through_the_explicit_consent_flags(self) -> None:
        app = _build_test_app()
        tokens = StoredTokens(access_token="operator-token")
        minted = {"code": "c", "expiresAt": "2024-01-01T00:10:00Z"}
        with (
            patch("cli.auth.credentials.CredentialStore.load", return_value=tokens),
            patch("cli.api.guild.mint_pairing_code", AsyncMock(return_value=minted)) as mint_mock,
        ):
            result = runner.invoke(
                app,
                [
                    "guild",
                    "pair",
                    "https://guild.example.com",
                    "--allow-plaintext",
                    "--allow-untrusted-node-auth",
                ],
            )
        assert result.exit_code == 0
        assert mint_mock.await_args.kwargs["allow_plaintext"] is True
        assert mint_mock.await_args.kwargs["allow_untrusted_node_auth"] is True

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
    def test_requires_an_external_host(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NIUU_CONFIG", str(tmp_path / "config.yaml"))
        app = _build_test_app(CLISettings())  # external_host left unset

        result = runner.invoke(app, ["join", "https://guild.example.com", "--code", "the-code"])

        assert result.exit_code == 1
        assert "server.external_host" in result.output

    def test_persists_node_id_guild_url_and_wires_identity_trust_into_host_auth(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        config_file = tmp_path / "config.yaml"
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        app = _build_test_app(_joinable_settings())
        join_result = {
            "nodeId": "node-123",
            "instances": [],
            "identity": {
                "mode": "oidc",
                "issuers": [{"issuer": "https://idp.example.com", "audiences": ["x"]}],
            },
        }
        fake_identity = AsyncMock()
        fake_identity.public_key_b64 = "cHVibGljLWtleQ=="
        with (
            patch("cli.auth.node_key.NodeIdentity.load_or_create", return_value=fake_identity),
            patch("cli.api.guild.join", AsyncMock(return_value=join_result)) as join_mock,
        ):
            result = runner.invoke(
                app,
                ["join", "https://guild.example.com", "--code", "the-code", "--name", "spark-1"],
            )

        assert result.exit_code == 0, result.output
        assert "node-123" in result.output
        assert join_mock.await_args.kwargs["node_auth_mode"] == "none"  # default host_auth.mode
        saved = yaml.safe_load(config_file.read_text())
        assert saved["guild"] == {"url": "https://guild.example.com", "node_id": "node-123"}
        assert saved["host_auth"] == {
            "mode": "oidc",
            "oidc": {"issuers": [{"issuer": "https://idp.example.com", "audiences": ["x"]}]},
        }

    def test_reports_guild_api_errors(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("NIUU_CONFIG", str(tmp_path / "config.yaml"))
        app = _build_test_app(_joinable_settings())
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

    def test_refuses_to_generate_a_new_key_when_none_is_on_disk(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Blocker-adjacent hardening: `niuu leave` must never silently mint
        a fresh keypair that wouldn't match what Guild has on file."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump({"guild": {"url": "https://guild.example.com", "node_id": "node-123"}})
        )
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        app = _build_test_app(CLISettings())

        with patch("cli.config.DEFAULT_CONFIG_DIR", tmp_path):
            result = runner.invoke(app, ["leave"])

        assert result.exit_code == 1
        assert "No node key" in result.output

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
            patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
            patch("cli.api.guild.leave", AsyncMock(return_value=None)),
        ):
            result = runner.invoke(app, ["leave"])

        assert result.exit_code == 0, result.output
        saved = yaml.safe_load(config_file.read_text())
        assert saved["guild"] == {"url": "", "node_id": ""}


class TestHeartbeatCommand:
    def test_once_sends_a_single_heartbeat(self, tmp_path: Path, monkeypatch) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            yaml.safe_dump({"guild": {"url": "https://guild.example.com", "node_id": "node-123"}})
        )
        monkeypatch.setenv("NIUU_CONFIG", str(config_file))
        settings = CLISettings()
        settings.server.external_host = "spark-1.lan"
        app = _build_test_app(settings)
        fake_identity = AsyncMock()
        with (
            patch("cli.auth.node_key.NodeIdentity.load", return_value=fake_identity),
            patch(
                "cli.services.guild_heartbeat.heartbeat", AsyncMock(return_value={"ok": True})
            ) as hb_mock,
        ):
            result = runner.invoke(app, ["guild", "heartbeat", "--once"])

        assert result.exit_code == 0, result.output
        hb_mock.assert_awaited_once()

    def test_reports_not_joined(self) -> None:
        app = _build_test_app(CLISettings())
        result = runner.invoke(app, ["guild", "heartbeat", "--once"])
        assert result.exit_code == 1
        assert "niuu join" in result.output
