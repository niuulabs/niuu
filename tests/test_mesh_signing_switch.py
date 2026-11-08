"""One switch for mesh signing: mini-mode config to Forge's signers and local sessions."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from cli.commands.platform import _resolve_local_pod_manager_env
from cli.config import CLISettings
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.mesh_settings import (
    add_mesh_env_to_spec,
)
from volundr.adapters.outbound.mesh_settings import (
    mesh_environment as _mesh_env,
)
from volundr.composition_builders import _build_pod_manager
from volundr.config import Settings

LOCAL = "volundr.adapters.outbound.local_process.LocalProcessPodManager"
FLUX = "volundr.adapters.outbound.flux.FluxPodManager"


def _local_kwargs(tmp_path, **extra):
    return {
        "workspaces_dir": str(tmp_path / "ws"),
        "state_file": str(tmp_path / "state.json"),
        **extra,
    }


def _local_session_mesh(env: dict[str, str]) -> dict | None:
    """The `mesh` kwarg the local pod manager receives, wherever the config nests it."""
    if "POD_MANAGER__KWARGS__MESH" in env:
        return json.loads(env["POD_MANAGER__KWARGS__MESH"])
    config = json.loads(env["POD_MANAGER"])
    local = config["kwargs"]["local"] if "local" in config["kwargs"] else config
    return local["kwargs"].get("mesh")


class TestMiniModeConfig:
    def test_default_asks_nothing_of_forge_or_the_sessions(self) -> None:
        env = _resolve_local_pod_manager_env(CLISettings(mode="mini"))
        assert not [key for key in env if key.startswith("MESH__")]
        assert _local_session_mesh(env) is None

    def test_the_switch_reaches_forge_and_the_session_settings_travel_as_json(self) -> None:
        settings = CLISettings(
            mode="mini",
            mesh={"enabled": True, "realm_id": "home", "attestation": {"required": False}},
        )
        env = _resolve_local_pod_manager_env(settings)
        assert env["MESH__ATTESTATION__REQUIRED"] == "false"
        assert _local_session_mesh(env) == {"enabled": True, "realm_id": "home"}

    def test_nng_discovery_and_nats_settings_travel_to_the_sessions(self) -> None:
        mesh = {
            "enabled": True,
            "nng": {"pub_sub_address": "tcp://127.0.0.1:7480"},
            "discovery_adapters": [{"adapter": "static", "peers": []}],
            "nats": {"servers": ["nats://127.0.0.1:4222"]},
        }
        env = _resolve_local_pod_manager_env(CLISettings(mode="mini", mesh=mesh))
        assert _local_session_mesh(env) == mesh

    def test_only_what_the_operator_set_is_sent(self) -> None:
        env = _resolve_local_pod_manager_env(
            CLISettings(mode="mini", mesh={"attestation": {"clock_skew_s": 60}})
        )
        assert env["MESH__ATTESTATION__CLOCK_SKEW_S"] == "60.0"
        assert "MESH__ATTESTATION__REQUIRED" not in env

    def test_an_unknown_key_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CLISettings(mode="mini", mesh={"attestaton": {"required": False}})

    def test_the_switch_reaches_every_launched_session(self, tmp_path, monkeypatch) -> None:
        settings = CLISettings(
            mode="mini", mesh={"enabled": True, "attestation": {"required": False}}
        )
        for key, value in _resolve_local_pod_manager_env(settings).items():
            monkeypatch.setenv(key, value)
        forge = Settings()
        assert forge.mesh.attestation.required is False

        kwargs = _local_kwargs(tmp_path)
        kwargs["mesh"] = _local_session_mesh(_resolve_local_pod_manager_env(settings))
        manager = _build_pod_manager(forge, LOCAL, kwargs)

        env = _mesh_env(manager._mesh)  # noqa: SLF001
        assert env.pop("SKULD__MESH__DISCOVERY_ADAPTERS")  # the default for this shape
        assert env == {
            "SKULD__MESH__ENABLED": "true",
            "SKULD__MESH__ATTESTATION__REQUIRED": "false",
        }


class TestOneDecision:
    def _settings(self, required: bool) -> Settings:
        settings = Settings()
        settings.mesh.attestation.required = required
        return settings

    def test_signing_is_the_default_and_adds_nothing_to_sessions(self, tmp_path) -> None:
        manager = _build_pod_manager(self._settings(True), LOCAL, _local_kwargs(tmp_path))
        assert _mesh_env(manager._mesh) == {}  # noqa: SLF001

    def test_off_is_written_into_the_local_session_settings(self, tmp_path) -> None:
        manager = _build_pod_manager(
            self._settings(False), LOCAL, _local_kwargs(tmp_path, mesh='{"realm_id": "r"}')
        )
        assert _mesh_env(manager._mesh) == {  # noqa: SLF001
            "SKULD__MESH__REALM_ID": "r",
            "SKULD__MESH__ATTESTATION__REQUIRED": "false",
        }

    def test_a_local_setting_that_disagrees_is_refused_at_startup(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="one decision"):
            _build_pod_manager(
                self._settings(False),
                LOCAL,
                _local_kwargs(tmp_path, mesh={"attestation": {"required": True}}),
            )
        with pytest.raises(ValueError, match="one decision"):
            _build_pod_manager(
                self._settings(True),
                LOCAL,
                _local_kwargs(tmp_path, mesh={"attestation": {"required": False}}),
            )

    def test_flux_sessions_follow_through_their_defaults(self) -> None:
        manager = _build_pod_manager(
            self._settings(False), FLUX, {"session_defaults": {"mesh": {"enabled": True}}}
        )
        assert manager._session_defaults["mesh"] == {  # noqa: SLF001
            "enabled": True,
            "attestation": {"required": False},
        }
        with pytest.raises(ValueError, match="session_defaults.mesh"):
            _build_pod_manager(
                self._settings(False),
                FLUX,
                {"session_defaults": {"mesh": {"attestation": {"required": True}}}},
            )

    def test_flux_defaults_are_untouched_while_signing_is_on(self) -> None:
        manager = _build_pod_manager(self._settings(True), FLUX, {"session_defaults": {"a": 1}})
        assert manager._session_defaults == {"a": 1}  # noqa: SLF001

    @pytest.mark.parametrize(
        "path",
        [
            "volundr.adapters.outbound.docker_container.DockerContainerPodManager",
            "volundr.adapters.outbound.direct_k8s_pod_manager.DirectK8sPodManager",
            "volundr.adapters.outbound.openshell_gateway.OpenShellGatewayPodManager",
            "volundr.adapters.outbound.vm_pod_manager.VmPodManager",
            LOCAL,
        ],
    )
    def test_every_pod_manager_that_starts_skuld_carries_the_decision(self, path) -> None:
        from niuu.utils import import_class
        from volundr.composition_builders import _apply_mesh_decision

        if "openshell" in path:
            pytest.importorskip("openshell")
        cls = import_class(path)
        kwargs: dict = {"mesh": {"realm_id": "r"}}
        _apply_mesh_decision(cls, kwargs, self._settings(False), covered=False)
        assert kwargs["mesh"] == {"realm_id": "r", "attestation": {"required": False}}

        untouched: dict = {}
        _apply_mesh_decision(cls, untouched, self._settings(True), covered=False)
        assert untouched == {}

        with pytest.raises(ValueError, match="one decision"):
            _apply_mesh_decision(
                cls,
                {"mesh": {"attestation": {"required": True}}},
                self._settings(False),
                covered=False,
            )

    def test_a_pod_manager_that_cannot_carry_it_is_refused_while_signing_is_off(self) -> None:
        from volundr.composition_builders import _apply_mesh_decision

        class Elsewhere:
            def __init__(self, **_extra) -> None:
                pass

        with pytest.raises(ValueError, match="Elsewhere has no setting"):
            _apply_mesh_decision(Elsewhere, {}, self._settings(False), covered=False)
        _apply_mesh_decision(Elsewhere, {}, self._settings(True), covered=False)
        _apply_mesh_decision(Elsewhere, {}, self._settings(False), covered=True)

    def test_the_spec_helper_adds_nothing_without_settings(self) -> None:
        from volundr.domain.models import SessionSpec

        spec = SessionSpec(values={}, pod_spec=None)
        add_mesh_env_to_spec(spec, {})
        assert spec.values == {}
        add_mesh_env_to_spec(spec, {"attestation": {"required": False}})
        assert spec.values["env"] == {"SKULD__MESH__ATTESTATION__REQUIRED": "false"}

    def test_a_mapping_that_is_not_one_is_refused(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="pod_manager.kwargs.mesh"):
            _build_pod_manager(self._settings(True), LOCAL, _local_kwargs(tmp_path, mesh="[1]"))
        with pytest.raises(ValueError, match="pod_manager.kwargs.mesh"):
            _build_pod_manager(self._settings(True), LOCAL, _local_kwargs(tmp_path, mesh="{nope"))


class TestVisible:
    def test_forge_health_reports_the_off_state(self) -> None:
        from fastapi.testclient import TestClient

        from volundr.main import create_app

        app = create_app()
        client = TestClient(app)
        assert "mesh_attestation" not in client.get("/health").json()
        app.state.mesh_signing = False
        assert client.get("/health").json()["mesh_attestation"] == {
            "required": False,
            "state": "unauthenticated",
        }
        app.state.mesh_signing = True
        assert client.get("/health").json()["mesh_attestation"]["state"] == "signed"

    def test_local_process_flattens_nested_settings(self) -> None:
        assert _mesh_env({"adapters": [{"a": 1}], "nats": {"servers": ["x"]}, "enabled": True}) == {
            "SKULD__MESH__ADAPTERS": '[{"a": 1}]',
            "SKULD__MESH__NATS__SERVERS": '["x"]',
            "SKULD__MESH__ENABLED": "true",
        }
        assert LocalProcessPodManager  # imported for the type under test

    def test_a_session_reports_unauthenticated_when_signing_is_off(self, tmp_path, monkeypatch):
        from skuld.broker import Broker
        from skuld.config import SkuldSettings

        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])

        def status(**mesh):
            settings = SkuldSettings(
                session={"id": "s1", "workspace_dir": str(tmp_path)}, mesh=mesh
            )
            return Broker(settings=settings).mesh_attestation_status()

        assert status(enabled=False) is None
        assert status(enabled=True) == {"required": True, "state": "signed"}
        assert status(enabled=True, attestation={"required": False}) == {
            "required": False,
            "state": "unauthenticated",
        }


class TestPlatformServicesFollowTheSwitch:
    def test_forge_and_ting_read_the_same_value_from_the_mini_mode_environment(
        self, monkeypatch
    ) -> None:
        from ting.config import Settings as TingSettings

        settings = CLISettings(mode="mini", mesh={"attestation": {"required": False}})
        for key, value in _resolve_local_pod_manager_env(settings).items():
            monkeypatch.setenv(key, value)

        assert Settings().mesh.attestation.required is False
        assert TingSettings().mesh.attestation.required is False

    def test_the_default_is_signing_in_both(self) -> None:
        from ting.config import Settings as TingSettings

        assert Settings().mesh.attestation.required is True
        assert TingSettings().mesh.attestation.required is True

    async def test_a_session_checks_platform_events_only_while_signing_is_on(
        self, tmp_path, monkeypatch
    ) -> None:
        from skuld.broker import Broker
        from skuld.config import SkuldSettings

        monkeypatch.setitem(SkuldSettings.model_config, "yaml_file", [])
        broker = Broker(
            settings=SkuldSettings(session={"id": "s1", "workspace_dir": str(tmp_path)})
        )
        assert broker._platform_event_verifier() is None  # noqa: SLF001

        broker._mesh_attested = type("A", (), {"verifier": object()})()  # noqa: SLF001
        verifier = broker._platform_event_verifier()  # noqa: SLF001
        assert verifier is not None
