"""A workflow run on the direct Kubernetes manager is one unit: one pod, a container per member."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from volundr.adapters.outbound.direct_k8s_pod_manager import (
    MEMBERS_ANNOTATION,
    DirectK8sPodManager,
)
from volundr.domain.models import (
    HOSTED_MEMBERS_KEY,
    HOSTED_UNIT_KEY,
    GitSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import HostedUnitError
from volundr.domain.services.forge_session_launch import with_forge_mcp

CREDENTIAL_PATH = "/var/run/secrets/niuu-forge-session"
GIT = {
    "cloneUrl": "https://github.com/org/repo.git",
    "repoUrl": "https://github.com/org/repo.git",
    "branch": "feat/run",
}


@dataclass
class Launch:
    session: Session
    spec: SessionSpec


def _config(launch: str) -> dict:
    return with_forge_mcp({}, grants=[], launch_id=launch)


def _root(members: list[Session] | None = None, *, recorded: bool = True) -> Session:
    config: dict = {HOSTED_UNIT_KEY: {}, **_config("root-launch")}
    if recorded:
        config[HOSTED_MEMBERS_KEY] = [
            {"id": str(m.id), "port": m.hosted_port} for m in members or []
        ]
    return Session(
        id=uuid4(),
        name="wf-run",
        owner_id="u1",
        tenant_id="t1",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        workload_config=config,
    )


def _member(root: Session, name: str, port: int, launch: str = "launch-1") -> Session:
    return Session(
        id=uuid4(),
        name=name,
        owner_id="u1",
        tenant_id="t1",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        hosted_by=root.id,
        hosted_port=port,
        workload_config=_config(launch),
    )


def _member_spec(member: Session, **extra_values) -> SessionSpec:
    """What the contributor pipeline gives a member, with its credential Secret mounted."""
    volume = f"forge-session-{member.id.hex[:12]}"
    return SessionSpec(
        values={
            "session": {"systemPrompt": f"you are {member.name}"},
            "forgeMcp": {"tokenFile": f"{CREDENTIAL_PATH}/token", "grants": ["notify"]},
            **extra_values,
        },
        pod_spec=PodSpecAdditions(
            volumes=({"name": volume, "secret": {"secretName": f"forge-session-{member.id.hex}"}},),
            volume_mounts=({"name": volume, "mountPath": CREDENTIAL_PATH, "readOnly": True},),
        ),
    )


async def _start(manager, root: Session, spec: SessionSpec):
    """Start ``root`` with the manager composed as Forge composes it: a member source."""
    members = list(getattr(spec, "members", ()) or ())

    async def source(unit_id, member_id):
        return [m for m in members if member_id in (None, m.session.id)]

    manager.configure_hosted_members(source)
    return await manager.start(root, spec)


def _unit(**root_values) -> tuple[Session, list[Launch], SessionSpec]:
    root = _root()
    coder = _member(root, "coder", 8082)
    reviewer = _member(root, "reviewer", 8083)
    root = _root([coder, reviewer])
    coder = coder.model_copy(update={"hosted_by": root.id})
    reviewer = reviewer.model_copy(update={"hosted_by": root.id})
    launches = [Launch(coder, _member_spec(coder)), Launch(reviewer, _member_spec(reviewer))]
    spec = SessionSpec(
        values={
            "session": {"systemPrompt": "root"},
            "forgeMcp": {"tokenFile": f"{CREDENTIAL_PATH}/token", "grants": ["notify"]},
            **root_values,
        },
        pod_spec=PodSpecAdditions(
            volumes=({"name": "forge-session-root", "secret": {"secretName": "forge-root"}},),
            volume_mounts=(
                {"name": "forge-session-root", "mountPath": CREDENTIAL_PATH, "readOnly": True},
            ),
        ),
    )
    spec.members = tuple(launches)
    return root, launches, spec


@pytest.fixture
def manager() -> DirectK8sPodManager:
    return DirectK8sPodManager(
        namespace="ns",
        skuld_image="skuld:test",
        volundr_api_url="http://forge.test",
        mesh={"enabled": True},
    )


def _pod(manager, root, spec, launches=None):
    members = tuple(spec.members if launches is None else launches)
    manifest = manager._build_deployment_manifest(root, spec, members)
    return manifest, manifest["spec"]["template"]["spec"]


def _env(container: dict) -> dict[str, str]:
    return {e["name"]: e["value"] for e in container["env"] if "value" in e}


def _container(pod: dict, name: str) -> dict:
    return next(c for c in pod["containers"] if c["name"] == name)


class TestPod:
    def test_the_pod_has_a_skuld_container_per_member_beside_the_roots(self, manager) -> None:
        root, launches, spec = _unit()

        _, pod = _pod(manager, root, spec)

        names = [c["name"] for c in pod["containers"]]
        assert names == ["nginx", "skuld", "member-8082", "member-8083", "vscode-reh", "devrunner"]
        member = _container(pod, "member-8083")
        assert member["image"] == "skuld:test"
        assert member["ports"] == [{"containerPort": 8083, "name": "m8083"}]

    def test_each_member_runs_as_its_own_session_on_its_own_port(self, manager) -> None:
        root, launches, spec = _unit()

        _, pod = _pod(manager, root, spec)

        for launch in launches:
            env = _env(_container(pod, f"member-{launch.session.hosted_port}"))
            assert env["SESSION_ID"] == str(launch.session.id)
            assert env["SKULD__SESSION__SYSTEM_PROMPT"] == f"you are {launch.session.name}"
            assert env["SKULD__PORT"] == str(launch.session.hosted_port)
            assert env["SKULD__HOST"] == "127.0.0.1"
            assert env["SKULD__SESSION__OWNER_ID"] == "u1"
        assert "SKULD__PORT" not in _env(_container(pod, "skuld"))

    def test_the_members_share_the_roots_checkout(self, manager) -> None:
        root, launches, spec = _unit()

        _, pod = _pod(manager, root, spec)

        for launch in launches:
            container = _container(pod, f"member-{launch.session.hosted_port}")
            assert _env(container)["SKULD__SESSION__WORKSPACE_DIR"] == (
                f"/volundr/sessions/{root.id}/workspace"
            )
            assert {"name": "workspace", "mountPath": "/volundr"} in container["volumeMounts"]

    def test_the_mesh_is_loopback_with_a_registry_file_in_a_shared_directory(self, manager) -> None:
        root, launches, spec = _unit()

        manifest, pod = _pod(manager, root, spec)

        assert {"name": "mesh", "emptyDir": {}} in pod["volumes"]
        for name in ("skuld", "member-8082", "member-8083"):
            container = _container(pod, name)
            assert {"name": "mesh", "mountPath": "/tmp/niuu-mesh"} in container["volumeMounts"]
            env = _env(container)
            assert env["SKULD__MESH__ENABLED"] == "true"
            assert json.loads(env["SKULD__MESH__DISCOVERY_ADAPTERS"]) == [
                {"adapter": "registry", "path": "/tmp/niuu-mesh/peers.json"}
            ]
            assert env["SKULD__MESH__NNG__PUB_SUB_ADDRESS"] == "tcp://127.0.0.1:0"
            assert env["SKULD__MESH__NNG__ADVERTISE_HOST"] == ""
            assert "POD_IP" not in {e["name"] for e in container["env"]}
        for name in ("nginx", "vscode-reh", "devrunner"):
            mounts = {m["mountPath"] for m in _container(pod, name)["volumeMounts"]}
            assert "/tmp/niuu-mesh" not in mounts
        # No pod listing: no discovery labels, no Role.
        assert not any(
            k.startswith("ravn.niuu.world/")
            for k in manifest["spec"]["template"]["metadata"]["labels"]
        )

    def test_each_members_credential_is_in_its_own_container_only(self, manager) -> None:
        root, launches, spec = _unit()

        _, pod = _pod(manager, root, spec)

        volumes = {v["name"]: v for v in pod["volumes"]}
        mounts_of = {
            c["name"]: {m["name"]: m["mountPath"] for m in c["volumeMounts"]}
            for c in pod["containers"]
        }
        for launch in launches:
            port = launch.session.hosted_port
            volume = f"member-{port}-forge-session-{launch.session.id.hex[:12]}"
            assert (
                volumes[volume]["secret"]["secretName"] == f"forge-session-{launch.session.id.hex}"
            )
            holders = [name for name, mounts in mounts_of.items() if volume in mounts]
            assert holders == [f"member-{port}"]
            assert mounts_of[f"member-{port}"][volume] == CREDENTIAL_PATH
        # The root's own credential stays in the root's container.
        holders = [n for n, m in mounts_of.items() if "forge-session-root" in m]
        assert holders == ["skuld"]

    def test_members_start_with_the_pod_and_have_no_probes(self, manager) -> None:
        root, launches, spec = _unit()

        manifest, pod = _pod(manager, root, spec)

        assert manifest["spec"]["replicas"] == 1
        for name in ("member-8082", "member-8083"):
            assert "readinessProbe" not in _container(pod, name)

    def test_a_member_gets_its_own_resources(self, manager) -> None:
        root, launches, spec = _unit()
        launches[1].spec.values["resources"] = {"limits": {"memory": "4Gi"}}

        _, pod = _pod(manager, root, spec)

        assert _container(pod, "member-8082")["resources"]["limits"]["memory"] == "1Gi"
        assert _container(pod, "member-8083")["resources"]["limits"]["memory"] == "4Gi"

    def test_the_members_and_their_launches_are_recorded_on_the_deployment(self, manager) -> None:
        root, launches, spec = _unit()

        manifest, _ = _pod(manager, root, spec)

        recorded = json.loads(manifest["metadata"]["annotations"][MEMBERS_ANNOTATION])
        assert recorded == [
            {"id": str(m.session.id), "port": m.session.hosted_port, "launch": "launch-1"}
            for m in launches
        ]

    def test_an_ordinary_session_is_unchanged(self, manager) -> None:
        session = Session(id=uuid4(), name="plain", source=GitSource(repo="r", branch="b"))
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        manifest, pod = _pod(manager, session, spec, launches=())

        assert [c["name"] for c in pod["containers"]] == [
            "nginx",
            "skuld",
            "vscode-reh",
            "devrunner",
        ]
        assert "annotations" not in manifest["metadata"]
        assert "mesh" not in {v["name"] for v in pod["volumes"]}

    def test_the_pods_checkout_is_cloned_from_what_the_members_agree_on(self, manager) -> None:
        root, launches, spec = _unit()
        for launch in launches:
            launch.spec.values["git"] = dict(GIT)

        _, pod = _pod(manager, root, spec)

        clone = next(c for c in pod["initContainers"] if c["name"] == "git-clone")
        assert "feat/run" in clone["args"][0]
        assert "github.com/org/repo.git" in clone["args"][0]

    def test_the_roots_repository_wins_and_members_must_agree(self, manager) -> None:
        root, launches, spec = _unit(git=dict(GIT))
        launches[0].spec.values["git"] = {**GIT, "branch": "other"}

        with pytest.raises(ValueError, match="share a repository and branch"):
            _pod(manager, root, spec)


class TestNginx:
    def test_the_pods_nginx_routes_each_member_to_its_own_skuld(self, manager) -> None:
        root, launches, _ = _unit()

        config = manager._build_nginx_configmap(root, launches)["data"]["nginx.conf"]

        for launch in launches:
            port = launch.session.hosted_port
            assert f"upstream member_{port} {{\n        server 127.0.0.1:{port};" in config
            assert f"location /m/{launch.session.id}/ {{" in config
            assert f"proxy_pass http://member_{port}/;" in config
        assert 'proxy_set_header Connection "upgrade";' in config

    def test_an_ordinary_pod_has_no_member_routes(self, manager) -> None:
        session = Session(id=uuid4(), name="plain")

        config = manager._build_nginx_configmap(session)["data"]["nginx.conf"]

        assert "/m/" not in config
        assert "{member_" not in config


class TestRefusals:
    def test_members_that_did_not_arrive_are_refused(self, manager) -> None:
        root, _, spec = _unit()

        with pytest.raises(ValueError, match="came from the member source"):
            manager._require_members(root, ())

    def test_members_that_differ_from_the_recorded_ones_are_refused(self, manager) -> None:
        root, launches, _ = _unit()

        with pytest.raises(ValueError, match="fixed when its unit starts"):
            manager._require_members(root, launches[:1])

    def test_members_of_a_session_that_is_not_a_unit_root_are_refused(self, manager) -> None:
        root, launches, _ = _unit()
        plain = root.model_copy(update={"workload_config": {}})

        with pytest.raises(ValueError, match="not a unit root"):
            manager._require_members(plain, launches)

    def test_a_member_hosted_by_another_unit_is_refused(self, manager) -> None:
        root, launches, _ = _unit()
        stray = Launch(
            launches[0].session.model_copy(update={"hosted_by": uuid4()}), launches[0].spec
        )

        with pytest.raises(ValueError, match="is hosted by"):
            manager._require_members(root, [stray, launches[1]])

    def test_a_member_without_a_port_is_refused(self, manager) -> None:
        root, launches, _ = _unit()
        portless = Launch(
            launches[0].session.model_copy(update={"hosted_port": None}), launches[0].spec
        )

        with pytest.raises(ValueError, match="no broker port"):
            manager._require_members(root, [portless, launches[1]])

    def test_a_member_launched_twice_is_refused(self, manager) -> None:
        root, launches, _ = _unit()

        with pytest.raises(ValueError, match="launched twice"):
            manager._require_members(root, [launches[0], launches[0]])

    def test_two_members_on_one_port_are_refused(self, manager) -> None:
        root, launches, _ = _unit()
        clash = Launch(
            launches[1].session.model_copy(update={"hosted_port": 8082}), launches[1].spec
        )
        root = root.model_copy(
            update={
                "workload_config": {
                    **root.workload_config,
                    HOSTED_MEMBERS_KEY: [
                        {"id": str(launches[0].session.id), "port": 8082},
                        {"id": str(clash.session.id), "port": 8082},
                    ],
                }
            }
        )

        with pytest.raises(ValueError, match="same broker port"):
            manager._require_members(root, [launches[0], clash])

    def test_a_member_on_the_roots_broker_port_is_refused(self, manager) -> None:
        root, launches, _ = _unit()
        on_root = Launch(
            launches[0].session.model_copy(update={"hosted_port": 8081}), launches[0].spec
        )
        root = root.model_copy(
            update={
                "workload_config": {
                    **root.workload_config,
                    HOSTED_MEMBERS_KEY: [{"id": str(on_root.session.id), "port": 8081}],
                }
            }
        )

        with pytest.raises(ValueError, match="same broker port"):
            manager._require_members(root, [on_root])

    def test_a_member_cannot_start_alone(self, manager) -> None:
        _, launches, _ = _unit()

        with pytest.raises(ValueError, match="starts with its unit"):
            manager._require_members(launches[0].session, ())

    def test_the_mesh_off_is_refused(self) -> None:
        manager = DirectK8sPodManager(namespace="ns", volundr_api_url="http://forge.test")
        root, _, spec = _unit()

        with pytest.raises(ValueError, match="mesh.enabled: true"):
            _pod(manager, root, spec)

    @pytest.mark.parametrize("field", ["init_containers", "extra_containers"])
    def test_a_member_cannot_add_containers_to_the_pod(self, manager, field) -> None:
        root, launches, spec = _unit()
        launches[0].spec.pod_spec = replace(launches[0].spec.pod_spec, **{field: ({"name": "x"},)})

        with pytest.raises(ValueError, match=f"asks for {field} of its own"):
            _pod(manager, root, spec)

    def test_a_member_cannot_bring_its_own_service_account(self, manager) -> None:
        root, launches, spec = _unit()
        launches[1].spec.pod_spec = replace(launches[1].spec.pod_spec, service_account="other")

        with pytest.raises(ValueError, match="service_account"):
            _pod(manager, root, spec)

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("nodeSelector", {"gpu": "yes"}),
            ("tolerations", [{"key": "x"}]),
            ("runtimeClassName", "kata"),
            ("homeVolume", {"enabled": True}),
        ],
    )
    def test_a_member_cannot_ask_for_a_different_pod(self, manager, key, value) -> None:
        root, launches, spec = _unit()
        launches[0].spec.values[key] = value

        with pytest.raises(ValueError, match=f"{key}=.*members share one pod"):
            _pod(manager, root, spec)

    def test_a_member_cannot_ask_for_pod_annotations_the_unit_does_not_have(self, manager) -> None:
        root, launches, spec = _unit()
        launches[0].spec.pod_spec = replace(
            launches[0].spec.pod_spec, annotations={"vault.hashicorp.com/agent-inject": "true"}
        )

        with pytest.raises(ValueError, match="pod annotation"):
            _pod(manager, root, spec)

    def test_the_roots_secret_injection_is_the_pods_and_reaches_every_member(self, manager) -> None:
        """OpenBao injection is the root's; its members add nothing and are injected too."""
        root, launches, spec = _unit()
        spec.pod_spec = replace(
            spec.pod_spec,
            service_account=f"openbao-session-{root.id}",
            annotations={
                "vault.hashicorp.com/agent-inject": "true",
                "vault.hashicorp.com/agent-inject-containers": "skuld,devrunner",
            },
        )

        manifest, pod = _pod(manager, root, spec)

        assert pod["serviceAccountName"] == f"openbao-session-{root.id}"
        injected = manifest["spec"]["template"]["metadata"]["annotations"][
            "vault.hashicorp.com/agent-inject-containers"
        ].split(",")
        assert injected == ["skuld", "devrunner", "member-8082", "member-8083"]

    def test_annotations_the_unit_has_are_accepted(self, manager) -> None:
        root, launches, spec = _unit()
        spec.pod_spec = replace(spec.pod_spec, annotations={"a": "b"})
        launches[0].spec.pod_spec = replace(launches[0].spec.pod_spec, annotations={"a": "b"})

        _pod(manager, root, spec)


class TestStart:
    async def test_nothing_is_applied_when_the_unit_cannot_start_whole(self, manager) -> None:
        root, launches, spec = _unit()
        launches[0].spec.values["nodeSelector"] = {"gpu": "yes"}
        manager._api_client = MagicMock()

        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()) as custom,
            patch.object(manager, "_apply_resource", AsyncMock()) as apply,
            pytest.raises(ValueError, match="nodeSelector"),
        ):
            await _start(manager, root, spec)

        custom.assert_not_called()
        apply.assert_not_called()

    async def test_the_unit_is_one_deployment_and_needs_no_mesh_role(self, manager) -> None:
        root, launches, spec = _unit()
        manager._api_client = MagicMock()

        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()),
            patch.object(manager, "_apply_resource", AsyncMock()) as apply,
        ):
            result = await _start(manager, root, spec)

        applied = [c.kwargs["api_class"] for c in apply.call_args_list]
        assert applied.count("AppsV1Api") == 1
        assert "RbacAuthorizationV1Api" not in applied
        deployment = next(
            c.kwargs["manifest"]
            for c in apply.call_args_list
            if c.kwargs["api_class"] == "AppsV1Api"
        )
        names = [c["name"] for c in deployment["spec"]["template"]["spec"]["containers"]]
        assert "member-8082" in names and "member-8083" in names
        assert result.pod_name == f"skuld-{root.id}"
        config = next(
            c.kwargs["manifest"]
            for c in apply.call_args_list
            if c.kwargs["manifest"]["kind"] == "ConfigMap"
        )
        assert f"/m/{launches[0].session.id}/" in config["data"]["nginx.conf"]

    async def test_members_survive_the_brokered_credential_copy(self, manager) -> None:
        root, launches, spec = _unit()
        manager._api_client = MagicMock()

        with (
            patch.object(manager, "_apply_custom_resource", AsyncMock()),
            patch.object(manager, "_apply_resource", AsyncMock()) as apply,
        ):
            await _start(manager, root, spec)

        deployment = next(
            c.kwargs["manifest"]
            for c in apply.call_args_list
            if c.kwargs["api_class"] == "AppsV1Api"
        )
        assert len(deployment["spec"]["template"]["spec"]["containers"]) == 6


def _deployment(manager, root, spec, *, ready=1):
    manifest = manager._build_deployment_manifest(root, spec, tuple(spec.members))
    annotations = manifest["metadata"]["annotations"]
    return SimpleNamespace(
        metadata=SimpleNamespace(annotations=annotations),
        spec=SimpleNamespace(replicas=1),
        status=SimpleNamespace(ready_replicas=ready, conditions=None),
    )


def _state(*, running=False, waiting=None, terminated=None):
    return SimpleNamespace(
        running=SimpleNamespace() if running else None,
        waiting=SimpleNamespace(reason=waiting, message="boom") if waiting else None,
        terminated=SimpleNamespace(exit_code=terminated) if terminated is not None else None,
    )


def _pod_with(**containers):
    return SimpleNamespace(
        metadata=SimpleNamespace(deletion_timestamp=None),
        status=SimpleNamespace(
            container_statuses=[
                SimpleNamespace(name=name, state=state) for name, state in containers.items()
            ]
        ),
    )


def _api(pod):
    api = MagicMock()
    api.list_namespaced_pod = AsyncMock(return_value=SimpleNamespace(items=[pod] if pod else []))
    return api


class TestMemberStatus:
    async def _status(self, manager, root, spec, member, *, deployment, pod):
        manager._api_client = MagicMock()
        with (
            patch.object(manager, "_read_deployment", AsyncMock(return_value=deployment)),
            patch.object(manager, "_get_api", return_value=_api(pod)),
        ):
            return await manager.status(member), await manager.status_detail(member)

    async def test_a_running_container_is_a_running_member(self, manager) -> None:
        root, launches, spec = _unit()
        pod = _pod_with(
            **{"member-8082": _state(running=True), "member-8083": _state(running=True)}
        )

        status, detail = await self._status(
            manager,
            root,
            spec,
            launches[0].session,
            deployment=_deployment(manager, root, spec),
            pod=pod,
        )

        assert status is SessionStatus.RUNNING
        assert detail is None

    async def test_a_member_is_judged_by_its_own_container_not_the_pod(self, manager) -> None:
        root, launches, spec = _unit()
        pod = _pod_with(
            **{
                "member-8082": _state(running=True),
                "member-8083": _state(waiting="CrashLoopBackOff"),
            }
        )
        deployment = _deployment(manager, root, spec, ready=0)

        live, _ = await self._status(
            manager, root, spec, launches[0].session, deployment=deployment, pod=pod
        )
        dead, detail = await self._status(
            manager, root, spec, launches[1].session, deployment=deployment, pod=pod
        )

        assert live is SessionStatus.RUNNING
        assert dead is SessionStatus.FAILED
        assert detail == "container member-8083: CrashLoopBackOff: boom"

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (_state(terminated=0), SessionStatus.STOPPED),
            (_state(terminated=1), SessionStatus.FAILED),
            (_state(waiting="ContainerCreating"), SessionStatus.STARTING),
            (_state(waiting="ImagePullBackOff"), SessionStatus.FAILED),
        ],
    )
    async def test_container_states(self, manager, state, expected) -> None:
        root, launches, spec = _unit()
        pod = _pod_with(**{"member-8082": state})

        status, _ = await self._status(
            manager,
            root,
            spec,
            launches[0].session,
            deployment=_deployment(manager, root, spec),
            pod=pod,
        )

        assert status is expected

    async def test_a_container_the_kubelet_has_not_reported_is_starting(self, manager) -> None:
        root, launches, spec = _unit()

        status, _ = await self._status(
            manager,
            root,
            spec,
            launches[0].session,
            deployment=_deployment(manager, root, spec),
            pod=_pod_with(),
        )

        assert status is SessionStatus.STARTING

    async def test_no_pod_yet_reads_as_the_deployment_does(self, manager) -> None:
        root, launches, spec = _unit()

        status, detail = await self._status(
            manager,
            root,
            spec,
            launches[0].session,
            deployment=_deployment(manager, root, spec, ready=0),
            pod=None,
        )

        assert status is SessionStatus.STARTING
        assert detail is None

    async def test_a_unit_that_is_gone_is_stopped(self, manager) -> None:
        root, launches, spec = _unit()

        status, _ = await self._status(
            manager, root, spec, launches[0].session, deployment=None, pod=None
        )

        assert status is SessionStatus.STOPPED

    async def test_a_session_the_pod_was_not_started_with_is_stopped(self, manager) -> None:
        root, launches, spec = _unit()
        stranger = _member(root, "late", 8090)
        root_pod = _pod_with()

        status, _ = await self._status(
            manager, root, spec, stranger, deployment=_deployment(manager, root, spec), pod=root_pod
        )

        assert status is SessionStatus.STOPPED

    async def test_a_member_started_again_cannot_run_under_its_new_launch(self, manager) -> None:
        root, launches, spec = _unit()
        relaunched = launches[0].session.model_copy(update={"workload_config": _config("launch-2")})
        pod = _pod_with(**{"member-8082": _state(running=True)})

        status, detail = await self._status(
            manager, root, spec, relaunched, deployment=_deployment(manager, root, spec), pod=pod
        )

        assert status is SessionStatus.FAILED
        assert "cannot be restarted one at a time" in detail
        assert "stop the run and start it again" in detail

    async def test_the_roots_status_is_the_deployments(self, manager) -> None:
        root, _, spec = _unit()
        manager._api_client = MagicMock()
        with patch.object(
            manager, "_read_deployment", AsyncMock(return_value=_deployment(manager, root, spec))
        ):
            assert await manager.status(root) is SessionStatus.RUNNING


class TestMemberEndpoints:
    def test_a_members_chat_endpoint_is_its_path_on_the_units_ingress(self, manager) -> None:
        root, launches, _ = _unit()
        member = launches[0].session

        assert manager.initial_chat_endpoint(member) == (
            f"ws://k3d-volundr-serverlb/s/{root.id}/m/{member.id}/session"
        )
        assert manager.initial_chat_endpoint(root) == (
            f"ws://k3d-volundr-serverlb/s/{root.id}/session"
        )

    async def test_forge_reaches_a_member_through_the_same_path(self, manager) -> None:
        root, launches, _ = _unit()
        member = launches[1].session

        target = await manager.broker_target(member)

        assert target.service_url == f"http://k3d-volundr-serverlb/s/{root.id}/m/{member.id}"
        assert (target.connect_host, target.connect_port) == ("k3d-volundr-serverlb", 80)

    async def test_the_ingress_backends_port_is_dialled(self) -> None:
        manager = DirectK8sPodManager(ingress_backend="10.0.0.1:8080")
        root, launches, _ = _unit()

        target = await manager.broker_target(launches[0].session)

        assert (target.connect_host, target.connect_port) == ("10.0.0.1", 8080)

    async def test_an_ordinary_session_has_none(self, manager) -> None:
        assert await manager.broker_target(Session(id=uuid4(), name="plain")) is None

    def test_a_member_belongs_to_its_units_release(self, manager) -> None:
        root, launches, _ = _unit()

        assert manager._release_name(launches[0].session) == f"skuld-{root.id}"


class TestStop:
    async def test_stopping_a_member_removes_nothing(self, manager) -> None:
        _, launches, _ = _unit()
        manager._api_client = MagicMock()

        with patch.object(manager, "_delete_resource", AsyncMock()) as delete:
            assert await manager.stop(launches[0].session) is False

        delete.assert_not_called()

    async def test_stopping_the_root_removes_the_unit(self, manager) -> None:
        root, _, _ = _unit()
        manager._api_client = MagicMock()

        with (
            patch.object(manager, "_delete_resource", AsyncMock(return_value=True)) as delete,
            patch.object(manager, "_delete_custom_resource", AsyncMock()),
        ):
            assert await manager.stop(root) is True

        assert delete.call_args_list[0].args[2] == f"skuld-{root.id}"


class TestMemberHomes:
    def test_each_member_keeps_its_own_agent_state(self, manager) -> None:
        root, launches, spec = _unit()

        _, pod = _pod(manager, root, spec)

        by_name = {
            c["name"]: {e["name"]: e.get("value") for e in c.get("env", [])}
            for c in pod["containers"]
        }
        for launch in launches:
            port = launch.session.hosted_port
            home = f"/volundr/sessions/{root.id}/members/{port}/home"
            env = by_name[f"member-{port}"]
            assert env["CODEX_HOME"] == f"{home}/.codex"
            assert env["CLAUDE_CONFIG_DIR"] == f"{home}/.claude"
        assert by_name["member-8082"]["CODEX_HOME"] != by_name["member-8083"]["CODEX_HOME"]
        assert "CODEX_HOME" not in by_name["skuld"]

    def test_a_launch_that_sets_a_home_itself_does_not_share_it(self, manager) -> None:
        root, launches, spec = _unit()
        launches[0].spec.pod_spec = PodSpecAdditions(
            env=({"name": "CODEX_HOME", "value": "/home/user/.codex"},),
            volumes=launches[0].spec.pod_spec.volumes,
            volume_mounts=launches[0].spec.pod_spec.volume_mounts,
        )

        _, pod = _pod(manager, root, spec)

        member = next(c for c in pod["containers"] if c["name"] == "member-8082")
        values = [e["value"] for e in member["env"] if e["name"] == "CODEX_HOME"]
        assert values == [f"/volundr/sessions/{root.id}/members/8082/home/.codex"]


class TestMemberHarness:
    """A container of the pod is stopped and restarted by asking the member's own Skuld."""

    @staticmethod
    def _wired(manager, handler):
        import httpx

        from volundr.adapters.outbound.hosted_member_harness import MemberHarnessControl

        manager._harness = MemberHarnessControl(
            restart_wait_seconds=5,
            poll_seconds=0.001,
            request_seconds=5,
            transport=httpx.MockTransport(handler),
        )

    async def test_stop_asks_the_members_skuld(self, manager) -> None:
        import httpx

        root, launches, spec = _unit()
        member = launches[0].session
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path))
            return httpx.Response(200, json={"status": "stopped"})

        self._wired(manager, handler)

        await manager.stop_hosted_member(member)

        assert seen == [("POST", f"/s/{root.id}/m/{member.id}/api/harness/stop")]

    async def test_restart_records_the_new_launch_on_the_deployment(self, manager) -> None:
        import httpx

        root, launches, spec = _unit()
        old = launches[0].session
        new = old.model_copy(update={"workload_config": _config("launch-2")})
        manager.configure_hosted_members(
            lambda unit_id, member_id: _answer([Launch(new, launches[0].spec)])
        )
        deployment = _deployment(manager, root, spec)
        apps = MagicMock()
        apps.patch_namespaced_deployment = AsyncMock()
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, request.content))
            return httpx.Response(200, json={"status": "ok"})

        self._wired(manager, handler)
        with (
            patch.object(manager, "_ensure_client", AsyncMock()),
            patch.object(manager, "_read_deployment", AsyncMock(return_value=deployment)),
            patch.object(manager, "_get_api", return_value=apps),
        ):
            await manager.restart_hosted_member(new)

        assert calls[0][:2] == ("POST", f"/s/{root.id}/m/{new.id}/api/harness/restart")
        assert b"launch-2" in calls[0][2]
        patched = apps.patch_namespaced_deployment.call_args.kwargs["body"]["metadata"]
        recorded = {
            e["id"]: e["launch"] for e in json.loads(patched["annotations"][MEMBERS_ANNOTATION])
        }
        assert recorded[str(new.id)] == "launch-2"
        assert recorded[str(launches[1].session.id)] == "launch-1"

    async def test_a_restart_with_the_deployment_gone_is_an_error(self, manager) -> None:
        import httpx

        root, launches, spec = _unit()
        member = launches[0].session
        manager.configure_hosted_members(
            lambda unit_id, member_id: _answer([Launch(member, launches[0].spec)])
        )
        self._wired(manager, lambda request: httpx.Response(200, json={"status": "ok"}))
        with (
            patch.object(manager, "_ensure_client", AsyncMock()),
            patch.object(manager, "_read_deployment", AsyncMock(return_value=None)),
            pytest.raises(HostedUnitError, match="is gone"),
        ):
            await manager.restart_hosted_member(member)


async def _answer(launches):
    return launches
