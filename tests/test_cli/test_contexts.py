"""Tests for named contexts: `niuu context …`, resolution, plugins and login."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from cli.app import build_app
from cli.auth.credentials import StoredTokens
from cli.config import CLISettings, ContextConfig, persist_contexts
from cli.context import ContextError, active, contexts, find, local_url, login_for, refresher
from cli.registry import PluginRegistry
from niuu.ports.plugin import CLITarget, ServicePlugin

runner = CliRunner()


def _config() -> Path:
    return Path(os.environ["NIUU_CONFIG"])


def _app(settings: CLISettings | None = None, registry: PluginRegistry | None = None):
    return build_app(settings=settings or CLISettings(), registry=registry or PluginRegistry())


def _ymir(**overrides) -> CLISettings:
    return CLISettings(
        contexts={"ymir": ContextConfig(url="https://ymir.example/", **overrides)}, **{}
    )


class TestContextConfig:
    def test_url_must_be_http_with_a_host(self) -> None:
        with pytest.raises(ValueError, match="http:// or https://"):
            ContextConfig(url="ftp://x")
        assert ContextConfig(url=" https://a.example/ ").url == "https://a.example"

    def test_settings_refuse_bad_names(self) -> None:
        with pytest.raises(ValueError, match="contexts.local: 'local' is this machine"):
            CLISettings(contexts={"local": ContextConfig(url="https://a.example")})
        with pytest.raises(ValueError, match="contexts.Prod"):
            CLISettings(contexts={"Prod": ContextConfig(url="https://a.example")})

    def test_persist_merges_into_existing_config(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("server:\n  port: 9000\n")
        persist_contexts(
            contexts={"ymir": ContextConfig(url="https://ymir.example", forge="ymir")},
            active="ymir",
            config_file=target,
        )
        data = yaml.safe_load(target.read_text())
        assert data == {
            "server": {"port": 9000},
            "contexts": {"ymir": {"url": "https://ymir.example", "forge": "ymir"}},
            "context": "ymir",
        }


class TestResolution:
    def test_local_is_built_in(self) -> None:
        settings = CLISettings()
        assert active(settings).url == "http://127.0.0.1:8080"
        assert active(settings).local

    @pytest.mark.parametrize(
        ("host", "url"),
        [
            ("0.0.0.0", "http://127.0.0.1:8080"),
            ("::", "http://127.0.0.1:8080"),
            ("::1", "http://[::1]:8080"),
        ],
    )
    def test_local_url_connects_to_loopback(self, host: str, url: str) -> None:
        settings = CLISettings()
        settings.server.host = host
        assert local_url(settings) == url

    def test_named_context(self) -> None:
        settings = _ymir(forge="ymir", issuer="https://kc/realms/niuu")
        settings.context = "ymir"
        context = active(settings)
        assert (context.name, context.url, context.forge, context.issuer) == (
            "ymir",
            "https://ymir.example",
            "ymir",
            "https://kc/realms/niuu",
        )
        assert not context.local
        assert [c.name for c in contexts(settings)] == ["local", "ymir"]

    def test_an_unknown_active_context_is_an_error_not_local(self) -> None:
        settings = CLISettings(context="gone")
        with pytest.raises(ContextError, match="'gone' is not configured.*niuu context add gone"):
            active(settings)
        with pytest.raises(ContextError):
            find(settings, "other")


class TestLoginFor:
    def _store(self, tokens: StoredTokens | None):
        store = type("Store", (), {"load": lambda self: tokens})()
        return store

    def test_no_issuer_takes_any_login(self) -> None:
        tokens = StoredTokens(access_token="t", issuer="https://kc/a")
        assert login_for(find(CLISettings(), "local"), self._store(tokens)) is tokens
        assert login_for(find(CLISettings(), "local"), self._store(None)) is None

    def test_matching_issuer(self) -> None:
        settings = _ymir(issuer="https://kc/a/")
        tokens = StoredTokens(access_token="t", issuer="https://kc/a")
        assert login_for(find(settings, "ymir"), self._store(tokens)) is tokens

    def test_other_issuer_is_refused(self) -> None:
        settings = _ymir(issuer="https://kc/a")
        tokens = StoredTokens(access_token="t", issuer="")
        with pytest.raises(ContextError, match="logged in to an unknown issuer"):
            login_for(find(settings, "ymir"), self._store(tokens))

    def test_refresher_needs_issuer_and_client(self) -> None:
        store = self._store(None)
        assert refresher(None, store) is None
        assert refresher(StoredTokens(access_token="t", refresh_token="r"), store) is None
        full = StoredTokens(
            access_token="t", refresh_token="r", issuer="https://kc", client_id="niuu-cli"
        )
        assert refresher(full, store) is not None


class TestContextCommands:
    def test_list_marks_the_active_one(self) -> None:
        settings = _ymir(forge="ymir")
        result = runner.invoke(_app(settings), ["context", "list"])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0].split() == ["NAME", "URL", "FORGE", "ISSUER"]
        assert lines[1].split()[:3] == ["*", "local", "http://127.0.0.1:8080"]
        assert "(this machine)" in lines[1]
        assert lines[2].split() == ["ymir", "https://ymir.example", "ymir", "-"]

    def test_list_flags_an_unconfigured_active_context(self) -> None:
        result = runner.invoke(_app(CLISettings(context="gone")), ["context", "list"])
        assert result.exit_code == 1
        assert "'gone' is not configured" in result.output

    def test_add_then_use(self) -> None:
        result = runner.invoke(
            _app(),
            [
                "context",
                "add",
                "ymir",
                "https://ymir.example",
                "--forge",
                "ymir",
                "--issuer",
                "https://kc/realms/niuu",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "Added context 'ymir' → https://ymir.example" in result.output
        data = yaml.safe_load(_config().read_text())
        assert data["contexts"]["ymir"] == {
            "url": "https://ymir.example",
            "forge": "ymir",
            "issuer": "https://kc/realms/niuu",
        }
        assert "context" not in data
        result = runner.invoke(_app(CLISettings()), ["context", "use", "ymir"])
        assert result.exit_code == 0, result.output
        assert "Switched to context 'ymir' (https://ymir.example)." in result.output
        assert yaml.safe_load(_config().read_text())["context"] == "ymir"

    def test_add_with_use(self) -> None:
        result = runner.invoke(_app(), ["context", "add", "lab", "http://lab:8080", "--use"])
        assert result.exit_code == 0, result.output
        assert yaml.safe_load(_config().read_text())["context"] == "lab"
        assert "Switched to context 'lab'." in result.output

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            (["local", "https://a.example"], "'local' is this machine's own server"),
            (["Bad Name", "https://a.example"], "a context name is lowercase"),
            (["lab", "lab.example"], "http:// or https://"),
        ],
    )
    def test_add_refuses(self, args: list[str], message: str) -> None:
        result = runner.invoke(_app(), ["context", "add", *args])
        assert result.exit_code == 1
        assert message in result.output
        assert not _config().exists()

    def test_add_refuses_an_existing_name(self) -> None:
        result = runner.invoke(_app(_ymir()), ["context", "add", "ymir", "https://b.example"])
        assert result.exit_code == 1
        assert "already exists; delete it first" in result.output

    def test_use_refuses_an_unknown_context(self) -> None:
        result = runner.invoke(_app(), ["context", "use", "prod"])
        assert result.exit_code == 1
        assert "'prod' is not configured" in result.output
        assert not _config().exists()

    def test_use_local(self) -> None:
        result = runner.invoke(_app(_ymir()), ["context", "use", "local"])
        assert result.exit_code == 0, result.output
        assert yaml.safe_load(_config().read_text())["context"] == "local"

    def test_delete(self) -> None:
        result = runner.invoke(_app(_ymir()), ["context", "delete", "ymir"])
        assert result.exit_code == 0, result.output
        assert yaml.safe_load(_config().read_text())["contexts"] == {}

    @pytest.mark.parametrize(
        ("name", "message"),
        [
            ("local", "cannot be removed"),
            ("prod", "'prod' is not configured"),
            ("ymir", "is the active context; switch first"),
        ],
    )
    def test_delete_refuses(self, name: str, message: str) -> None:
        settings = _ymir()
        settings.context = "ymir"
        result = runner.invoke(_app(settings), ["context", "delete", name])
        assert result.exit_code == 1
        assert message in result.output

    def test_config_show_names_the_context_url(self) -> None:
        settings = _ymir()
        settings.context = "ymir"
        result = runner.invoke(_app(settings), ["config", "show"])
        assert "Context: ymir (https://ymir.example)" in result.output
        result = runner.invoke(_app(CLISettings(context="gone")), ["config", "show"])
        assert "Context: gone (not configured" in result.output


class _Plugin(ServicePlugin):
    @property
    def name(self) -> str:
        return "probe"

    @property
    def description(self) -> str:
        return "probe"

    def register_commands(self, app) -> None:
        plugin = self

        @app.command("probe")
        def probe() -> None:
            client = plugin.cli_client("Probe")
            print(f"{client._base_url} {client._auth_token}")


class TestPluginTarget:
    def _registry(self) -> PluginRegistry:
        registry = PluginRegistry()
        registry.register(_Plugin())
        return registry

    def test_plugins_use_the_active_context_and_login(self) -> None:
        settings = _ymir()
        settings.context = "ymir"
        tokens = StoredTokens(access_token="operator-token")
        with patch("cli.auth.credentials.CredentialStore.load", return_value=tokens):
            result = runner.invoke(_app(settings, self._registry()), ["probe"])
        assert result.exit_code == 0, result.output
        assert result.output.strip() == "https://ymir.example operator-token"

    def test_an_unknown_context_stops_plugin_commands(self) -> None:
        result = runner.invoke(_app(CLISettings(context="gone"), self._registry()), ["probe"])
        assert result.exit_code == 1
        assert "'gone' is not configured" in result.output

    def test_a_plugin_outside_the_app_talks_to_the_local_default(self) -> None:
        client = _Plugin().cli_client("Probe")
        assert client._base_url == CLITarget().base_url == "http://localhost:8080"
        assert client._auth_token is None


class TestLoginIssuer:
    def test_login_takes_the_context_issuer(self) -> None:
        settings = _ymir(issuer="https://kc/realms/niuu")
        settings.context = "ymir"
        login = AsyncMock(return_value=StoredTokens(access_token="t"))
        with (
            patch("cli.auth.oidc.OIDCClient.__init__", return_value=None) as init,
            patch("cli.auth.oidc.OIDCClient.login", login),
        ):
            result = runner.invoke(_app(settings), ["login"])
        assert result.exit_code == 0, result.output
        assert init.call_args.kwargs["issuer"] == "https://kc/realms/niuu"

    def test_login_with_an_unknown_context(self) -> None:
        result = runner.invoke(_app(CLISettings(context="gone")), ["login"])
        assert result.exit_code == 1
        assert "'gone' is not configured" in result.output

    def test_login_without_any_issuer(self) -> None:
        result = runner.invoke(_app(), ["login"])
        assert result.exit_code == 1
        assert "--issuer is required" in result.output
