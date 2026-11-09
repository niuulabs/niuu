"""A workflow run on Flux is one unit: one HelmRelease whose pod hosts every member."""

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import yaml

from volundr.adapters.outbound.flux import FluxPodManager
from volundr.domain.models import (
    HOSTED_MEMBERS_KEY,
    HOSTED_UNIT_KEY,
    GitSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)

CHART_DIR = Path(__file__).parent.parent.parent / "charts" / "skuld"
CREDENTIAL_PATH = "/var/run/secrets/niuu-forge-session"
GIT = {"repoUrl": "https://github.com/org/repo.git", "branch": "feat/run"}


@dataclass
class Launch:
    """A member as Forge hands it to the pod manager (HostedMemberLaunch)."""

    session: Session
    spec: SessionSpec


def _root(members: list[Session] | None = None, *, recorded: bool = True) -> Session:
    root_id = uuid4()
    config = {HOSTED_UNIT_KEY: {}}
    if recorded:
        config[HOSTED_MEMBERS_KEY] = [
            {"id": str(m.id), "port": m.hosted_port} for m in members or []
        ]
    return Session(
        id=root_id,
        name="wf-run",
        model="claude-sonnet",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        workload_config=config,
    )


def _member(root: Session, name: str, port: int, **update) -> Session:
    return Session(
        id=uuid4(),
        name=name,
        model="claude-sonnet",
        source=GitSource(repo="https://github.com/org/repo", branch="main"),
        hosted_by=root.id,
        hosted_port=port,
        **update,
    )


def _member_spec(member: Session, **extra_values) -> SessionSpec:
    """What the contributor pipeline gives a member, with its credential Secret mounted."""
    volume = f"forge-session-{member.id.hex[:12]}"
    values = {
        "session": {"id": str(member.id), "name": member.name, "ownerId": "u1", "tenantId": "t1"},
        "mesh": {
            "enabled": True,
            "realmId": "run-1",
            "nng": {"pubSubAddress": "tcp://0.0.0.0:7480", "advertiseHost": "10.0.0.1"},
        },
        "forgeMcp": {"tokenFile": f"{CREDENTIAL_PATH}/token"},
        **extra_values,
    }
    return SessionSpec(
        values=values,
        pod_spec=PodSpecAdditions(
            volumes=({"name": volume, "secret": {"secretName": f"forge-session-{member.id.hex}"}},),
            volume_mounts=({"name": volume, "mountPath": CREDENTIAL_PATH, "readOnly": True},),
            env=({"name": "MEMBER", "value": member.name},),
        ),
    )


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
            "session": {"id": str(root.id), "name": root.name, "ownerId": "u1", "tenantId": "t1"},
            "mesh": {"enabled": True, "realmId": "run-1"},
            **root_values,
        },
        pod_spec=PodSpecAdditions(),
    )
    spec.members = tuple(launches)
    return root, launches, spec


@pytest.fixture
def manager() -> FluxPodManager:
    return FluxPodManager(
        namespace="ns",
        base_domain="volundr.example.com",
        gateway_domain="gw.example.com",
        session_defaults={
            "image": {"repository": "ghcr.io/niuulabs/skuld", "tag": "t"},
            "ingress": {"enabled": False},
            "gateway": {"enabled": True, "name": "gw", "namespace": "gw-ns"},
        },
    )


@pytest.fixture
def api() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def core(monkeypatch) -> AsyncMock:
    core = AsyncMock()
    monkeypatch.setattr(FluxPodManager, "_get_core_api", AsyncMock(return_value=core))
    return core


async def _start(manager: FluxPodManager, api: AsyncMock, root: Session, spec: SessionSpec) -> dict:
    with patch.object(manager, "_get_api", return_value=api):
        await manager.start(root, spec)
    return api.create_namespaced_custom_object.call_args[1]["body"]["spec"]["values"]


class TestUnitRelease:
    async def test_the_release_lists_every_member_with_its_port_and_own_values(self, manager, api):
        root, launches, spec = _unit()

        values = await _start(manager, api, root, spec)

        assert [(m["id"], m["port"]) for m in values["members"]] == [
            (str(launches[0].session.id), 8082),
            (str(launches[1].session.id), 8083),
        ]
        assert [m["values"]["session"]["name"] for m in values["members"]] == [
            "coder",
            "reviewer",
        ]
        # A member's values carry the deployment's defaults beneath its own spec.
        assert values["members"][0]["values"]["image"]["repository"] == "ghcr.io/niuulabs/skuld"
        assert {"name": "MEMBER", "value": "coder"} in values["members"][0]["values"]["envVars"]
        assert "members" not in values["members"][0]["values"]

    async def test_a_members_credential_is_mounted_into_its_own_container_only(self, manager, api):
        root, launches, spec = _unit()

        values = await _start(manager, api, root, spec)

        volumes = {v["name"]: v for v in values["extraVolumes"]}
        assert set(volumes) == {
            f"member-0-forge-session-{launches[0].session.id.hex[:12]}",
            f"member-1-forge-session-{launches[1].session.id.hex[:12]}",
        }
        for index, launch in enumerate(launches):
            member = values["members"][index]["values"]
            (mount,) = member["extraVolumeMounts"]
            assert mount["name"] == f"member-{index}-forge-session-{launch.session.id.hex[:12]}"
            assert volumes[mount["name"]]["secret"]["secretName"] == (
                f"forge-session-{launch.session.id.hex}"
            )
            assert "extraVolumes" not in member
        assert "extraVolumeMounts" not in values

    async def test_every_skuld_of_the_pod_uses_the_pods_own_mesh(self, manager, api):
        root, _, spec = _unit()

        values = await _start(manager, api, root, spec)

        registry = [{"adapter": "registry", "path": "/tmp/niuu-mesh/peers.json"}]
        assert values["unit"]["meshDir"] == "/tmp/niuu-mesh"
        meshes = [values["mesh"], *[m["values"]["mesh"] for m in values["members"]]]
        for mesh in meshes:
            assert mesh["discoveryAdapters"] == registry
        for member in values["members"]:
            nng = member["values"]["mesh"]["nng"]
            assert nng["pubSubAddress"] == nng["reqRepAddress"] == "tcp://127.0.0.1:0"
            assert nng["advertiseHost"] == ""

    async def test_the_mesh_directory_is_configurable(self, api):
        manager = FluxPodManager(namespace="ns", unit_mesh_dir="/run/mesh/")
        root, _, spec = _unit()

        values = await _start(manager, api, root, spec)

        assert values["unit"]["meshDir"] == "/run/mesh"
        assert values["mesh"]["discoveryAdapters"][0]["path"] == "/run/mesh/peers.json"

    def test_the_mesh_directory_must_be_absolute(self):
        with pytest.raises(ValueError, match="absolute"):
            FluxPodManager(namespace="ns", unit_mesh_dir="mesh")

    async def test_a_mesh_that_is_off_or_not_nng_is_left_alone(self, manager, api):
        root, launches, spec = _unit(mesh={"enabled": False})
        launches[0].spec.values["mesh"] = {"enabled": True, "transport": "nats"}

        values = await _start(manager, api, root, spec)

        assert "discoveryAdapters" not in values["mesh"]
        assert "discoveryAdapters" not in values["members"][0]["values"]["mesh"]
        assert values["members"][1]["values"]["mesh"]["discoveryAdapters"]

    async def test_the_checkout_is_cloned_from_the_member_that_names_a_repository(
        self, manager, api
    ):
        root, launches, spec = _unit()
        for launch in launches:
            launch.spec.values["git"] = dict(GIT)

        values = await _start(manager, api, root, spec)

        assert values["unit"]["checkoutFrom"] == str(launches[0].session.id)

    async def test_a_root_with_its_own_repository_needs_no_member_to_clone(self, manager, api):
        root, launches, spec = _unit(git=dict(GIT))
        launches[1].spec.values["git"] = dict(GIT)

        values = await _start(manager, api, root, spec)

        assert "checkoutFrom" not in values["unit"]

    async def test_members_on_different_branches_are_refused(self, manager, api):
        root, launches, spec = _unit()
        launches[0].spec.values["git"] = dict(GIT)
        launches[1].spec.values["git"] = {**GIT, "branch": "other"}

        with pytest.raises(ValueError, match="share one checkout"):
            await _start(manager, api, root, spec)

        api.create_namespaced_custom_object.assert_not_called()

    async def test_a_member_on_another_branch_than_the_roots_is_refused(self, manager, api):
        root, launches, spec = _unit(git=dict(GIT))
        launches[0].spec.values["git"] = {**GIT, "branch": "other"}

        with pytest.raises(ValueError, match="share a repository and branch"):
            await _start(manager, api, root, spec)

    async def test_the_response_is_the_roots_own_address(self, manager, api):
        root, _, spec = _unit()

        with patch.object(manager, "_get_api", return_value=api):
            result = await manager.start(root, spec)

        assert result.pod_name == f"skuld-{root.id}"
        assert result.chat_endpoint == f"wss://gw.example.com/s/{root.id}/session"

    async def test_a_session_without_members_is_unchanged(self, manager, api):
        session = Session(
            id=uuid4(),
            name="solo",
            model="m",
            source=GitSource(repo="https://github.com/org/repo", branch="main"),
        )

        values = await _start(
            manager, api, session, SessionSpec(values={"session": {}}, pod_spec=PodSpecAdditions())
        )

        assert "members" not in values
        assert "unit" not in values


class TestUnitRefusals:
    async def test_recorded_members_that_did_not_arrive_are_refused(self, manager, api):
        root, _, spec = _unit()
        spec.members = ()

        with pytest.raises(ValueError, match="no launch for them reached"):
            await _start(manager, api, root, spec)

        api.create_namespaced_custom_object.assert_not_called()

    async def test_members_that_differ_from_the_recorded_ones_are_refused(self, manager, api):
        root, launches, spec = _unit()
        spec.members = (launches[0],)

        with pytest.raises(ValueError, match="fixed when its unit starts"):
            await _start(manager, api, root, spec)

    async def test_members_of_a_session_that_is_not_a_unit_root_are_refused(self, manager, api):
        root, _, spec = _unit()
        plain = root.model_copy(update={"workload_config": {}})

        with pytest.raises(ValueError, match="not a unit root"):
            await _start(manager, api, plain, spec)

    async def test_a_member_of_another_unit_is_refused(self, manager, api):
        root, launches, spec = _unit()
        stranger = launches[0].session.model_copy(update={"hosted_by": uuid4()})
        spec.members = (Launch(stranger, launches[0].spec), launches[1])

        with pytest.raises(ValueError, match="is hosted by"):
            await _start(manager, api, root, spec)

    async def test_two_members_on_one_port_are_refused(self, manager, api):
        root, launches, spec = _unit()
        twin = launches[1].session.model_copy(update={"hosted_port": 8082})
        root = root.model_copy(
            update={
                "workload_config": {
                    HOSTED_UNIT_KEY: {},
                    HOSTED_MEMBERS_KEY: [
                        {"id": str(launches[0].session.id), "port": 8082},
                        {"id": str(twin.id), "port": 8082},
                    ],
                }
            }
        )
        spec.members = (launches[0], Launch(twin, launches[1].spec))

        with pytest.raises(ValueError, match="same broker port"):
            await _start(manager, api, root, spec)

    async def test_a_member_launched_twice_is_refused(self, manager, api):
        root, launches, spec = _unit()
        spec.members = (launches[0], launches[0], launches[1])

        with pytest.raises(ValueError, match="launched twice"):
            await _start(manager, api, root, spec)

    async def test_a_member_without_a_port_is_refused(self, manager, api):
        root, launches, spec = _unit()
        spec.members = (
            Launch(launches[0].session.model_copy(update={"hosted_port": None}), launches[0].spec),
            launches[1],
        )

        with pytest.raises(ValueError, match="no broker port"):
            await _start(manager, api, root, spec)

    @pytest.mark.parametrize(
        ("field", "extra"),
        [
            ("init_containers", {"init_containers": ({"name": "x"},)}),
            ("extra_containers", {"extra_containers": ({"name": "y"},)}),
            ("service_account", {"service_account": "elevated"}),
        ],
    )
    async def test_a_member_cannot_add_pod_level_parts(self, manager, api, field, extra):
        root, launches, spec = _unit()
        pod_spec = launches[0].spec.pod_spec
        launches[0].spec.pod_spec = PodSpecAdditions(
            volumes=pod_spec.volumes, volume_mounts=pod_spec.volume_mounts, **extra
        )

        with pytest.raises(ValueError, match=field):
            await _start(manager, api, root, spec)

    async def test_a_member_launched_for_another_node_pool_is_refused(self, manager, api):
        root, launches, spec = _unit(nodeSelector={"pool": "a"})
        launches[0].spec.values["nodeSelector"] = {"pool": "b"}

        with pytest.raises(ValueError, match="nodeSelector"):
            await _start(manager, api, root, spec)

    async def test_a_member_with_pod_labels_the_pod_lacks_is_refused(self, manager, api):
        root, launches, spec = _unit()
        launches[0].spec.pod_spec = PodSpecAdditions(
            volumes=launches[0].spec.pod_spec.volumes,
            volume_mounts=launches[0].spec.pod_spec.volume_mounts,
            labels={"vault": "yes"},
        )

        with pytest.raises(ValueError, match="podLabels"):
            await _start(manager, api, root, spec)

    async def test_a_member_cannot_be_started_on_its_own(self, manager, api):
        root, launches, spec = _unit()

        with pytest.raises(ValueError, match="starts with it"):
            await manager.start(launches[0].session, spec)


def _release(root: Session, members: list[Session], *, ready: bool = True) -> dict:
    return {
        "spec": {
            "values": {"members": [{"id": str(m.id), "port": m.hosted_port} for m in members]}
        },
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def _pod(name: str, state: SimpleNamespace | None):
    statuses = [] if state is None else [SimpleNamespace(name=name, state=state)]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name="pod-1",
            deletion_timestamp=None,
            labels={"pod-template-hash": "h"},
            owner_references=[],
            creation_timestamp=0,
        ),
        status=SimpleNamespace(container_statuses=statuses),
    )


def _state(*, running=False, waiting=None, terminated=None):
    return SimpleNamespace(
        running=SimpleNamespace() if running else None,
        waiting=SimpleNamespace(reason=waiting, message="boom") if waiting else None,
        terminated=SimpleNamespace(exit_code=terminated, reason="Error", message="bad")
        if terminated is not None
        else None,
    )


class TestMemberStatus:
    @pytest.fixture
    def unit(self, manager, monkeypatch):
        root, launches, _ = _unit()
        members = [launch.session for launch in launches]
        monkeypatch.setattr(
            manager, "_get_helmrelease", AsyncMock(return_value=_release(root, members))
        )
        return root, members

    def _pod_is(self, manager, monkeypatch, pod):
        monkeypatch.setattr(manager, "_session_pod", AsyncMock(return_value=pod))

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (_state(running=True), SessionStatus.RUNNING),
            (_state(waiting="ContainerCreating"), SessionStatus.STARTING),
            (_state(waiting="CrashLoopBackOff"), SessionStatus.FAILED),
            (_state(waiting="ImagePullBackOff"), SessionStatus.FAILED),
            (_state(terminated=0), SessionStatus.STOPPED),
            (_state(terminated=137), SessionStatus.FAILED),
        ],
    )
    async def test_a_member_answers_from_its_own_container(
        self, manager, monkeypatch, core, unit, state, expected
    ):
        _, members = unit
        self._pod_is(manager, monkeypatch, _pod("member-1", state))

        assert await manager.status(members[1]) == expected

    async def test_a_live_member_is_not_reaped_by_a_sweep_while_a_dead_one_is_found(
        self, manager, monkeypatch, core, unit
    ):
        _, members = unit
        pod = _pod("member-0", _state(running=True))
        pod.status.container_statuses.append(
            SimpleNamespace(name="member-1", state=_state(terminated=1))
        )
        self._pod_is(manager, monkeypatch, pod)

        assert await manager.status(members[0]) == SessionStatus.RUNNING
        assert await manager.status(members[1]) == SessionStatus.FAILED

    async def test_a_container_the_kubelet_has_not_reported_is_starting(
        self, manager, monkeypatch, core, unit
    ):
        _, members = unit
        self._pod_is(manager, monkeypatch, _pod("member-0", None))

        assert await manager.status(members[0]) == SessionStatus.STARTING

    async def test_no_pod_yet_is_starting(self, manager, monkeypatch, core, unit):
        _, members = unit
        self._pod_is(manager, monkeypatch, None)

        assert await manager.status(members[0]) == SessionStatus.STARTING

    async def test_a_member_follows_a_unit_that_is_not_ready(self, manager, monkeypatch, core):
        root, launches, _ = _unit()
        members = [launch.session for launch in launches]
        monkeypatch.setattr(
            manager,
            "_get_helmrelease",
            AsyncMock(return_value=_release(root, members, ready=False)),
        )

        assert await manager.status(members[0]) == SessionStatus.STARTING

    async def test_a_member_the_unit_was_not_started_with_is_not_running(
        self, manager, monkeypatch, core, unit
    ):
        root, _ = unit
        stranger = _member(root, "late", 8099)

        assert await manager.status(stranger) == SessionStatus.STOPPED

    async def test_a_member_of_a_unit_that_is_gone_is_stopped(self, manager, monkeypatch, core):
        root, launches, _ = _unit()
        monkeypatch.setattr(manager, "_get_helmrelease", AsyncMock(return_value=None))

        assert await manager.status(launches[0].session) == SessionStatus.STOPPED

    async def test_why_a_member_is_not_running(self, manager, monkeypatch, core, unit):
        _, members = unit
        self._pod_is(manager, monkeypatch, _pod("member-0", _state(waiting="CrashLoopBackOff")))

        detail = await manager.status_detail(members[0])

        assert detail is not None
        assert "member-0" in detail and "CrashLoopBackOff" in detail
        self._pod_is(manager, monkeypatch, _pod("member-0", _state(running=True)))
        assert await manager.status_detail(members[0]) is None

    async def test_a_member_with_no_pod_yet_has_nothing_to_explain(
        self, manager, monkeypatch, core, unit
    ):
        _, members = unit
        self._pod_is(manager, monkeypatch, None)

        assert await manager.status_detail(members[0]) is None

    async def test_a_member_unknown_to_its_unit_says_so(self, manager, monkeypatch, core, unit):
        root, _ = unit

        detail = await manager.status_detail(_member(root, "late", 8099))

        assert detail is not None and "not one of the members" in detail


class TestMemberLifecycle:
    async def test_stopping_a_member_never_deletes_its_units_release(self, manager, api):
        root, launches, _ = _unit()

        with patch.object(manager, "_get_api", return_value=api):
            assert await manager.stop(launches[0].session) is False
            assert await manager.destroy(launches[0].session) is False

        api.delete_namespaced_custom_object.assert_not_called()

    async def test_stopping_the_root_deletes_the_unit(self, manager, api):
        root, _, _ = _unit()

        with patch.object(manager, "_get_api", return_value=api):
            await manager.stop(root)

        assert api.delete_namespaced_custom_object.call_args[1]["name"] == f"skuld-{root.id}"

    async def test_forge_reaches_a_member_on_its_path_of_the_units_service(self, manager):
        root, launches, _ = _unit()
        member = launches[0].session.model_copy(update={"status": SessionStatus.RUNNING})

        target = await manager.broker_target(member)

        host = f"skuld-{root.id}.ns.svc.cluster.local"
        assert target is not None
        assert target.service_url == f"http://{host}:8080/m/{member.id}/"
        assert (target.connect_host, target.connect_port) == (host, 8080)
        assert manager.session_proxy_target(member) == target

    async def test_a_member_whose_unit_is_not_running_has_no_broker(self, manager):
        _, launches, _ = _unit()
        member = launches[0].session.model_copy(update={"status": SessionStatus.STARTING})

        with pytest.raises(ValueError, match="not running"):
            await manager.broker_target(member)

    async def test_an_ordinary_session_is_reached_at_its_chat_endpoint(self, manager):
        root, _, _ = _unit()

        assert await manager.broker_target(root) is None

    async def test_a_member_has_its_own_gateway_address(self, manager):
        _, launches, _ = _unit()
        member = launches[0].session

        assert (
            manager.initial_chat_endpoint(member) == f"wss://gw.example.com/s/{member.id}/session"
        )


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required")
class TestAdapterAndChartAgree:
    """What the pod manager writes into the HelmRelease is what the chart renders."""

    async def test_the_release_renders_a_pod_with_every_member_started_together(
        self, manager, api, tmp_path
    ):
        # The sessions volume is the deployment's, so root and members share it by default.
        manager = FluxPodManager(
            namespace="ns",
            gateway_domain="gw.example.com",
            session_defaults={
                "ingress": {"enabled": False},
                "gateway": {"enabled": True, "name": "gw", "namespace": "gw-ns"},
                "persistence": {
                    "enabled": True,
                    "emptyDir": True,
                    "mountPath": "/volundr/sessions",
                },
            },
        )
        root, launches, spec = _unit()
        for launch in launches:
            launch.spec.values["git"] = dict(GIT)
        values = await _start(manager, api, root, spec)
        values_file = tmp_path / "values.yaml"
        values_file.write_text(yaml.safe_dump(values))

        output = subprocess.check_output(
            ["helm", "template", f"skuld-{root.id}", str(CHART_DIR), "-f", str(values_file)],
            stderr=subprocess.PIPE,
            text=True,
        )
        docs = [d for d in yaml.safe_load_all(output) if d]

        deployment = next(d for d in docs if d["kind"] == "Deployment")
        pod = deployment["spec"]["template"]["spec"]
        containers = {c["name"]: c for c in pod["containers"]}
        assert {"skuld", "member-0", "member-1"} <= set(containers)
        ids = [launch.session.id for launch in launches]
        for index, member_id in enumerate(ids):
            container = containers[f"member-{index}"]
            mounts = {m["mountPath"]: m for m in container["volumeMounts"]}
            assert mounts["/etc/skuld/config.yaml"]["subPath"] == f"{member_id}.yaml"
            assert mounts[CREDENTIAL_PATH]["name"].startswith(f"member-{index}-forge-session-")
            assert "/tmp/niuu-mesh" in mounts
        assert CREDENTIAL_PATH not in {m["mountPath"] for m in containers["skuld"]["volumeMounts"]}
        volumes = {v["name"] for v in pod["volumes"]}
        assert {"mesh", "skuld-members"} <= volumes
        clone = next(c for c in pod["initContainers"] if c["name"] == "git-clone")
        assert f'WORKSPACE="/volundr/sessions/{root.id}/workspace"' in clone["args"][0]
        config = next(
            d
            for d in docs
            if d["kind"] == "ConfigMap" and d["metadata"]["name"].endswith("-members")
        )
        for member_id in ids:
            member_config = yaml.safe_load(config["data"][f"{member_id}.yaml"])
            assert member_config["session"]["id"] == str(member_id)
            assert member_config["mesh"]["discovery_adapters"][0]["adapter"] == "registry"
        routes = {d["metadata"]["name"] for d in docs if d["kind"] == "HTTPRoute"}
        assert routes == {f"session-{root.id}", *(f"session-{member_id}" for member_id in ids)}
