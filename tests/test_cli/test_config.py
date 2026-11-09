"""Tests for cli.config — CLISettings."""

from __future__ import annotations

import pytest

from cli.config import (
    AuthConfig,
    AuthOidcConfig,
    CLISettings,
    DatabaseConfig,
    HostsConfig,
    HostSessionImagesConfig,
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

    def test_compute_slot_settings_default_to_one_session_per_machine(self) -> None:
        compute = CLISettings(
            mode="docker",
            compute=self._compute(),
            pod_manager=self._pod_manager(),
        ).compute

        assert compute is not None
        assert compute.slots_per_machine == 1
        assert compute.max_sessions_per_machine is None
        assert compute.max_machine_age_seconds is None

    def test_compute_slot_settings_are_read_from_the_config_file(self) -> None:
        compute = CLISettings(
            mode="docker",
            compute=self._compute(
                slots_per_machine=4, max_sessions_per_machine=8, max_machine_age_seconds=86400
            ),
            pod_manager=self._pod_manager(),
        ).compute

        assert compute is not None
        assert (compute.slots_per_machine, compute.max_sessions_per_machine) == (4, 8)
        assert compute.max_machine_age_seconds == 86400

    @pytest.mark.parametrize(
        "field", ["slots_per_machine", "max_sessions_per_machine", "max_machine_age_seconds"]
    )
    @pytest.mark.parametrize("value", [0, -1])
    def test_compute_slot_settings_must_be_positive(self, field: str, value: int) -> None:
        with pytest.raises(ValueError):
            CLISettings(
                mode="docker",
                compute=self._compute(**{field: value}),
                pod_manager=self._pod_manager(),
            )

    @staticmethod
    def _compute(**extra) -> dict:
        return {
            "pool_id": "acme",
            "max_machines": 1,
            "provider": {"adapter": "private.Provider"},
            "auth": {"adapter": "private.Auth"},
            "runtime": {"adapter": "private.Runtime"},
            **extra,
        }

    @staticmethod
    def _pod_manager() -> dict:
        return {
            "adapter": "volundr.adapters.outbound.vm_pod_manager.VmPodManager",
            "profile": "cpu",
            "pool_id": "acme",
            "max_machines": 1,
        }


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
        # No session limit unless the operator sets one.
        assert config.max_concurrent is None

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
        assert json.loads(env["AUTH__KWARGS"]) == kwargs
        assert json.loads(env["HOST_IDENTITY__KWARGS"]) == kwargs


class TestOidcCoverageGate:
    _ISSUER_KWARGS = {
        "mode": "oidc",
        "oidc": {
            "issuers": [{"issuer": "https://kc.example/realms/volundr", "audiences": ["api"]}]
        },
    }

    _CLEAR_UNCOVERED = {"enabled": {"bifrost": False}}

    def test_oidc_allowed_with_mimir_plugin_enabled_by_default(self) -> None:
        """Mímir's own auth checks now go through auth_mode (see mimir.config.

        MimirServiceConfig.auth_mode/identity_adapter and
        niuu.service_runtime._validate_identity_adapter_class), so it no
        longer needs plugins.enabled.mimir: false to run oidc — only bifrost
        (its client-credential gap — see the 'bifrost' entry in
        _OIDC_UNCOVERED_PLUGINS) still needs disabling.
        """
        settings = CLISettings(host_auth=self._ISSUER_KWARGS, plugins=self._CLEAR_UNCOVERED)
        assert settings.host_auth.mode == "oidc"

    def test_oidc_allowed_with_guild_enabled(self) -> None:
        """Guild forwards only the caller's bearer token to a remote instance

        (see niuu.adapters.inbound.remote_urls.forward_identity_headers), so
        it does not need to be disabled for auth.mode: oidc.
        """
        settings = CLISettings(host_auth=self._ISSUER_KWARGS, plugins=self._CLEAR_UNCOVERED)
        assert settings.host_auth.mode == "oidc"
        assert settings.plugins.enabled.get("guild", True) is True

    def test_oidc_allowed_regardless_of_bifrost_auth_mode_once_bifrost_plugin_disabled(
        self,
    ) -> None:
        """Bifröst's auth_mode is now forced to oidc by the CLI host itself

        (cli.commands.platform._effective_bifrost_config) whenever
        host_auth.mode: oidc, so a stale bifrost.auth_mode in config.yaml no
        longer needs to be validated here — see that function's docstring.
        The bifrost *plugin* still needs disabling separately (see below):
        that's about whether anything can reach it without a credential,
        not about what auth_mode it's configured with.
        """
        settings = CLISettings(
            host_auth=self._ISSUER_KWARGS,
            plugins=self._CLEAR_UNCOVERED,
            bifrost={"auth_mode": "open"},
        )
        assert settings.host_auth.mode == "oidc"
        assert settings.bifrost.auth_mode == "open"  # untouched here; platform.py overrides it

    def test_oidc_blocked_while_bifrost_plugin_enabled_by_default(self) -> None:
        """Bifröst's inbound auth is verified now, but sessions/residents have

        no way to authenticate to it yet (skuld.config.ModelGatewayConfig.
        token has no real default; ravn.adapters.llm.bifrost.BifrostAdapter
        sends no Authorization header) — every model call would 401. See the
        'bifrost' entry in _OIDC_UNCOVERED_PLUGINS for the full reasoning.
        """
        with pytest.raises(ValueError, match="bifrost"):
            CLISettings(host_auth=self._ISSUER_KWARGS)

    def test_none_mode_ignores_uncovered_plugins(self) -> None:
        settings = CLISettings()
        assert settings.host_auth.mode == "none"


class TestHostsConfig:
    """The registered-hosts pool: on in mini mode by default, rendered as ``compute:``."""

    COMPUTE = {
        "pool_id": "vms",
        "max_machines": 1,
        "provider": {"adapter": "private.Provider", "kwargs": {}},
        "runtime": {"adapter": "private.Runtime", "kwargs": {}},
    }

    def test_unset_means_on_in_mini_mode_only(self) -> None:
        hosts = HostsConfig()

        assert hosts.enabled is None
        assert hosts.active("mini", compute_configured=False) is True
        assert hosts.active("mini", compute_configured=True) is False
        for mode in ("docker", "cluster", "openshell"):
            assert hosts.active(mode, compute_configured=False) is False

    @pytest.mark.parametrize("mode", ["mini", "docker"])
    @pytest.mark.parametrize("compute_configured", [True, False])
    def test_an_explicit_choice_wins(self, mode, compute_configured) -> None:
        assert HostsConfig(enabled=True).active(mode, compute_configured) is True
        assert HostsConfig(enabled=False).active(mode, compute_configured) is False

    def test_settings_report_whether_the_pool_is_served(self) -> None:
        assert CLISettings(mode="mini").hosts_active is True
        assert CLISettings(mode="mini", compute=self.COMPUTE).hosts_active is False
        assert CLISettings(mode="mini", hosts={"enabled": False}).hosts_active is False
        assert CLISettings(mode="cluster").hosts_active is False

    def test_a_defined_pool_and_an_explicit_hosts_pool_conflict(self) -> None:
        with pytest.raises(ValueError, match="both define this Forge's compute pool"):
            CLISettings(mode="mini", hosts={"enabled": True}, compute=self.COMPUTE)

    def test_an_explicit_hosts_pool_is_for_mini_mode(self) -> None:
        with pytest.raises(ValueError, match="hosts.enabled is for mini mode"):
            CLISettings(mode="docker", hosts={"enabled": True})

    def test_the_built_in_execution_is_rendered_when_no_catalog_is_named(self, tmp_path) -> None:
        hosts = HostsConfig(
            pool_id="pool", profile="byo", data_dir=str(tmp_path / "hosts"), max_hosts=3
        )

        compute = hosts.compute(platform_port=8081, join_host="10.0.0.5")

        assert compute.pool_id == compute.provider_binding == "pool"
        assert compute.execution_catalog.adapter.endswith("BuiltinHostCatalog")
        assert compute.execution_catalog.kwargs == {
            "pool_id": "pool",
            "provider_binding": "pool",
            "profile": "byo",
            "data_dir": str(tmp_path / "hosts"),
            "platform_port": 8081,
        }
        assert compute.session_images.linux == "ghcr.io/niuulabs/openshell:dev"
        assert compute.session_images.darwin == "ghcr.io/niuulabs/skuld-darwin:dev"

    def test_the_data_dir_is_expanded(self) -> None:
        compute = HostsConfig(data_dir="~/hosts-data").compute(platform_port=8080)

        assert "~" not in compute.execution_catalog.kwargs["data_dir"]
        assert compute.execution_catalog.kwargs["data_dir"].endswith("hosts-data")

    def test_the_release_images_can_be_replaced(self) -> None:
        hosts = HostsConfig(
            session_images={
                "linux": "ghcr.io/niuulabs/openshell:1.2.3",
                "darwin": "ghcr.io/niuulabs/skuld-darwin:1.2.3",
            }
        )

        images = hosts.compute(platform_port=8080).session_images

        assert (images.linux, images.darwin) == (
            "ghcr.io/niuulabs/openshell:1.2.3",
            "ghcr.io/niuulabs/skuld-darwin:1.2.3",
        )

    def test_the_pinned_join_listener_is_named_by_the_host_others_reach_us_at(self) -> None:
        hosts = HostsConfig().compute(platform_port=8080, join_host="forge.lan").hosts

        assert hosts.join_url == "https://forge.lan:8443"
        assert hosts.join_listener is not None

    def test_a_join_url_wins_over_the_listener(self) -> None:
        hosts = (
            HostsConfig(join_url="https://forge.example.test")
            .compute(platform_port=8080, join_host="forge.lan")
            .hosts
        )

        assert hosts.join_url == "https://forge.example.test"
        assert hosts.join_listener is None

    def test_without_a_join_host_or_url_none_is_rendered(self) -> None:
        assert HostsConfig().compute(platform_port=8080).hosts.join_url == ""

    def test_the_session_images_are_never_empty(self) -> None:
        defaults = HostSessionImagesConfig()
        assert defaults.linux.endswith(":dev") and defaults.darwin.endswith(":dev")
        with pytest.raises(ValueError, match="at least 1 character"):
            HostSessionImagesConfig(linux="")
        with pytest.raises(ValueError, match="windows"):
            HostSessionImagesConfig(windows="x")
