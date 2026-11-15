"""``mesh.enabled: true`` is all an operator says: each shape's pod manager knows the rest."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.conftest import make_spec
from volundr.adapters.outbound.direct_k8s_pod_manager import (
    DirectK8sPodManager,
    MeshDiscoveryRbacError,
)
from volundr.adapters.outbound.local_process import LocalProcessPodManager
from volundr.adapters.outbound.mesh_settings import (
    mesh_environment,
    mesh_is_nng,
    mesh_with_defaults,
)
from volundr.domain.models import GitSource, PodSpecAdditions, Session

DEFAULTS = {"discovery_adapters": [{"adapter": "registry", "path": "/x"}], "nng": {"a": 1, "b": 2}}


class TestMeshWithDefaults:
    def test_a_disabled_mesh_gets_nothing(self) -> None:
        assert mesh_with_defaults({}, DEFAULTS) == {}
        assert mesh_with_defaults({"enabled": False}, DEFAULTS) == {"enabled": False}

    def test_another_transport_keeps_its_own_discovery(self) -> None:
        mesh = {"enabled": True, "transport": "nats"}
        assert mesh_with_defaults(mesh, DEFAULTS) == mesh

    def test_an_enabled_nng_mesh_is_filled_in(self) -> None:
        merged = mesh_with_defaults({"enabled": True}, DEFAULTS)
        assert merged["discovery_adapters"] == DEFAULTS["discovery_adapters"]
        assert merged["nng"] == {"a": 1, "b": 2}

    def test_what_the_operator_set_wins_key_by_key(self) -> None:
        merged = mesh_with_defaults({"enabled": True, "nng": {"a": 9}}, DEFAULTS)
        assert merged["nng"] == {"a": 9, "b": 2}

    def test_configured_discovery_is_never_added_to(self) -> None:
        for mine in ({"discovery_adapters": [{"adapter": "static"}]}, {"adapters": [{"x": 1}]}):
            merged = mesh_with_defaults({"enabled": True, **mine}, DEFAULTS)
            assert "registry" not in json.dumps(merged.get("discovery_adapters", []))

    def test_enabled_text_from_an_environment_variable_counts(self) -> None:
        assert mesh_is_nng({"enabled": "true"})
        assert not mesh_is_nng({"enabled": "false"})


class TestLocalProcess:
    def _manager(self, tmp_path, mesh) -> LocalProcessPodManager:
        return LocalProcessPodManager(workspaces_dir=str(tmp_path / "workspaces"), mesh=mesh)

    def test_sessions_on_the_machine_share_a_registry_file_beside_the_workspaces(
        self, tmp_path
    ) -> None:
        manager = self._manager(tmp_path, {"enabled": True})
        [entry] = manager._mesh["discovery_adapters"]
        assert entry == {"adapter": "registry", "path": str(tmp_path / "mesh" / "peers.json")}
        env = mesh_environment(manager._mesh)
        assert json.loads(env["SKULD__MESH__DISCOVERY_ADAPTERS"]) == [entry]

    def test_no_mesh_no_discovery(self, tmp_path) -> None:
        assert self._manager(tmp_path, None)._mesh == {}

    def test_an_operator_choice_of_discovery_is_kept(self, tmp_path) -> None:
        chosen = [{"adapter": "static", "peers": []}]
        manager = self._manager(tmp_path, {"enabled": True, "discovery_adapters": chosen})
        assert manager._mesh["discovery_adapters"] == chosen


class TestDirectKubernetes:
    def _manager(self, mesh) -> DirectK8sPodManager:
        return DirectK8sPodManager(
            namespace="sessions",
            skuld_image="skuld:test",
            nginx_image="nginx:test",
            devrunner_image="dev:test",
            mesh=mesh,
        )

    def _session(self) -> Session:
        return Session(
            id=uuid4(), name="s", model="m", source=GitSource(repo="https://x/y", branch="main")
        )

    def test_pods_find_each_other_through_k8s_discovery_on_a_pod_ip_address(self) -> None:
        manager = self._manager({"enabled": True, "realm_id": "flock-a"})
        session = self._session()
        env = manager._build_env(session, make_spec())
        names = [e["name"] for e in env]

        assert env[names.index("POD_IP")]["valueFrom"]["fieldRef"]["fieldPath"] == ("status.podIP")
        values = {e["name"]: e["value"] for e in env if "value" in e}
        assert values["SKULD__MESH__NNG__PUB_SUB_ADDRESS"] == "tcp://0.0.0.0:7480"
        assert values["SKULD__MESH__NNG__ADVERTISE_HOST"] == "$(POD_IP)"
        # Kubernetes expands $(POD_IP) only from an entry defined earlier.
        assert names.index("POD_IP") < names.index("SKULD__MESH__NNG__ADVERTISE_HOST")
        assert json.loads(values["SKULD__MESH__DISCOVERY_ADAPTERS"]) == [
            {"adapter": "k8s", "namespace": "sessions", "nng_port": 7480}
        ]

    def test_the_pod_carries_the_labels_and_peer_id_discovery_reads(self) -> None:
        manager = self._manager({"enabled": True, "realm_id": "flock-a"})
        session = self._session()
        template = manager._build_deployment_manifest(session, make_spec())["spec"]["template"]

        assert template["metadata"]["labels"]["ravn.niuu.world/realm"] == "flock-a"
        assert template["metadata"]["labels"]["ravn.niuu.world/role"] == "agent"
        assert template["metadata"]["annotations"]["ravn.niuu.world/peer-id"] == str(session.id)
        assert template["metadata"]["annotations"]["ravn.niuu.world/participant-type"] == "skuld"
        assert template["metadata"]["annotations"]["ravn.niuu.world/display-name"] == "s"

    def test_a_mesh_that_is_off_adds_no_pod_ip_and_no_labels(self) -> None:
        manager = self._manager(None)
        session = self._session()
        env = manager._build_env(session, make_spec())
        template = manager._build_deployment_manifest(session, make_spec())["spec"]["template"]

        assert "POD_IP" not in [e["name"] for e in env]
        assert "ravn.niuu.world/realm" not in template["metadata"]["labels"]
        assert "ravn.niuu.world/peer-id" not in template["metadata"]["annotations"]

    def test_operator_discovery_keeps_the_pod_without_k8s_labels(self) -> None:
        manager = self._manager(
            {"enabled": True, "discovery_adapters": [{"adapter": "static", "peers": []}]}
        )
        template = manager._build_deployment_manifest(self._session(), make_spec())["spec"][
            "template"
        ]
        assert "ravn.niuu.world/realm" not in template["metadata"]["labels"]


class TestDirectKubernetesRbac:
    """k8s discovery lists pods: the pod's ServiceAccount gets a namespaced Role to do only that."""

    def _manager(self, mesh) -> DirectK8sPodManager:
        return TestDirectKubernetes()._manager(mesh)

    def _session(self) -> Session:
        return TestDirectKubernetes()._session()

    def test_a_role_that_reads_pods_and_a_binding_to_the_pods_service_account(self) -> None:
        manager = self._manager({"enabled": True})
        session = self._session()
        spec = make_spec()
        spec.pod_spec = PodSpecAdditions(service_account="openbao-s1")

        role, binding = manager._mesh_rbac_manifests(session, spec)

        name = f"skuld-{session.id}-mesh-discovery"
        assert role["kind"] == "Role" and role["metadata"]["namespace"] == "sessions"
        assert role["metadata"]["name"] == binding["metadata"]["name"] == name
        assert role["rules"] == [
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch"]}
        ]
        assert binding["roleRef"] == {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": name,
        }
        assert binding["subjects"] == [
            {"kind": "ServiceAccount", "name": "openbao-s1", "namespace": "sessions"}
        ]

    def test_without_a_contributed_service_account_it_is_the_default_one(self) -> None:
        _, binding = self._manager({"enabled": True})._mesh_rbac_manifests(
            self._session(), make_spec()
        )
        assert binding["subjects"][0]["name"] == "default"

    @pytest.mark.parametrize(
        "mesh",
        [
            None,
            {"enabled": False},
            {"enabled": True, "discovery_adapters": [{"adapter": "static", "peers": []}]},
            {"enabled": True, "transport": "nats"},
            {
                "enabled": True,
                "discovery_adapters": [{"adapter": "k8s", "namespace": "elsewhere"}],
            },
            {"enabled": True, "discovery_adapters": [{"adapter": "k8s"}]},
        ],
        ids=["off", "disabled", "static", "nats", "other-namespace", "every-namespace"],
    )
    def test_no_role_unless_the_mesh_lists_pods_of_this_namespace(self, mesh) -> None:
        assert self._manager(mesh)._mesh_rbac_manifests(self._session(), make_spec()) is None

    @pytest.mark.asyncio
    async def test_start_creates_the_role_and_binding_before_the_deployment(self) -> None:
        manager = self._manager({"enabled": True})
        manager._api_client = MagicMock()
        apply = AsyncMock()
        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()),
            patch.object(manager, "_apply_resource", apply),
        ):
            await manager.start(self._session(), make_spec())

        calls = [
            (c.kwargs["api_class"], c.kwargs["api_method_create"]) for c in apply.call_args_list
        ]
        rbac = [i for i, c in enumerate(calls) if c[0] == "RbacAuthorizationV1Api"]
        deployment = calls.index(("AppsV1Api", "create_namespaced_deployment"))
        assert [calls[i][1] for i in rbac] == [
            "create_namespaced_role",
            "create_namespaced_role_binding",
        ]
        assert max(rbac) < deployment

    @pytest.mark.asyncio
    async def test_a_forge_that_may_not_create_roles_fails_the_start_naming_the_permission(
        self,
    ) -> None:
        manager = self._manager({"enabled": True})
        manager._api_client = MagicMock()

        class ForbiddenError(Exception):
            status = 403

        async def apply(**kwargs) -> None:
            if kwargs["api_class"] == "RbacAuthorizationV1Api":
                raise ForbiddenError()

        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()),
            patch.object(manager, "_apply_resource", AsyncMock(side_effect=apply)) as applied,
            patch.object(manager, "_get_api_exception", return_value=ForbiddenError),
            pytest.raises(MeshDiscoveryRbacError, match="roles and rolebindings"),
        ):
            await manager.start(self._session(), make_spec())

        assert all(c.kwargs["api_class"] != "AppsV1Api" for c in applied.call_args_list)

    @pytest.mark.asyncio
    async def test_another_api_error_is_not_dressed_up_as_a_permission_problem(self) -> None:
        manager = self._manager({"enabled": True})
        manager._api_client = MagicMock()

        class BrokenError(Exception):
            status = 500

        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()),
            patch.object(manager, "_apply_resource", AsyncMock(side_effect=BrokenError())),
            patch.object(manager, "_get_api_exception", return_value=BrokenError),
            pytest.raises(BrokenError),
        ):
            await manager.start(self._session(), make_spec())

    @pytest.mark.asyncio
    async def test_stop_removes_them_only_for_a_session_that_had_them(self) -> None:
        for mesh, deleted in (({"enabled": True}, True), (None, False)):
            manager = self._manager(mesh)
            manager._api_client = MagicMock()
            delete = AsyncMock(return_value=True)
            with (
                patch.object(manager, "_delete_resource", delete),
                patch.object(manager, "_delete_custom_resource", AsyncMock()),
            ):
                await manager.stop(self._session())
            rbac = [
                c.args[1] for c in delete.call_args_list if c.args[0] == "RbacAuthorizationV1Api"
            ]
            assert rbac == (
                ["delete_namespaced_role_binding", "delete_namespaced_role"] if deleted else []
            )
