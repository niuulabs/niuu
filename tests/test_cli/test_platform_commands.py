"""Tests for cli.commands.platform — dynamic service flags and platform lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer
from typer.testing import CliRunner

from cli.commands.platform import (
    _build_init_config,
    _build_preflight_config,
    _build_up_callback,
    _collect_service_definitions,
    _prompt_mode_selection,
    _resolve_enabled_services,
    _resolve_local_pod_manager_env,
    _route_inventory_payload,
    create_platform_commands,
)
from cli.config import CLISettings, PerServiceConfig, PodManagerConfig
from cli.registry import PluginRegistry
from cli.server import MountedRouteDomain
from cli.services.manager import ServiceManager
from niuu.ports.plugin import ServiceDefinition
from tests.test_cli.conftest import FakePlugin, StubService

runner = CliRunner()


def _make_svc_def(
    name: str,
    default_enabled: bool = True,
    depends_on: list[str] | None = None,
    port: int = 8080,
) -> ServiceDefinition:
    return ServiceDefinition(
        name=name,
        description=f"{name} service",
        factory=StubService,
        default_enabled=default_enabled,
        depends_on=depends_on or [],
        default_port=port,
    )


class _ServicePlugin(FakePlugin):
    """FakePlugin that also implements register_service().

    Passes ``depends_on`` from the ServiceDefinition to FakePlugin so that
    ``depends_on()`` (which FakePlugin overrides) stays in sync.
    """

    def __init__(self, svc_def: ServiceDefinition, **kwargs: object) -> None:
        super().__init__(
            name=svc_def.name,
            service=StubService(),
            deps=list(svc_def.depends_on),
            **kwargs,
        )
        self._svc_def = svc_def

    def register_service(self) -> ServiceDefinition:
        return self._svc_def


class TestCollectServiceDefinitions:
    def test_collects_from_plugins_with_register_service(self) -> None:
        registry = PluginRegistry()
        registry.register(_ServicePlugin(_make_svc_def("volundr")))
        registry.register(_ServicePlugin(_make_svc_def("ting")))
        defs = _collect_service_definitions(registry)
        assert "volundr" in defs
        assert "ting" in defs

    def test_skips_plugins_without_register_service(self) -> None:
        registry = PluginRegistry()
        registry.register(FakePlugin(name="query-only"))  # no register_service
        registry.register(_ServicePlugin(_make_svc_def("volundr")))
        defs = _collect_service_definitions(registry)
        assert "volundr" in defs
        assert "query-only" not in defs

    def test_includes_disabled_plugins(self) -> None:
        """Disabled plugins still contribute ServiceDefinitions (for flags)."""
        registry = PluginRegistry()
        registry.register(_ServicePlugin(_make_svc_def("skuld", default_enabled=False)))
        registry.disable("skuld")
        defs = _collect_service_definitions(registry)
        assert "skuld" in defs


class TestResolveEnabledServices:
    def test_default_enabled_only(self) -> None:
        service_defs = {
            "volundr": _make_svc_def("volundr", default_enabled=True),
            "ting": _make_svc_def("ting", default_enabled=True),
            "skuld": _make_svc_def("skuld", default_enabled=False),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {})
        assert "volundr" in enabled
        assert "ting" in enabled
        assert "skuld" not in enabled

    def test_cli_flag_adds_disabled_service(self) -> None:
        service_defs = {
            "skuld": _make_svc_def("skuld", default_enabled=False),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {"skuld": True})
        assert "skuld" in enabled

    def test_cli_flag_removes_enabled_service(self) -> None:
        service_defs = {
            "ting": _make_svc_def("ting", default_enabled=True),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), False, {"ting": False})
        assert "ting" not in enabled

    def test_start_all_enables_everything(self) -> None:
        service_defs = {
            "volundr": _make_svc_def("volundr", default_enabled=True),
            "skuld": _make_svc_def("skuld", default_enabled=False),
        }
        enabled = _resolve_enabled_services(service_defs, CLISettings(), True, {})
        assert enabled == {"volundr", "skuld"}

    def test_config_override_enables(self) -> None:
        service_defs = {"skuld": _make_svc_def("skuld", default_enabled=False)}
        settings = CLISettings(service_overrides={"skuld": PerServiceConfig(enabled=True)})
        enabled = _resolve_enabled_services(service_defs, settings, False, {})
        assert "skuld" in enabled

    def test_config_override_disables(self) -> None:
        service_defs = {"ting": _make_svc_def("ting", default_enabled=True)}
        settings = CLISettings(service_overrides={"ting": PerServiceConfig(enabled=False)})
        enabled = _resolve_enabled_services(service_defs, settings, False, {})
        assert "ting" not in enabled

    def test_cli_flag_wins_over_config_override(self) -> None:
        """CLI flag has the highest priority."""
        service_defs = {"ting": _make_svc_def("ting", default_enabled=False)}
        settings = CLISettings(service_overrides={"ting": PerServiceConfig(enabled=False)})
        enabled = _resolve_enabled_services(service_defs, settings, False, {"ting": True})
        assert "ting" in enabled

    def test_start_all_overrides_no_flags(self) -> None:
        """--all takes highest precedence even when --no-service is set."""
        service_defs = {"ting": _make_svc_def("ting", default_enabled=True)}
        enabled = _resolve_enabled_services(service_defs, CLISettings(), True, {"ting": False})
        assert "ting" in enabled


class TestDynamicUpCallback:
    def test_up_callback_has_service_flags(self) -> None:
        service_defs = {
            "volundr": _make_svc_def("volundr"),
            "ting": _make_svc_def("ting"),
        }
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        import inspect

        sig = inspect.signature(up_fn)
        assert "volundr" in sig.parameters
        assert "ting" in sig.parameters
        assert "skip_preflight" in sig.parameters
        assert "all" in sig.parameters
        assert "host_profile" in sig.parameters
        assert "mounts" in sig.parameters
        assert "workspaces_dir" in sig.parameters

    def test_up_flag_defaults_are_none_for_services(self) -> None:
        """An unspecified service flag stays None so config/defaults decide.

        The parameter default is a ``typer.Option`` carrying the flag's help
        text, so the value Typer resolves lives one level in.
        """
        service_defs = {"skuld": _make_svc_def("skuld", default_enabled=False)}
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        import inspect

        sig = inspect.signature(up_fn)
        assert sig.parameters["skuld"].default.default is None

    def test_up_flags_carry_help_text(self) -> None:
        """Every generated flag is documented, including per-service ones."""
        service_defs = {"skuld": _make_svc_def("skuld", default_enabled=False)}
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        import inspect

        sig = inspect.signature(up_fn)
        for name in ("skip_preflight", "all", "host_profile", "mounts", "skuld"):
            assert sig.parameters[name].default.help, f"{name} has no help text"
        # The service flag borrows its wording from the ServiceDefinition.
        assert "skuld" in sig.parameters["skuld"].default.help

    def test_new_plugin_adds_flag_automatically(self) -> None:
        """Adding a new service definition adds its flag with no code change."""
        service_defs = {
            "odin": _make_svc_def("odin", default_enabled=False),
        }
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        import inspect

        sig = inspect.signature(up_fn)
        assert "odin" in sig.parameters

    def test_up_callback_forwards_host_profile_and_mounts(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        startup = AsyncMock()
        shutdown = AsyncMock()

        with (
            patch("cli.commands.platform._startup", startup),
            patch("cli.commands.platform._shutdown", shutdown),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch("cli.commands.platform.asyncio.run", side_effect=asyncio.run),
            patch.dict("os.environ", {}, clear=True),
        ):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=True,
                host_profile="api",
                mounts="niuu-api,runtime-config",
                volundr=True,
            )

        startup.assert_awaited_once_with(
            manager,
            settings,
            {"volundr"},
            True,
            "api",
            {"niuu-api", "runtime-config"},
            workspaces_dir_override="",
        )
        shutdown.assert_awaited_once_with(manager)

    def test_up_callback_sets_mini_mode_runtime_overrides(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings(mode="mini")
        up_fn = _build_up_callback(service_defs, manager, settings)

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        captured_environment: dict[str, str] = {}

        async def capture_startup(*_args, **_kwargs) -> None:
            captured_environment.update(os.environ)

        startup = AsyncMock(side_effect=capture_startup)
        shutdown = AsyncMock()

        with (
            patch("cli.commands.platform._startup", startup),
            patch("cli.commands.platform._shutdown", shutdown),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch("cli.commands.platform.asyncio.run", side_effect=asyncio.run),
            patch.dict("os.environ", {}, clear=True),
        ):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=False,
                host_profile="full",
                mounts="",
                volundr=True,
            )

            assert captured_environment["LOCAL_MOUNTS__ENABLED"] == "true"
            assert captured_environment["LOCAL_MOUNTS__MINI_MODE"] == "true"
            assert captured_environment["POD_MANAGER__ADAPTER"] == settings.pod_manager.adapter
            assert (
                captured_environment["POD_MANAGER__KWARGS__WORKSPACES_DIR"] == "~/.niuu/workspaces"
            )
            assert captured_environment["STORAGE__KWARGS__WORKSPACE_MOUNT_PATH"] == str(
                Path("~/.niuu/workspaces").expanduser()
            )
            assert captured_environment["STORAGE__KWARGS__HOME_MOUNT_PATH"] == str(
                Path("~/.niuu/home").expanduser()
            )
            assert captured_environment["POD_MANAGER__KWARGS__CLAUDE_BINARY"] == "claude"
            assert captured_environment["POD_MANAGER__KWARGS__MAX_CONCURRENT"] == "4"
            assert captured_environment["POD_MANAGER__KWARGS__SDK_PORT_START"] == "9100"
            assert captured_environment["GIT__VALIDATE_ON_CREATE"] == "false"
            assert "LOCAL_MOUNTS__ENABLED" not in os.environ

        startup.assert_awaited_once()
        shutdown.assert_awaited_once_with(manager)

    def test_up_callback_does_not_set_local_overrides_for_openshell_gateway(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings(
            mode="openshell",
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager",
                gateway_endpoint="openshell.openshell.svc.cluster.local:8080",
                sandbox_image="skuld:test",
                service_port=9200,
            ),
        )
        up_fn = _build_up_callback(service_defs, manager, settings)

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        startup = AsyncMock()
        shutdown = AsyncMock()

        with (
            patch("cli.commands.platform._startup", startup),
            patch("cli.commands.platform._shutdown", shutdown),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch("cli.commands.platform.asyncio.run", side_effect=asyncio.run),
            patch.dict("os.environ", {}, clear=True),
        ):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=False,
                host_profile="full",
                mounts="",
                volundr=True,
            )

            assert "LOCAL_MOUNTS__ENABLED" not in os.environ
            assert "POD_MANAGER__ADAPTER" not in os.environ

        startup.assert_awaited_once()
        shutdown.assert_awaited_once_with(manager)

    def test_up_callback_applies_workspaces_dir_override_in_mini_mode(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings(mode="mini")
        up_fn = _build_up_callback(service_defs, manager, settings)

        fake_event = MagicMock()
        fake_event.wait = AsyncMock(side_effect=asyncio.CancelledError())
        captured_environment: dict[str, str] = {}

        async def capture_startup(*_args, **_kwargs) -> None:
            captured_environment.update(os.environ)

        startup = AsyncMock(side_effect=capture_startup)
        shutdown = AsyncMock()

        with (
            patch("cli.commands.platform._startup", startup),
            patch("cli.commands.platform._shutdown", shutdown),
            patch("cli.commands.platform.asyncio.Event", return_value=fake_event),
            patch("cli.commands.platform.asyncio.run", side_effect=asyncio.run),
            patch.dict("os.environ", {}, clear=True),
        ):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=False,
                host_profile="full",
                mounts="",
                workspaces_dir="/tmp/mini-workspaces",
                volundr=True,
            )

            assert (
                captured_environment["POD_MANAGER__KWARGS__WORKSPACES_DIR"]
                == "/tmp/mini-workspaces"
            )
            assert (
                captured_environment["STORAGE__KWARGS__WORKSPACE_MOUNT_PATH"]
                == "/tmp/mini-workspaces"
            )
            assert captured_environment["STORAGE__KWARGS__HOME_MOUNT_PATH"] == "/tmp/home"
            assert "POD_MANAGER__KWARGS__WORKSPACES_DIR" not in os.environ

        assert startup.await_count == 1
        called_settings = startup.await_args.args[1]
        assert called_settings.pod_manager.workspaces_dir == "/tmp/mini-workspaces"
        shutdown.assert_awaited_once_with(manager)

    def test_up_callback_rejects_workspaces_dir_override_outside_mini_mode(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings(mode="cluster")
        up_fn = _build_up_callback(service_defs, manager, settings)

        with pytest.raises(typer.BadParameter, match="only supported in mini mode"):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=False,
                host_profile="full",
                mounts="",
                workspaces_dir="/tmp/mini-workspaces",
                volundr=True,
            )

    def test_up_callback_rejects_unknown_mounts(self) -> None:
        service_defs = {"volundr": _make_svc_def("volundr")}
        manager = MagicMock()
        settings = CLISettings()
        up_fn = _build_up_callback(service_defs, manager, settings)

        with pytest.raises(Exception, match="Unknown route domains"):
            up_fn(
                skip_preflight=True,
                all=False,
                no_web=False,
                host_profile="full",
                mounts="unknown-domain",
                volundr=True,
            )


class TestCreatePlatformCommands:
    def _make_platform(self, plugins: list | None = None) -> tuple:
        registry = PluginRegistry()
        for p in plugins or []:
            registry.register(p)
        settings = CLISettings()
        manager = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=0.5,
            health_check_max_retries=1,
        )
        platform = create_platform_commands(registry, settings, manager)
        return platform, registry, settings, manager

    def test_platform_has_up_down_status_init(self) -> None:
        platform, *_ = self._make_platform()
        names = [c.name or c.callback.__name__ for c in platform.registered_commands]
        assert "up" in names
        assert "down" in names
        assert "status" in names
        assert "init" in names

    def test_platform_down_command(self) -> None:
        platform, *_ = self._make_platform()
        result = runner.invoke(platform, ["down"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    def test_platform_init_command(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        platform, *_ = self._make_platform()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch("cli.commands.platform.typer.prompt", return_value="1"),
                patch("cli.commands.platform.Path.home", return_value=Path(tmp_dir)),
            ):
                result = runner.invoke(platform, ["init"])
        assert result.exit_code == 0
        assert "setup complete" in result.output.lower()

    def test_platform_status_no_services(self) -> None:
        platform, *_ = self._make_platform()
        result = runner.invoke(platform, ["status"])
        assert result.exit_code == 0

    def test_platform_status_with_service_defs(self) -> None:
        plugin = _ServicePlugin(_make_svc_def("volundr"))
        platform, *_ = self._make_platform(plugins=[plugin])
        result = runner.invoke(platform, ["status"])
        assert result.exit_code == 0
        assert "volundr" in result.output

    def test_platform_up_with_service_flags_in_help(self) -> None:
        plugin = _ServicePlugin(_make_svc_def("volundr"))
        platform, *_ = self._make_platform(plugins=[plugin])
        result = runner.invoke(platform, ["up", "--help"])
        assert result.exit_code == 0
        assert "volundr" in result.output

    def test_platform_up_all_flag_in_help(self) -> None:
        import re

        platform, *_ = self._make_platform()
        result = runner.invoke(platform, ["up", "--help"])
        assert result.exit_code == 0
        # Strip ANSI codes before checking — Rich/Typer may inject escape sequences
        plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output)
        # Must be --all, not --start-all
        assert "--all" in plain
        assert "--start-all" not in plain
        assert "--host-profile" in plain
        assert "--mounts" in plain
        assert "--workspaces-dir" in plain


class TestDependencyResolutionViaEnabledServices:
    async def test_ting_dep_pulls_in_volundr(self) -> None:
        registry = PluginRegistry()
        registry.register(_ServicePlugin(_make_svc_def("volundr")))
        registry.register(_ServicePlugin(_make_svc_def("ting", depends_on=["volundr"])))
        manager = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=0.5,
            health_check_max_retries=1,
        )
        order = manager.resolve_start_order(enabled_services={"ting"})
        assert "volundr" in order
        assert "ting" in order
        assert order.index("volundr") < order.index("ting")


class TestBuildPreflightConfig:
    def test_mini_mode_defaults(self) -> None:
        settings = CLISettings()
        config = _build_preflight_config(settings)
        assert config.mode == "mini"
        assert config.claude_binary == "claude"
        assert config.workspaces_dir == "~/.niuu/workspaces"

    def test_mini_mode_uses_workspaces_dir_override(self) -> None:
        settings = CLISettings()
        config = _build_preflight_config(
            settings,
            workspaces_dir_override="/tmp/mini-workspaces",
        )
        assert config.workspaces_dir == "/tmp/mini-workspaces"

    def test_cluster_mode_from_settings(self) -> None:
        settings = CLISettings(
            mode="cluster",
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
                namespace="test-ns",
                kubeconfig="/tmp/kubeconfig",
            ),
        )
        config = _build_preflight_config(settings)
        assert config.mode == "cluster"
        assert config.namespace == "test-ns"
        assert config.kubeconfig == "/tmp/kubeconfig"

    def test_cluster_mode_extra_fields_forwarded(self) -> None:
        """Extra fields on PodManagerConfig are available via adapter_kwargs."""
        settings = CLISettings(
            mode="cluster",
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
                namespace="custom",
                kubeconfig="~/.kube/custom",
            ),
        )
        config = _build_preflight_config(settings)
        assert config.namespace == "custom"
        assert config.kubeconfig == "~/.kube/custom"


class TestBuildInitConfig:
    def test_mini_config(self) -> None:
        config = _build_init_config("mini")
        assert config["mode"] == "mini"
        assert "local_process" in config["pod_manager"]["adapter"]

    def test_cluster_config(self) -> None:
        config = _build_init_config("cluster")
        assert config["mode"] == "cluster"
        assert "direct_k8s" in config["pod_manager"]["adapter"]
        assert config["pod_manager"]["namespace"] == "volundr"

    def test_openshell_config(self) -> None:
        config = _build_init_config("openshell")
        assert config["mode"] == "openshell"
        assert "openshell_gateway.OpenShellGatewayPodManager" in config["pod_manager"]["adapter"]
        assert (
            config["pod_manager"]["gateway_endpoint"]
            == "openshell.openshell.svc.cluster.local:8080"
        )
        assert config["pod_manager"]["service_port"] == 9200

    def test_cluster_config_uses_pinned_image_version(self) -> None:
        """skuld_image must use a pinned version, not :latest."""
        config = _build_init_config("cluster")
        skuld_image = config["pod_manager"]["skuld_image"]
        assert ":latest" not in skuld_image
        assert ":" in skuld_image  # has a version tag

    def test_mini_runtime_exposes_local_resident_profiles(self) -> None:
        settings = CLISettings(
            mode="mini",
            bifrost={
                "providers": {
                    "local-vllm": {
                        "base_url": "https://vllm.example.test",
                        "models": ["nvidia/nemotron-test"],
                    }
                }
            },
        )

        env = _resolve_local_pod_manager_env(settings)
        resident_config = json.loads(env["RESIDENT_RUNTIMES"])

        assert resident_config["controllers"][0]["adapter"].endswith(
            "LocalContainerResidentRuntimeController"
        )
        assert {profile["id"] for profile in resident_config["profiles"]} == {
            "ravn-local",
            "nemoclaw-local",
            "nemohermes-local",
        }
        assert {profile["backend"] for profile in resident_config["profiles"]} == {"local"}
        assert resident_config["profiles"][0]["default_model"] == "gpt-5.6-sol"
        assert resident_config["profiles"][0]["allowed_models"] == ["gpt-5.6-sol"]
        assert resident_config["profiles"][0]["deployment"]["values"]["session"] == {
            "reasoningEffort": "high"
        }
        assert json.loads(env["BIFROST_CONFIG"])["providers"]["local-vllm"]["models"] == [
            "nvidia/nemotron-test"
        ]
        assert resident_config["profiles"][1]["default_model"] == ("niuu/nvidia/nemotron-test")
        assert "FileCredentialStore" in json.loads(env["CREDENTIAL_STORE"])["adapter"]


class TestRouteInventoryPayload:
    def test_serializes_inventory_records(self) -> None:
        payload = _route_inventory_payload(
            (
                MountedRouteDomain(
                    name="niuu-api",
                    prefixes=("/api/v1/niuu",),
                    source="plugin",
                    plugin_name="niuu",
                ),
            )
        )
        assert payload == [
            {
                "name": "niuu-api",
                "prefixes": ["/api/v1/niuu"],
                "source": "plugin",
                "plugin": "niuu",
            }
        ]


class TestInitOverwriteProtection:
    def test_aborts_when_config_exists_and_user_declines(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        platform, *_ = self._make_platform()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config_path = tmp / ".niuu" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("mode: mini\n")
            with (
                patch("cli.commands.platform.Path.home", return_value=tmp),
                patch("cli.commands.platform.typer.confirm", return_value=False),
            ):
                result = runner.invoke(platform, ["init"])
            assert result.exit_code == 0
            assert "preserved" in result.output.lower()
            # Original content must be untouched
            assert config_path.read_text() == "mode: mini\n"

    def test_overwrites_when_config_exists_and_user_confirms(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        platform, *_ = self._make_platform()
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            config_path = tmp / ".niuu" / "config.yaml"
            config_path.parent.mkdir(parents=True)
            config_path.write_text("mode: mini\n")
            with (
                patch("cli.commands.platform.Path.home", return_value=tmp),
                patch("cli.commands.platform.typer.confirm", return_value=True),
                patch("cli.commands.platform.typer.prompt", return_value="1"),
            ):
                result = runner.invoke(platform, ["init"])
            assert result.exit_code == 0
            assert "setup complete" in result.output.lower()
            # Config was rewritten
            assert config_path.read_text() != "mode: mini\n"

    def test_writes_without_prompt_when_no_existing_config(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        platform, *_ = self._make_platform()
        with tempfile.TemporaryDirectory() as tmp_dir:
            with (
                patch("cli.commands.platform.Path.home", return_value=Path(tmp_dir)),
                patch("cli.commands.platform.typer.prompt", return_value="1"),
            ):
                result = runner.invoke(platform, ["init"])
            assert result.exit_code == 0
            assert "setup complete" in result.output.lower()

    def _make_platform(self) -> tuple:
        registry = PluginRegistry()
        settings = CLISettings()
        manager = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=0.5,
            health_check_max_retries=1,
        )
        platform = create_platform_commands(registry, settings, manager)
        return platform, registry, settings, manager


class TestPromptModeSelection:
    def test_default_returns_mini(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="1"):
            mode = _prompt_mode_selection()
        assert mode == "mini"

    def test_choice_2_returns_openshell(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="2"):
            mode = _prompt_mode_selection()
        assert mode == "openshell"

    def test_choice_cluster_string_returns_cluster(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="cluster"):
            mode = _prompt_mode_selection()
        assert mode == "cluster"

    def test_choice_openshell_string_returns_openshell(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="openshell"):
            mode = _prompt_mode_selection()
        assert mode == "openshell"

    def test_choice_cluster_number_returns_cluster(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="3"):
            mode = _prompt_mode_selection()
        assert mode == "cluster"

    def test_garbage_returns_mini(self) -> None:
        from unittest.mock import patch

        with patch("cli.commands.platform.typer.prompt", return_value="xyz"):
            mode = _prompt_mode_selection()
        assert mode == "mini"


class TestPodManagerConfigAdapterKwargs:
    def test_mini_defaults(self) -> None:
        config = PodManagerConfig()
        kwargs = config.adapter_kwargs()
        assert "adapter" not in kwargs
        assert kwargs["workspaces_dir"] == "~/.niuu/workspaces"
        assert kwargs["claude_binary"] == "claude"

    def test_cluster_extra_fields(self) -> None:
        config = PodManagerConfig(
            adapter="volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
            namespace="volundr",
            kubeconfig="~/.kube/config",
            ingress_class="traefik",
        )
        kwargs = config.adapter_kwargs()
        assert "adapter" not in kwargs
        assert kwargs["namespace"] == "volundr"
        assert kwargs["kubeconfig"] == "~/.kube/config"
        assert kwargs["ingress_class"] == "traefik"


class TestConfigModeSwitching:
    def test_mini_mode_loads_local_process(self) -> None:
        """mode=mini uses LocalProcessPodManager adapter by default."""
        settings = CLISettings(mode="mini")
        assert "local_process" in settings.pod_manager.adapter

    def test_cluster_mode_adapter(self) -> None:
        """mode=cluster with explicit adapter loads DirectK8sPodManager."""
        settings = CLISettings(
            mode="cluster",
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
            ),
        )
        assert "DirectK8sPodManager" in settings.pod_manager.adapter
        assert settings.mode == "cluster"

    def test_openshell_mode_adapter(self) -> None:
        settings = CLISettings(
            mode="openshell",
            pod_manager=PodManagerConfig(
                adapter="volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager",
            ),
        )
        assert "OpenShellGatewayPodManager" in settings.pod_manager.adapter
        assert settings.mode == "openshell"


class TestPlatformStatusClusterInfo:
    def _make_platform(self, mode: str = "mini", plugins: list | None = None) -> tuple:
        registry = PluginRegistry()
        for p in plugins or []:
            registry.register(p)
        pod_manager_kwargs = {}
        if mode == "cluster":
            pod_manager_kwargs = {
                "adapter": "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
                "namespace": "test-ns",
                "kubeconfig": "/tmp/kube",
            }
        settings = CLISettings(
            mode=mode,
            pod_manager=(
                PodManagerConfig(**pod_manager_kwargs) if pod_manager_kwargs else PodManagerConfig()
            ),
        )
        manager = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=0.5,
            health_check_max_retries=1,
        )
        platform = create_platform_commands(registry, settings, manager)
        return platform, registry, settings, manager

    def test_status_shows_mode(self) -> None:
        platform, *_ = self._make_platform()
        result = runner.invoke(platform, ["status"])
        assert result.exit_code == 0
        assert "Mode: mini" in result.output

    def test_status_shows_cluster_info(self) -> None:
        platform, *_ = self._make_platform(mode="cluster")
        result = runner.invoke(platform, ["status"])
        assert result.exit_code == 0
        assert "Mode: cluster" in result.output
        assert "Namespace: test-ns" in result.output
        assert "Kubeconfig: /tmp/kube" in result.output

    def test_status_shows_pod_manager_name(self) -> None:
        platform, *_ = self._make_platform(mode="cluster")
        result = runner.invoke(platform, ["status"])
        assert "DirectK8sPodManager" in result.output

    def test_status_mini_no_cluster_info(self) -> None:
        platform, *_ = self._make_platform(mode="mini")
        result = runner.invoke(platform, ["status"])
        assert "Namespace:" not in result.output
        assert "Kubeconfig:" not in result.output


class TestPlatformInventoryCommand:
    def _make_platform(self, plugins: list | None = None) -> tuple:
        registry = PluginRegistry()
        for p in plugins or []:
            registry.register(p)
        settings = CLISettings()
        manager = ServiceManager(
            registry=registry,
            health_check_interval=0.01,
            health_check_timeout=0.5,
            health_check_max_retries=1,
        )
        platform = create_platform_commands(registry, settings, manager)
        return platform, registry, settings, manager

    def test_inventory_command_present(self) -> None:
        platform, *_ = self._make_platform()
        names = [c.name or c.callback.__name__ for c in platform.registered_commands]
        assert "inventory" in names

    def test_inventory_json_output(self) -> None:
        plugin = _ServicePlugin(_make_svc_def("niuu"))
        platform, registry, *_ = self._make_platform(plugins=[plugin])

        class _InventoryPlugin(_ServicePlugin):
            def api_route_domains(self):
                from niuu.ports.plugin import APIRouteDomain

                return (APIRouteDomain(name="niuu-api", prefixes=("/api/v1/niuu",)),)

        registry.register(_InventoryPlugin(_make_svc_def("niuu")))
        result = runner.invoke(
            platform,
            ["inventory", "--json", "--mounts", "niuu-api,runtime-config"],
        )
        assert result.exit_code == 0
        assert '"name": "niuu-api"' in result.output
        assert '"name": "runtime-config"' in result.output

    def test_inventory_writes_file(self, tmp_path: Path) -> None:
        class _InventoryPlugin(_ServicePlugin):
            def api_route_domains(self):
                from niuu.ports.plugin import APIRouteDomain

                return (APIRouteDomain(name="niuu-api", prefixes=("/api/v1/niuu",)),)

        platform, *_ = self._make_platform(plugins=[_InventoryPlugin(_make_svc_def("niuu"))])
        out_path = tmp_path / "route-inventory.json"
        result = runner.invoke(platform, ["inventory", "--out", str(out_path)])
        assert result.exit_code == 0
        assert out_path.exists()
        assert '"name": "niuu-api"' in out_path.read_text()
