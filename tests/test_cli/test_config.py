"""Tests for cli.config — CLISettings."""

from __future__ import annotations

import pytest

from cli.config import (
    AuthConfig,
    AuthOidcConfig,
    CLISettings,
    DatabaseConfig,
    OidcIssuerConfig,
    PluginConfig,
    PodManagerConfig,
    ServerConfig,
    ServiceConfig,
    TUIConfig,
    auth_adapter_env,
)


class TestCLISettings:
    def test_defaults(self) -> None:
        settings = CLISettings()
        assert settings.version == "0.1.0"
        assert settings.context == "local"
        assert settings.mode == "mini"
        assert isinstance(settings.plugins, PluginConfig)
        assert isinstance(settings.services, ServiceConfig)
        assert isinstance(settings.tui, TUIConfig)
        assert isinstance(settings.database, DatabaseConfig)
        assert isinstance(settings.pod_manager, PodManagerConfig)
        assert isinstance(settings.server, ServerConfig)

    def test_plugin_config_defaults(self) -> None:
        config = PluginConfig()
        assert config.enabled == {}
        assert config.extra == []

    def test_service_config_defaults(self) -> None:
        config = ServiceConfig()
        assert config.health_check_interval_seconds == 2.0
        assert config.health_check_timeout_seconds == 30.0
        assert config.health_check_max_retries == 15

    def test_tui_config_defaults(self) -> None:
        config = TUIConfig()
        assert config.theme == "textual-dark"

    def test_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIUU_CONTEXT", "remote")
        settings = CLISettings()
        assert settings.context == "remote"

    def test_docker_compute_requires_matching_vm_pod_manager(self) -> None:
        compute = {
            "pool_id": "acme",
            "max_machines": 1,
            "provider": {"adapter": "private.Provider"},
            "auth": {"adapter": "private.Auth"},
            "runtime": {"adapter": "private.Runtime"},
        }
        with pytest.raises(ValueError, match="VmPodManager"):
            CLISettings(mode="docker", compute=compute)

        configured = CLISettings(
            mode="docker",
            compute=compute,
            pod_manager={
                "adapter": "volundr.adapters.outbound.vm_pod_manager.VmPodManager",
                "profile": "cpu",
                "pool_id": "acme",
                "max_machines": 1,
            },
        )
        assert configured.compute is not None
        assert configured.compute.pool_id == "acme"


class TestDatabaseConfig:
    def test_defaults(self) -> None:
        config = DatabaseConfig()
        assert config.mode == "embedded"
        assert config.dsn == ""

    def test_external_mode(self) -> None:
        config = DatabaseConfig(mode="external", dsn="postgresql://localhost/niuu")
        assert config.mode == "external"
        assert config.dsn == "postgresql://localhost/niuu"


class TestPodManagerConfig:
    def test_defaults(self) -> None:
        config = PodManagerConfig()
        assert "LocalProcessPodManager" in config.adapter
        assert config.workspaces_dir == "~/.niuu/workspaces"
        assert config.claude_binary == "claude"
        assert config.max_concurrent == 4

    def test_custom_adapter(self) -> None:
        config = PodManagerConfig(adapter="custom.adapter.PodManager")
        assert config.adapter == "custom.adapter.PodManager"


class TestServerConfig:
    def test_defaults(self) -> None:
        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8080

    def test_custom_port(self) -> None:
        config = ServerConfig(port=9090)
        assert config.port == 9090


class TestAuthConfig:
    def test_default_is_none_explicit_no_auth(self) -> None:
        settings = CLISettings()
        assert settings.host_auth.mode == "none"
        assert settings.host_auth.oidc.issuers == []

    def test_oidc_without_issuers_raises_with_remedy(self) -> None:
        with pytest.raises(ValueError, match="auth.oidc.issuers"):
            AuthConfig(mode="oidc")

    def test_oidc_with_issuer_missing_audience_raises(self) -> None:
        with pytest.raises(ValueError):
            AuthConfig(
                mode="oidc",
                oidc=AuthOidcConfig(issuers=[OidcIssuerConfig(issuer="https://kc.example")]),
            )

    def test_oidc_with_valid_issuer_is_accepted(self) -> None:
        config = AuthConfig(
            mode="oidc",
            oidc=AuthOidcConfig(
                issuers=[
                    OidcIssuerConfig(issuer="https://kc.example/realms/volundr", audiences=["api"])
                ]
            ),
        )
        assert config.mode == "oidc"

    def test_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValueError):
            AuthConfig(mode="envoy")


class TestAuthAdapterEnv:
    def test_none_mode_env(self) -> None:
        env = auth_adapter_env(AuthConfig())
        assert env["IDENTITY__ADAPTER"].endswith("AllowAllIdentityAdapter")
        assert env["AUTHORIZATION__ADAPTER"].endswith("AllowAllAuthorizationAdapter")
        assert env["RAVN_API_AUTH__ADAPTER"].endswith("AllowAllHeaderAuthenticationAdapter")
        assert env["HOST_IDENTITY__ADAPTER"].endswith("AllowAllHeaderAuthenticationAdapter")
        assert env["AUTH__ALLOW_ANONYMOUS_DEV"] == "true"
        assert env["AUTH_MODE"] == "none"
        assert "IDENTITY__KWARGS" not in env
        # 'none' also switches Ting off its Envoy-trusting default onto the
        # same explicit allow-all adapter every other service gets.
        assert env["AUTH__ADAPTER"].endswith("AllowAllHeaderAuthenticationAdapter")

    def test_oidc_mode_env(self) -> None:
        import json

        auth = AuthConfig(
            mode="oidc",
            oidc=AuthOidcConfig(
                issuers=[
                    OidcIssuerConfig(
                        issuer="https://kc.example/realms/volundr",
                        audiences=["volundr-api"],
                        jwks_uri="https://kc.example/realms/volundr/protocol/openid-connect/certs",
                    )
                ]
            ),
        )
        env = auth_adapter_env(auth)

        assert env["IDENTITY__ADAPTER"] == "identity.adapters.jwks.JwksIdentityAdapter"
        assert env["AUTHORIZATION__ADAPTER"] == "identity.adapters.cedar.CedarAuthorizationAdapter"
        assert (
            env["RAVN_API_AUTH__ADAPTER"]
            == "identity.adapters.jwks.JwksBearerAuthenticationAdapter"
        )
        assert env["AUTH__ADAPTER"] == "identity.adapters.jwks.JwksBearerAuthenticationAdapter"
        assert (
            env["HOST_IDENTITY__ADAPTER"]
            == "identity.adapters.jwks.JwksBearerAuthenticationAdapter"
        )
        assert env["AUTH__ALLOW_ANONYMOUS_DEV"] == "false"
        assert env["AUTH_MODE"] == "oidc"

        kwargs = json.loads(env["IDENTITY__KWARGS"])
        assert kwargs["issuers"][0]["issuer"] == "https://kc.example/realms/volundr"
        assert kwargs["issuers"][0]["audiences"] == ["volundr-api"]
        assert kwargs["role_mapping"] == {
            "admin": "volundr:admin",
            "developer": "volundr:developer",
            "viewer": "volundr:viewer",
        }
        assert json.loads(env["RAVN_API_AUTH__KWARGS"]) == kwargs
        assert json.loads(env["AUTH__KWARGS"]) == kwargs
        assert json.loads(env["HOST_IDENTITY__KWARGS"]) == kwargs


class TestOidcCoverageGate:
    _ISSUER_KWARGS = {
        "mode": "oidc",
        "oidc": {
            "issuers": [{"issuer": "https://kc.example/realms/volundr", "audiences": ["api"]}]
        },
    }

    def test_oidc_blocked_while_mimir_plugin_enabled_by_default(self) -> None:
        with pytest.raises(ValueError, match="mimir"):
            CLISettings(host_auth=self._ISSUER_KWARGS)

    def test_oidc_allowed_once_mimir_disabled_and_bifrost_not_open(self) -> None:
        settings = CLISettings(
            host_auth=self._ISSUER_KWARGS,
            plugins={"enabled": {"mimir": False, "guild": False}},
            bifrost={"auth_mode": "pat"},
        )
        assert settings.host_auth.mode == "oidc"

    def test_oidc_allowed_with_guild_enabled(self) -> None:
        """Guild forwards only the caller's bearer token to a remote instance
        (see niuu.adapters.inbound.remote_urls.forward_identity_headers), so
        it no longer needs to be disabled for auth.mode: oidc — unlike mimir,
        which still trusts x-auth-* headers directly."""
        settings = CLISettings(
            host_auth=self._ISSUER_KWARGS,
            plugins={"enabled": {"mimir": False}},
            bifrost={"auth_mode": "pat"},
        )
        assert settings.host_auth.mode == "oidc"
        assert settings.plugins.enabled.get("guild", True) is True

    def test_oidc_blocked_while_bifrost_auth_mode_open(self) -> None:
        with pytest.raises(ValueError, match="bifrost.auth_mode"):
            CLISettings(
                host_auth=self._ISSUER_KWARGS,
                plugins={"enabled": {"mimir": False, "guild": False}},
            )

    def test_oidc_allowed_with_bifrost_pat_mode(self) -> None:
        settings = CLISettings(
            host_auth=self._ISSUER_KWARGS,
            plugins={"enabled": {"mimir": False, "guild": False}},
            bifrost={"auth_mode": "pat"},
        )
        assert settings.bifrost.auth_mode == "pat"

    def test_none_mode_ignores_uncovered_plugins(self) -> None:
        settings = CLISettings()
        assert settings.host_auth.mode == "none"
