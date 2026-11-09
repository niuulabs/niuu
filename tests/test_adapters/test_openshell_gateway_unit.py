"""OpenShellGatewayPodManager hosting a workflow run: one sandbox, a peer workload per member."""

from __future__ import annotations

import io
import json
import shlex
import tarfile
from uuid import uuid4

import pytest

from tests.support.forge_session import token_service
from tests.test_adapters.test_openshell_gateway import (
    _FakeOpenShellGatewayClient,
    _import_adapter,
)
from volundr.adapters.outbound.openshell_contract import MAX_PEER_WORKLOADS
from volundr.adapters.outbound.vm_hosted_unit import HostedMemberLaunch, HostedUnitError
from volundr.domain.models import HOSTED_MEMBERS_KEY as UNIT_MEMBERS_KEY
from volundr.domain.models import (
    GitSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.services.forge_session_launch import with_forge_mcp

VALUES = {
    "persistence": {"existingClaim": "workspace", "mountPath": "/sandbox/workspace"},
    "session": {"systemPrompt": "root prompt"},
}


def _config(launch: str) -> dict:
    return with_forge_mcp({}, grants=[], launch_id=launch)


def _member(root_id, index: int, *, launch: str = "launch-1") -> Session:
    return Session(
        id=uuid4(),
        name=f"member-{index}",
        owner_id="owner-1",
        tenant_id="tenant-1",
        hosted_by=root_id,
        hosted_port=8082 + index,
        source=GitSource(repo="https://github.com/niuulabs/volundr", branch="dev"),
        workload_config=_config(launch),
    )


def _member_spec(session: Session, **session_values) -> SessionSpec:
    credential = token_service().mint(
        session_id=session.id,
        session_name=session.name,
        owner_id="owner-1",
        tenant_id=None,
        grants=(),
        launch_id="launch-1",
    )
    spec = SessionSpec(
        values={
            "session": {"systemPrompt": f"you are {session.name}\nsecond line", **session_values},
            "persistence": VALUES["persistence"],
        },
        pod_spec=PodSpecAdditions(),
    )
    spec.forge_session = credential
    return spec


class UnitFixture:
    def __init__(self, adapter, count: int = 2) -> None:
        self.adapter = adapter
        self.client = _FakeOpenShellGatewayClient(adapter)
        self.manager = adapter.OpenShellGatewayPodManager(
            client=self.client, mesh={"enabled": True}
        )
        self.root = Session(
            id=uuid4(),
            name="wf-run",
            owner_id="owner-1",
            tenant_id="tenant-1",
            source=GitSource(repo="https://github.com/niuulabs/volundr", branch="dev"),
        )
        self.members = [_member(self.root.id, i) for i in range(count)]
        self.root = self.root.model_copy(
            update={
                "workload_config": {
                    **_config("root-launch"),
                    UNIT_MEMBERS_KEY: [
                        {"id": str(m.id), "port": m.hosted_port} for m in self.members
                    ],
                }
            }
        )
        self.launches = [HostedMemberLaunch(session=m, spec=_member_spec(m)) for m in self.members]

        async def source(unit_id, only):
            return [m for m in self.launches if only is None or m.session.id == only]

        self.manager.configure_hosted_members(source)

    def spec(self) -> SessionSpec:
        return SessionSpec(values=json.loads(json.dumps(VALUES)), pod_spec=PodSpecAdditions())

    async def start(self):
        return await self.manager.start(self.root, self.spec())

    @property
    def workloads(self) -> list[dict]:
        return self.client.created["driver_config"]["containers"]["workloads"]

    @property
    def files(self) -> dict[str, bytes]:
        merged: dict[str, bytes] = {}
        for call in self.client.written_files:
            merged.update(call["files"])
        return merged


@pytest.fixture
def unit(monkeypatch: pytest.MonkeyPatch) -> UnitFixture:
    return UnitFixture(_import_adapter(monkeypatch))


class TestStartUnit:
    async def test_each_member_is_a_peer_workload_of_the_roots_sandbox(self, unit) -> None:
        await unit.start()

        assert [w["name"] for w in unit.workloads] == ["member-0", "member-1"]
        assert {w["image"] for w in unit.workloads} == {unit.manager._sandbox_image}
        assert len(unit.client.execs) == 1  # only the root's Skuld is started by exec
        assert unit.client.created["name"] == f"forge-{unit.root.id.hex[:13]}"

    async def test_mesh_is_one_shared_directory_and_members_get_their_own(self, unit) -> None:
        await unit.start()

        config = unit.client.created["driver_config"]
        names = [v["name"] for v in config["volumes"]]
        assert names[-3:] == ["niuu-mesh", "niuu-member-0", "niuu-member-1"]
        assert all(v.get("empty_dir") == {} for v in config["volumes"][-3:])
        for index, workload in enumerate(unit.workloads):
            mounts = {m["mount_path"] for m in workload["volume_mounts"]}
            assert "/tmp/niuu-mesh" in mounts
            assert f"/tmp/niuu-members/{index}" in mounts
            other = f"/tmp/niuu-members/{1 - index}"
            assert other not in mounts
        agent = {m["mount_path"] for m in config["containers"]["agent"]["volume_mounts"]}
        assert {"/tmp/niuu-mesh", "/tmp/niuu-members/0", "/tmp/niuu-members/1"} <= agent

    async def test_every_skuld_discovers_peers_in_the_shared_registry(self, unit) -> None:
        await unit.start()

        root_env = unit.client.created["env"]
        assert root_env["SKULD__MESH__ENABLED"] == "true"
        assert json.loads(root_env["SKULD__MESH__DISCOVERY_ADAPTERS"]) == [
            {"adapter": "registry", "path": "/tmp/niuu-mesh/peers.json"}
        ]
        for index in range(2):
            env_file = unit.files[f"/tmp/niuu-members/{index}/env"].decode()
            assert "SKULD__MESH__DISCOVERY_ADAPTERS=" in env_file
            assert "/tmp/niuu-mesh/peers.json" in env_file

    async def test_each_member_has_its_own_credential_file_and_port(self, unit) -> None:
        await unit.start()

        for index, launch in enumerate(unit.launches):
            token = unit.files[f"/tmp/niuu-members/{index}/token"]
            assert token.decode() == launch.spec.forge_session.token
            env_file = unit.files[f"/tmp/niuu-members/{index}/env"].decode()
            assert f"SKULD__FORGE_MCP__TOKEN_FILE=/tmp/niuu-members/{index}/token" in env_file
            assert f"SKULD__PORT={8082 + index}" in env_file
            assert f"SKULD__SESSION__ID={launch.session.id}" in env_file
            assert launch.spec.forge_session.token not in env_file
        tokens = {unit.files[f"/tmp/niuu-members/{i}/token"] for i in range(2)}
        assert len(tokens) == 2
        # No token travels in the pod spec.
        assert all("token" not in json.dumps(w).lower() for w in unit.workloads)

    async def test_each_member_keeps_its_own_home_and_shares_the_workspace(self, unit) -> None:
        await unit.start()

        env_file = unit.files["/tmp/niuu-members/1/env"].decode()
        assert "HOME=/tmp/niuu-members/1/home" in env_file
        assert "CODEX_HOME=/tmp/niuu-members/1/home/.codex" in env_file
        assert "SKULD__SESSION__WORKSPACE_DIR=/sandbox/workspace" in env_file
        assert unit.workloads[0]["volume_mounts"][0]["mount_path"] == "/sandbox/workspace"

    async def test_prompts_with_newlines_travel_in_the_file_not_the_pod(self, unit) -> None:
        await unit.start()

        env_file = unit.files["/tmp/niuu-members/0/env"].decode()
        quoted = shlex.quote("you are member-0\nsecond line")
        assert f"SKULD__SESSION__SYSTEM_PROMPT={quoted}" in env_file
        assert all("second line" not in json.dumps(w) for w in unit.workloads)
        # The file is a valid shell file that sets exactly what it says.
        assert shlex.split(env_file.splitlines()[-1].split("=", 1)[1])

    async def test_root_identity_inherited_by_peers_is_unset_by_the_member(self, unit) -> None:
        await unit.start()

        env_file = unit.files["/tmp/niuu-members/0/env"].decode()
        lines = env_file.splitlines()
        # The member sets its own id, so the root's is overridden, not unset.
        assert f"SKULD__SESSION__ID={unit.members[0].id}" in lines
        assert not any(line.startswith("unset SKULD__SESSION__ID") for line in lines)
        # Whatever the root has and the member does not (here the root's model) is unset
        # before anything is set.
        unsets = [i for i, line in enumerate(lines) if line.startswith("unset ")]
        sets = [i for i, line in enumerate(lines) if "=" in line and not line.startswith("unset")]
        assert all(u < min(sets) for u in unsets)

    async def test_peer_command_sources_the_file_then_runs_skuld(self, unit) -> None:
        await unit.start()

        command = unit.workloads[0]["command"]
        # The adapter wraps the command so the peer waits for the start marker.
        assert command[:2] == ["python", "-c"]
        assert command[5:7] == ["sh", "-c"]
        assert command[8:10] == ["niuu-member", "/tmp/niuu-members/0/env"]
        assert command[10:] == unit.manager._sandbox_command

    async def test_credentials_are_written_before_the_peers_are_released(self, unit) -> None:
        await unit.start()

        first, last = unit.client.written_files[0], unit.client.written_files[-1]
        assert "/tmp/niuu-members/0/token" in first["files"]
        marker = unit.workloads[0]["command"][3]
        assert list(last["files"])[-1] == marker

    async def test_state_for_the_unit_is_remembered(self, unit) -> None:
        await unit.start()

        root_id = str(unit.root.id)
        assert unit.manager._unit_sandboxes[root_id] == f"forge-{unit.root.id.hex[:13]}"
        assert unit.manager._member_launches == {str(m.id): "launch-1" for m in unit.members}

    async def test_member_resources_are_the_peers(self, unit) -> None:
        for launch in unit.launches:
            launch.spec.values["resources"] = {
                "requests": {"cpu": "1"},
                "limits": {"memory": "2Gi"},
            }

        await unit.start()

        assert unit.workloads[0]["resources"] == {
            "requests": {"cpu": "1"},
            "limits": {"memory": "2Gi"},
        }


class TestRefusals:
    async def test_more_members_than_the_sandbox_admits_is_refused_with_the_reason(
        self, monkeypatch
    ) -> None:
        unit = UnitFixture(_import_adapter(monkeypatch), count=MAX_PEER_WORKLOADS + 1)

        with pytest.raises(ValueError) as error:
            await unit.start()

        message = str(error.value)
        assert f"{MAX_PEER_WORKLOADS + 1} member sessions" in message
        assert f"at most {MAX_PEER_WORKLOADS} peer workloads per sandbox" in message
        assert unit.client.created is None
        assert unit.manager._progress == {}

    async def test_exactly_the_limit_fits(self, monkeypatch) -> None:
        unit = UnitFixture(_import_adapter(monkeypatch), count=MAX_PEER_WORKLOADS)

        await unit.start()

        assert len(unit.workloads) == MAX_PEER_WORKLOADS

    async def test_no_member_source_is_refused(self, unit) -> None:
        unit.manager._hosted_members = None

        with pytest.raises(HostedUnitError, match="no member source"):
            await unit.start()
        assert unit.client.created is None

    async def test_members_that_differ_from_the_recorded_ones_are_refused(self, unit) -> None:
        stranger = _member(unit.root.id, 5)
        unit.launches.append(HostedMemberLaunch(session=stranger, spec=_member_spec(stranger)))

        with pytest.raises(HostedUnitError, match="fixed when its unit starts"):
            await unit.start()

    async def test_a_member_hosted_by_another_unit_is_refused(self, unit) -> None:
        unit.launches[0] = HostedMemberLaunch(
            session=unit.members[0].model_copy(update={"hosted_by": uuid4()}),
            spec=unit.launches[0].spec,
        )

        with pytest.raises(HostedUnitError, match="is hosted by"):
            await unit.start()

    async def test_two_members_on_one_port_are_refused(self, unit) -> None:
        unit.launches[1] = HostedMemberLaunch(
            session=unit.members[1].model_copy(update={"hosted_port": 8082}),
            spec=unit.launches[1].spec,
        )

        with pytest.raises(HostedUnitError, match="same port"):
            await unit.start()

    async def test_a_member_on_the_roots_broker_port_is_refused(self, unit) -> None:
        unit.launches[0] = HostedMemberLaunch(
            session=unit.members[0].model_copy(update={"hosted_port": unit.manager._service_port}),
            spec=unit.launches[0].spec,
        )

        with pytest.raises(HostedUnitError, match="same port"):
            await unit.start()

    async def test_mesh_off_is_refused(self, unit) -> None:
        unit.manager._mesh = {}

        with pytest.raises(HostedUnitError, match="mesh.enabled: true"):
            await unit.start()
        assert unit.client.created is None

    async def test_no_shared_workspace_volume_is_refused(self, unit) -> None:
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        with pytest.raises(ValueError, match="workspace volume"):
            await unit.manager.start(unit.root, spec)
        assert unit.client.created is None

    async def test_peer_workloads_declared_by_the_root_are_refused(self, unit) -> None:
        spec = unit.spec()
        spec.values["openshell"] = {"workloads": [{"name": "x"}]}

        with pytest.raises(ValueError, match="peer workloads of its own"):
            await unit.manager.start(unit.root, spec)

    async def test_a_member_cannot_start_alone(self, unit) -> None:
        with pytest.raises(HostedUnitError, match="starts with its unit"):
            await unit.manager.start(unit.members[0], unit.spec())

    async def test_an_egress_a_member_needs_that_the_policy_denies_is_refused(self, unit) -> None:
        unit.launches[1].spec.values["openshell"] = {
            "egress": [
                {"host": "api.example.com", "port": 443, "purpose": "x", "passthrough": False}
            ]
        }

        with pytest.raises(ValueError, match="does not allow"):
            await unit.start()
        assert unit.client.created is None

    async def test_a_member_that_materializes_its_environment_is_refused(
        self, unit, monkeypatch
    ) -> None:
        async def context(_session, _spec):
            return unit.adapter.OpenShellCredentialContext(
                files={}, providers=(), environment={}, process_environment={"A": "b"}
            )

        monkeypatch.setattr(unit.manager, "_resolve_credential_env", context)
        original = unit.manager._resolve_credential_env

        async def root_ok(session, spec):
            if session.id == unit.root.id:
                return unit.adapter.OpenShellCredentialContext(
                    files={}, providers=(), environment={}, process_environment={}
                )
            return await original(session, spec)

        monkeypatch.setattr(unit.manager, "_resolve_credential_env", root_ok)

        with pytest.raises(ValueError, match="materializeEnvironment"):
            await unit.start()
        assert unit.client.created is None


class TestMemberStatus:
    async def test_a_member_reads_as_its_units_sandbox(self, unit) -> None:
        await unit.start()

        for member in unit.members:
            assert await unit.manager.status(member) is SessionStatus.RUNNING

    async def test_a_member_of_a_gone_sandbox_is_stopped_not_reaped_while_running(
        self, unit
    ) -> None:
        await unit.start()
        unit.client.sandbox_exists = False

        assert await unit.manager.status(unit.members[0]) is SessionStatus.STOPPED

    async def test_the_members_sandbox_is_found_after_a_forge_restart(self, unit) -> None:
        # Nothing in memory: the sandbox name derives from the unit's id.
        assert await unit.manager.status(unit.members[0]) is SessionStatus.RUNNING
        assert unit.manager._sandbox_of(unit.members[0]) == f"forge-{unit.root.id.hex[:13]}"

    async def test_a_member_started_again_cannot_run_under_its_new_launch(self, unit) -> None:
        await unit.start()
        relaunched = unit.members[0].model_copy(update={"workload_config": _config("launch-2")})

        assert await unit.manager.status(relaunched) is SessionStatus.FAILED
        detail = await unit.manager.status_detail(relaunched)
        assert "cannot restart one peer workload" in detail
        assert "stop the run and start it again" in detail
        assert await unit.manager.status(unit.members[1]) is SessionStatus.RUNNING
        assert await unit.manager.status_detail(unit.members[1]) is None

    async def test_wait_for_ready_waits_for_the_units_sandbox(self, unit) -> None:
        await unit.start()

        assert await unit.manager.wait_for_ready(unit.members[0], 5) is SessionStatus.RUNNING


class TestBrokerTarget:
    async def test_a_member_is_reached_on_its_own_port_over_the_native_tunnel(self, unit) -> None:
        await unit.start()
        member = unit.members[1]

        target = await unit.manager.broker_target(member)

        assert target.service_url == "http://skuld.internal"
        assert (target.connect_host, target.connect_port) == ("127.0.0.1", 43210)
        assert unit.client.forwarded == [
            {"sandbox": f"forge-{unit.root.id.hex[:13]}", "target_port": 8083}
        ]
        assert unit.manager.session_proxy_target(member) == target

    async def test_an_ordinary_session_has_none(self, unit) -> None:
        assert await unit.manager.broker_target(Session(id=uuid4(), name="plain")) is None

    async def test_a_member_of_a_sandbox_that_is_gone_raises(self, unit) -> None:
        unit.client.sandbox_exists = False

        with pytest.raises(ValueError, match="no ready OpenShell broker"):
            await unit.manager.broker_target(unit.members[0])
        assert unit.manager.session_proxy_target(unit.members[0]) is None

    async def test_a_member_without_a_port_has_no_target(self, unit) -> None:
        member = unit.members[0].model_copy(update={"hosted_port": None})
        assert unit.manager.session_proxy_target(member) is None


class TestStop:
    async def test_stopping_a_member_leaves_the_sandbox(self, unit) -> None:
        await unit.start()
        await unit.manager.broker_target(unit.members[0])

        assert await unit.manager.stop(unit.members[0]) is True

        assert unit.client.deleted == []
        assert len(unit.client.closed_forwarders) == 1
        assert str(unit.members[0].id) not in unit.manager._member_launches
        assert str(unit.members[1].id) in unit.manager._member_launches

    async def test_destroying_a_member_does_not_delete_the_units_sandbox(self, unit) -> None:
        await unit.start()

        assert await unit.manager.destroy(unit.members[0]) is True
        assert unit.client.deleted == []

    async def test_stopping_the_root_ends_the_sandbox_and_releases_every_member(self, unit) -> None:
        await unit.start()
        for member in unit.members:
            await unit.manager.broker_target(member)

        await unit.manager.destroy(unit.root)

        assert unit.client.deleted == [f"forge-{unit.root.id.hex[:13]}"]
        assert len(unit.client.closed_forwarders) == 2
        assert unit.manager._member_launches == {}
        assert unit.manager._unit_sandboxes == {}


class TestMemberFiles:
    def test_the_archive_leaves_the_mount_points_alone(self, monkeypatch) -> None:
        adapter = _import_adapter(monkeypatch)
        archive = adapter._credential_file_archive(
            {
                "/tmp/niuu-members/0/token": b"t",
                "/tmp/niuu-members/0/home/.codex/auth.json": b"a",
            },
            extraction_root="/tmp",
        )

        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            names = tar.getnames()
        assert "niuu-members" not in names
        assert "niuu-members/0" not in names
        assert "niuu-members/0/token" in names
        assert "niuu-members/0/home" in names
        assert "niuu-members/0/home/.codex" in names

    def test_member_files_are_extracted_under_tmp(self, monkeypatch) -> None:
        adapter = _import_adapter(monkeypatch)

        archives = adapter._credential_file_archives(
            {"/tmp/niuu-members/0/token": b"t", "/sandbox/.volundr/x": b"y"}
        )

        assert [root for root, _ in archives] == ["/sandbox", "/tmp"]

    def test_only_the_units_directory_under_tmp_is_a_credential_root(self, monkeypatch) -> None:
        adapter = _import_adapter(monkeypatch)

        with pytest.raises(RuntimeError, match="outside allowed roots"):
            adapter._sandbox_credential_path("/tmp/elsewhere/token")

    async def test_agent_credentials_move_into_the_members_own_home(self, unit) -> None:
        moved = unit.manager._member_credential_files(
            {
                "/home/volundr/.codex/auth.json": b"codex",
                "/sandbox/workspace/.env": b"shared",
                "/run/secrets/key": b"secret",
            },
            "/tmp/niuu-members/1/home",
        )

        assert moved == {
            "/tmp/niuu-members/1/home/.codex/auth.json": b"codex",
            "/sandbox/workspace/.env": b"shared",
            "/run/secrets/key": b"secret",
        }

    async def test_a_members_auth_file_reaches_its_volume_only(self, unit, monkeypatch) -> None:
        async def context(session, spec):
            files = {"/home/volundr/.codex/auth.json": b"codex-auth"}
            return unit.adapter.OpenShellCredentialContext(
                files=files if session.id == unit.members[1].id else {},
                providers=(),
                environment={},
                process_environment={},
            )

        monkeypatch.setattr(unit.manager, "_resolve_credential_env", context)

        await unit.start()

        assert unit.files["/tmp/niuu-members/1/home/.codex/auth.json"] == b"codex-auth"
        assert "/tmp/niuu-members/0/home/.codex/auth.json" not in unit.files
        assert "/sandbox/.codex/auth.json" not in unit.files


class TestMemberHarness:
    """The gateway restarts a sandbox, never a peer: the member's own Skuld is asked."""

    @staticmethod
    def _wired(unit, handler):
        import httpx

        from volundr.adapters.outbound.hosted_member_harness import MemberHarnessControl

        unit.manager._harness = MemberHarnessControl(
            restart_wait_seconds=5,
            poll_seconds=0.001,
            request_seconds=5,
            transport=httpx.MockTransport(handler),
        )

    async def test_stop_asks_the_members_skuld_on_its_tunnel(self, unit) -> None:
        import httpx

        await unit.start()
        seen = []

        def handler(request):
            seen.append((request.method, request.url.path, request.headers["host"]))
            return httpx.Response(200, json={"status": "stopped"})

        self._wired(unit, handler)

        await unit.manager.stop_hosted_member(unit.members[1])

        assert seen == [("POST", "/api/harness/stop", "skuld.internal")]

    async def test_restart_writes_the_new_credential_then_asks_and_records_the_launch(
        self, unit
    ) -> None:
        import httpx

        await unit.start()
        member = unit.members[0]
        relaunched = member.model_copy(update={"workload_config": _config("launch-2")})
        unit.launches[0] = HostedMemberLaunch(session=relaunched, spec=_member_spec(relaunched))
        calls = []

        def handler(request):
            calls.append((request.method, request.url.path, request.content))
            return httpx.Response(200, json={"status": "ok"})

        self._wired(unit, handler)
        before = len(unit.client.written_files)

        await unit.manager.restart_hosted_member(relaunched)

        written = unit.client.written_files[before:]
        assert [call["files"] for call in written] == [
            {"/tmp/niuu-members/0/token": unit.launches[0].spec.forge_session.token.encode()}
        ]
        assert calls[0][:2] == ("POST", "/api/harness/restart")
        assert b"launch-2" in calls[0][2]
        assert await unit.manager.status(relaunched) is SessionStatus.RUNNING
        assert await unit.manager.status_detail(relaunched) is None

    async def test_a_member_this_controller_did_not_start_cannot_be_restarted(self, unit) -> None:
        with pytest.raises(HostedUnitError, match="was not started by this OpenShell controller"):
            await unit.manager.restart_hosted_member(unit.members[0])

    async def test_a_restart_with_no_credential_minted_is_refused(self, unit) -> None:
        await unit.start()
        spec = _member_spec(unit.members[0])
        spec.forge_session = None
        unit.launches[0] = HostedMemberLaunch(session=unit.members[0], spec=spec)

        with pytest.raises(HostedUnitError, match="No credential was minted"):
            await unit.manager.restart_hosted_member(unit.members[0])


class TestUnitSizeIsCounted:
    async def test_the_refusal_counts_on_the_cap_counter(self, monkeypatch) -> None:
        from niuu.cap_telemetry import health_counts

        key = "cap_hits.openshell.max_peer_workloads"
        before = health_counts().get(key, 0)
        unit = UnitFixture(_import_adapter(monkeypatch), count=MAX_PEER_WORKLOADS + 1)

        with pytest.raises(ValueError):
            await unit.start()

        assert health_counts()[key] == before + 1
