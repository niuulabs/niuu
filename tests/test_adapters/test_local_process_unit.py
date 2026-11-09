"""LocalProcessPodManager hosting a workflow run: one process group for root and members."""

from __future__ import annotations

import os
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from volundr.adapters.outbound.local_process import (
    LocalProcessPodManager,
    ProcessInfo,
    ProcessState,
)
from volundr.adapters.outbound.vm_hosted_unit import HostedMemberLaunch, HostedUnitError
from volundr.domain.models import HOSTED_MEMBERS_KEY as UNIT_MEMBERS_KEY
from volundr.domain.models import (
    GitSource,
    LocalMountSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.services.forge_session_launch import with_forge_mcp
from volundr.domain.services.session import SessionCapacityError

MESH = {"enabled": True}


def _config(launch: str) -> dict:
    return with_forge_mcp({}, grants=[], launch_id=launch)


def _root(members: list[Session], *, source=None) -> Session:
    return Session(
        id=uuid4(),
        name="wf-run",
        owner_id="owner-1",
        source=source or GitSource(),
        workload_config={
            **_config("root-launch"),
            UNIT_MEMBERS_KEY: [{"id": str(m.id), "port": m.hosted_port} for m in members],
        },
    )


def _member(unit_id, index: int, *, launch: str = "launch-1", source=None) -> Session:
    return Session(
        id=uuid4(),
        name=f"member-{index}",
        owner_id="owner-1",
        hosted_by=unit_id,
        hosted_port=8082 + index,
        source=source or GitSource(),
        workload_config=_config(launch),
    )


def _members(root_id, count: int = 2, **kwargs) -> list[Session]:
    return [_member(root_id, i, **kwargs) for i in range(count)]


def _spec() -> SessionSpec:
    return SessionSpec(values={}, pod_spec=PodSpecAdditions())


def _hosted(sessions: list[Session]) -> list[HostedMemberLaunch]:
    return [HostedMemberLaunch(session=s, spec=_spec()) for s in sessions]


class Fixture:
    """A unit root, its members and a manager whose process spawning is recorded."""

    def __init__(self, manager: LocalProcessPodManager, workspace: Path, count: int = 2) -> None:
        self.manager = manager
        self.workspace = workspace
        # Created before the root so the root can record them; the root's id is what they
        # are hosted by, so build the root first with placeholder members.
        placeholder_root = uuid4()
        self.members = _members(placeholder_root, count)
        self.root = _root(self.members)
        self.members = [m.model_copy(update={"hosted_by": self.root.id}) for m in self.members]
        self.next_pid = 1000
        self.spawned: list[dict] = []
        self.source = AsyncMock(side_effect=self._source)

    async def _source(self, unit_id, only):
        members = [m for m in self.members if only is None or m.id == only]
        return _hosted(members)

    async def fake_spawn(self, session, spec, workspace, port, *, mesh=None, process_group=None):
        self.next_pid += 1
        self.spawned.append(
            {
                "session": session,
                "port": port,
                "mesh": mesh,
                "group": process_group,
                "pid": self.next_pid,
                "workspace": workspace,
            }
        )
        return self.next_pid


@pytest.fixture
def fx(tmp_path: Path) -> Fixture:
    workspace = tmp_path / "project"
    workspace.mkdir()
    manager = LocalProcessPodManager(
        workspaces_dir=str(tmp_path / "ws"),
        state_file=str(tmp_path / "state.json"),
        sdk_port_start=19_500,
        mesh=MESH,
        max_concurrent=8,
    )
    fixture = Fixture(manager, workspace)
    manager.configure_hosted_members(fixture.source)
    return fixture


def _local(fx: Fixture) -> LocalMountSource:
    return LocalMountSource(local_path=str(fx.workspace))


@pytest.fixture
def started(fx: Fixture):
    """The unit started, with spawning and the monitors replaced."""

    async def start():
        root = fx.root.model_copy(update={"source": _local(fx)})
        patches = (
            patch.object(fx.manager, "_spawn_skuld", side_effect=fx.fake_spawn),
            patch.object(fx.manager, "_monitor_process", new=AsyncMock()),
        )
        for p in patches:
            p.start()
        try:
            return root, await fx.manager.start(root, _spec())
        finally:
            for p in patches:
                p.stop()

    return start


class TestStartUnit:
    async def test_members_join_the_roots_process_group(self, fx, started) -> None:
        root, result = await started()

        assert [s["group"] for s in fx.spawned] == [0, 1001, 1001]
        assert [s["session"].id for s in fx.spawned] == [root.id, *[m.id for m in fx.members]]
        assert result.chat_endpoint.endswith(f"/s/{root.id}/session")
        assert result.code_endpoint == f"file://{fx.workspace}"
        info = fx.manager._processes[str(root.id)]
        assert info.group == 1001
        assert info.hosted_by is None

    async def test_every_member_is_its_own_process_on_its_own_port(self, fx, started) -> None:
        root, _ = await started()

        ports = [s["port"] for s in fx.spawned]
        assert len(set(ports)) == 3
        for member in fx.members:
            info = fx.manager._processes[str(member.id)]
            assert info.hosted_by == str(root.id)
            assert info.state is ProcessState.RUNNING
            assert info.launch_id == "launch-1"
            assert info.group is None

    async def test_all_share_the_one_checkout(self, fx, started) -> None:
        await started()

        assert {s["workspace"] for s in fx.spawned} == {fx.workspace}

    async def test_mesh_registry_is_the_runs_own_directory(self, fx, started, tmp_path) -> None:
        root, _ = await started()

        meshes = {s["session"].id: s["mesh"] for s in fx.spawned}
        registry = tmp_path / "mesh" / "units" / str(root.id) / "peers.json"
        for mesh in meshes.values():
            assert mesh["discovery_adapters"] == [{"adapter": "registry", "path": str(registry)}]
            assert mesh["enabled"] is True
        assert registry.parent.is_dir()

    async def test_state_is_persisted_with_the_group(self, fx, started) -> None:
        root, _ = await started()

        saved = ProcessInfo.from_dict(
            __import__("json").loads(fx.manager._state_file.read_text())[str(root.id)]
        )
        assert saved.group == 1001
        assert saved.state is ProcessState.RUNNING

    async def test_members_are_registered_with_the_proxy(self, fx, started) -> None:
        registry = MagicMock()
        fx.manager.set_skuld_registry(registry)
        root, _ = await started()

        registered = {call.args[0] for call in registry.register.call_args_list}
        assert registered == {str(root.id), *[str(m.id) for m in fx.members]}

    async def test_a_member_cannot_start_alone(self, fx) -> None:
        with pytest.raises(HostedUnitError, match="starts with its unit"):
            await fx.manager.start(fx.members[0], _spec())

    async def test_no_member_source_is_refused(self, fx) -> None:
        fx.manager._hosted_members = None
        with pytest.raises(HostedUnitError, match="no member source"):
            await fx.manager.start(fx.root, _spec())

    async def test_members_that_differ_from_the_recorded_ones_are_refused(self, fx) -> None:
        stranger = _member(fx.root.id, 5)
        fx.source.side_effect = None
        fx.source.return_value = _hosted([*fx.members, stranger])

        with pytest.raises(HostedUnitError, match="fixed when its unit starts"):
            await fx.manager.start(fx.root, _spec())

    async def test_a_member_hosted_by_another_unit_is_refused(self, fx) -> None:
        wrong = fx.members[0].model_copy(update={"hosted_by": uuid4()})
        fx.source.side_effect = None
        fx.source.return_value = _hosted([wrong, fx.members[1]])

        with pytest.raises(HostedUnitError, match="is hosted by"):
            await fx.manager.start(fx.root, _spec())

    async def test_mesh_off_refuses_the_unit(self, fx, tmp_path) -> None:
        manager = LocalProcessPodManager(
            workspaces_dir=str(tmp_path / "ws2"), state_file=str(tmp_path / "s2.json")
        )
        manager.configure_hosted_members(fx.source)

        with pytest.raises(HostedUnitError, match="mesh.enabled: true"):
            await manager.start(fx.root, _spec())
        assert fx.spawned == []

    async def test_unit_that_does_not_fit_says_how_many_slots_it_needs(self, fx, started) -> None:
        fx.manager._max_concurrent = 2

        with pytest.raises(SessionCapacityError, match="needs 3 free slots"):
            await fx.manager.start(fx.root, _spec())
        assert fx.spawned == []

    async def test_spawn_failure_ends_the_group_and_fails_every_member(self, fx) -> None:
        root = fx.root.model_copy(update={"source": _local(fx)})
        calls = 0

        async def failing(session, spec, workspace, port, *, mesh=None, process_group=None):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("no such file")
            return 2000 + calls

        with (
            patch.object(fx.manager, "_spawn_skuld", side_effect=failing),
            patch.object(fx.manager, "_terminate_group", new_callable=AsyncMock) as terminate,
            pytest.raises(OSError, match="no such file"),
        ):
            await fx.manager.start(root, _spec())

        terminate.assert_awaited_once_with(2001)
        states = [fx.manager._processes[str(s.id)].state for s in (root, *fx.members)]
        assert states == [ProcessState.FAILED] * 3
        assert fx.manager._port_allocator.allocated == set()


class TestWorkspace:
    async def test_roots_source_is_the_checkout(self, fx) -> None:
        root = fx.root.model_copy(update={"source": _local(fx)})
        provision = AsyncMock(return_value=fx.workspace)
        with patch.object(fx.manager, "_provision_workspace", provision):
            members = _hosted(fx.members)
            assert await fx.manager._provision_unit_workspace(root, _spec(), members) == (
                fx.workspace
            )
        provision.assert_awaited_once()
        assert provision.await_args.args[0] is root

    async def test_agentless_root_clones_what_the_members_agree_on(self, fx) -> None:
        source = GitSource(repo="github.com/org/repo", branch="feat/x")
        members = [m.model_copy(update={"source": source}) for m in fx.members]
        provision = AsyncMock(return_value=fx.workspace)
        with patch.object(fx.manager, "_provision_workspace", provision):
            await fx.manager._provision_unit_workspace(fx.root, _spec(), _hosted(members))

        provisioned = provision.await_args.args[0]
        assert provisioned.id == fx.root.id
        assert provisioned.source == source

    async def test_members_on_different_branches_are_refused(self, fx) -> None:
        first = fx.members[0].model_copy(update={"source": GitSource(repo="r", branch="a")})
        second = fx.members[1].model_copy(update={"source": GitSource(repo="r", branch="b")})

        with pytest.raises(HostedUnitError, match="share one checkout"):
            await fx.manager._provision_unit_workspace(fx.root, _spec(), _hosted([first, second]))

    async def test_member_naming_another_workspace_than_the_root_is_refused(self, fx) -> None:
        root = fx.root.model_copy(update={"source": _local(fx)})
        member = fx.members[0].model_copy(update={"source": GitSource(repo="r", branch="a")})

        with pytest.raises(HostedUnitError, match="share one checkout"):
            await fx.manager._provision_unit_workspace(root, _spec(), _hosted([member]))

    async def test_no_source_anywhere_gives_the_root_its_own_directory(self, fx) -> None:
        provision = AsyncMock(return_value=fx.workspace)
        with patch.object(fx.manager, "_provision_workspace", provision):
            await fx.manager._provision_unit_workspace(fx.root, _spec(), _hosted(fx.members))
        assert provision.await_args.args[0] is fx.root


class TestStatusPerMember:
    async def test_a_running_member_reads_running_not_reaped(self, fx, started) -> None:
        await started()
        member = fx.members[0]
        fx.manager._ready.add(str(member.id))

        assert await fx.manager.status(member) is SessionStatus.RUNNING

    async def test_a_member_not_yet_ready_reads_provisioning(self, fx, started) -> None:
        await started()
        with patch.object(fx.manager, "_broker_healthy", AsyncMock(return_value=False)):
            assert await fx.manager.status(fx.members[0]) is SessionStatus.PROVISIONING

    async def test_a_stopped_member_reads_stopped_while_the_unit_runs(self, fx, started) -> None:
        root, _ = await started()
        fx.manager._processes[str(fx.members[0].id)].state = ProcessState.STOPPED
        fx.manager._ready.add(str(root.id))

        assert await fx.manager.status(fx.members[0]) is SessionStatus.STOPPED
        assert await fx.manager.status(fx.members[1]) is not SessionStatus.STOPPED


class TestBrokerTarget:
    async def test_member_is_reached_on_its_own_loopback_port(self, fx, started) -> None:
        await started()
        member = fx.members[1]
        port = fx.manager._processes[str(member.id)].port

        target = await fx.manager.broker_target(member)

        assert target.connect_host == "127.0.0.1"
        assert target.connect_port == port
        assert target.service_url == f"http://127.0.0.1:{port}"

    async def test_an_ordinary_session_has_none(self, fx) -> None:
        assert await fx.manager.broker_target(Session(id=uuid4(), name="plain")) is None

    async def test_a_member_without_a_process_raises(self, fx) -> None:
        with pytest.raises(ValueError, match="no running Skuld"):
            await fx.manager.broker_target(fx.members[0])

    def test_member_chat_endpoint_is_its_own_proxy_route(self, fx) -> None:
        member = fx.members[0]
        assert fx.manager.initial_chat_endpoint(member) == (
            f"ws://localhost:8080/s/{member.id}/session"
        )
        assert fx.manager.initial_chat_endpoint(fx.root) is None


class TestStop:
    async def test_stopping_the_root_ends_the_whole_group(self, fx, started) -> None:
        root, _ = await started()

        with patch.object(fx.manager, "_terminate_group", new_callable=AsyncMock) as terminate:
            assert await fx.manager.stop(root) is True

        terminate.assert_awaited_once_with(1001)
        for session in (root, *fx.members):
            assert fx.manager._processes[str(session.id)].state is ProcessState.STOPPED
        assert fx.manager._port_allocator.allocated == set()

    async def test_stopping_the_root_removes_the_runs_registry(self, fx, started, tmp_path) -> None:
        root, _ = await started()
        registry_dir = tmp_path / "mesh" / "units" / str(root.id)
        assert registry_dir.is_dir()

        with patch.object(fx.manager, "_terminate_group", new_callable=AsyncMock):
            await fx.manager.stop(root)

        assert not registry_dir.exists()

    async def test_stopping_a_member_leaves_the_unit_running(self, fx, started) -> None:
        root, _ = await started()
        victim, other = fx.members

        with (
            patch.object(fx.manager, "_terminate_process", new_callable=AsyncMock) as terminate,
            patch.object(fx.manager, "_terminate_group", new_callable=AsyncMock) as group,
        ):
            assert await fx.manager.stop(victim) is True

        terminate.assert_awaited_once()
        group.assert_not_awaited()
        assert fx.manager._processes[str(victim.id)].state is ProcessState.STOPPED
        assert fx.manager._processes[str(other.id)].state is ProcessState.RUNNING
        assert fx.manager._processes[str(root.id)].state is ProcessState.RUNNING

    async def test_the_roots_exit_ends_the_members_too(self, fx, started) -> None:
        root, _ = await started()
        info = fx.manager._processes[str(root.id)]
        info.state = ProcessState.STOPPED

        with patch.object(fx.manager, "_terminate_group", new_callable=AsyncMock) as group:
            ended = await fx.manager._end_unit_after_root(str(root.id), info)

        group.assert_awaited_once_with(1001)
        assert sorted(ended) == sorted(str(m.id) for m in fx.members)
        for member in fx.members:
            assert fx.manager._processes[str(member.id)].state is ProcessState.STOPPED

    async def test_an_ordinary_process_exit_cascades_nothing(self, fx) -> None:
        info = ProcessInfo(session_id="s", pid=5, state=ProcessState.STOPPED)
        assert await fx.manager._end_unit_after_root("s", info) == []


class TestTerminateGroup:
    async def test_signals_the_group_and_waits_for_it_to_empty(self, fx) -> None:
        calls: list[tuple[int, int]] = []

        def killpg(group: int, sig: int) -> None:
            calls.append((group, sig))
            if sig == 0:
                raise ProcessLookupError

        with (
            patch.object(os, "killpg", side_effect=killpg),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await fx.manager._terminate_group(77)

        assert calls == [(77, signal.SIGTERM), (77, 0)]

    async def test_kills_what_survives_the_timeout(self, fx) -> None:
        fx.manager._stop_timeout = 1
        calls: list[int] = []

        def killpg(group: int, sig: int) -> None:
            calls.append(sig)

        with (
            patch.object(os, "killpg", side_effect=killpg),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await fx.manager._terminate_group(77)

        assert calls[0] == signal.SIGTERM
        assert calls[-1] == signal.SIGKILL

    async def test_a_group_that_is_gone_is_done(self, fx) -> None:
        with patch.object(os, "killpg", side_effect=ProcessLookupError):
            await fx.manager._terminate_group(77)


class TestRestartInPlace:
    async def test_a_new_launch_replaces_the_members_process_in_the_units_group(
        self, fx, started
    ) -> None:
        root, _ = await started()
        fx.manager._ready.add(str(root.id))
        member = fx.members[0]
        old = fx.manager._processes[str(member.id)]
        old_pid = old.pid
        relaunched = member.model_copy(update={"workload_config": _config("launch-2")})
        fx.members[0] = relaunched
        fx.spawned.clear()

        with (
            patch.object(fx.manager, "_spawn_skuld", side_effect=fx.fake_spawn),
            patch.object(fx.manager, "_monitor_process", new=AsyncMock()),
            patch.object(fx.manager, "_terminate_process", new_callable=AsyncMock) as terminate,
        ):
            status = await fx.manager.status(relaunched)

        assert status is SessionStatus.PROVISIONING
        terminate.assert_awaited_once_with(old_pid)
        fx.source.assert_awaited_with(root.id, member.id)
        assert [s["group"] for s in fx.spawned] == [1001]
        info = fx.manager._processes[str(member.id)]
        assert info.launch_id == "launch-2"
        assert info.state is ProcessState.RUNNING
        assert info.pid == fx.spawned[0]["pid"]

    async def test_the_same_launch_is_not_restarted(self, fx, started) -> None:
        await started()
        fx.source.reset_mock()
        fx.manager._ready.add(str(fx.members[0].id))

        assert await fx.manager.status(fx.members[0]) is SessionStatus.RUNNING
        fx.source.assert_not_awaited()

    async def test_a_restart_that_cannot_spawn_fails_the_member(self, fx, started) -> None:
        await started()
        member = fx.members[0]
        relaunched = member.model_copy(update={"workload_config": _config("launch-2")})
        fx.members[0] = relaunched

        with (
            patch.object(fx.manager, "_spawn_skuld", side_effect=OSError("boom")),
            patch.object(fx.manager, "_terminate_process", new_callable=AsyncMock),
            pytest.raises(OSError, match="boom"),
        ):
            await fx.manager.status(relaunched)

        assert fx.manager._processes[str(member.id)].state is ProcessState.FAILED

    async def test_without_a_source_a_new_launch_is_an_error(self, fx, started) -> None:
        await started()
        fx.manager._hosted_members = None
        relaunched = fx.members[0].model_copy(update={"workload_config": _config("launch-2")})

        with pytest.raises(HostedUnitError, match="no member source"):
            await fx.manager.status(relaunched)

    async def test_a_source_answering_for_another_member_is_refused(self, fx, started) -> None:
        await started()
        relaunched = fx.members[0].model_copy(update={"workload_config": _config("launch-2")})
        fx.source.side_effect = None
        fx.source.return_value = _hosted(fx.members)

        with pytest.raises(HostedUnitError, match="answered"):
            await fx.manager.status(relaunched)

    async def test_a_stopped_unit_does_not_restart_members(self, fx, started) -> None:
        root, _ = await started()
        fx.manager._processes[str(root.id)].state = ProcessState.STOPPED
        relaunched = fx.members[0].model_copy(update={"workload_config": _config("launch-2")})
        fx.source.reset_mock()

        await fx.manager.status(relaunched)

        fx.source.assert_not_awaited()


class TestSpawnArguments:
    async def test_process_group_and_mesh_reach_the_subprocess(self, fx, tmp_path) -> None:
        session = fx.members[0]
        workspace = tmp_path / "w"
        workspace.mkdir()
        mesh = {"enabled": True, "marker": "unit"}
        with (
            patch.object(fx.manager, "_resolve_claude_binary", return_value="/bin/claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=MagicMock(pid=9),
            ) as spawn,
        ):
            await fx.manager._spawn_skuld(
                session, _spec(), workspace, 9100, mesh=mesh, process_group=42
            )

        assert spawn.call_args.kwargs["process_group"] == 42
        assert spawn.call_args.kwargs["env"]["SKULD__MESH__MARKER"] == "unit"
        assert spawn.call_args.kwargs["env"]["SKULD__SESSION__ID"] == str(session.id)

    async def test_an_ordinary_session_is_not_given_a_group(self, fx, tmp_path) -> None:
        session = Session(id=uuid4(), name="plain")
        workspace = tmp_path / "w"
        workspace.mkdir()
        with (
            patch.object(fx.manager, "_resolve_claude_binary", return_value="/bin/claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=MagicMock(pid=9),
            ) as spawn,
        ):
            await fx.manager._spawn_skuld(session, _spec(), workspace, 9100)

        assert "process_group" not in spawn.call_args.kwargs


class TestProcessInfoPersistence:
    def test_old_state_files_load(self) -> None:
        info = ProcessInfo.from_dict({"session_id": "a", "pid": 1, "state": "running"})
        assert (info.group, info.hosted_by, info.launch_id) == (None, None, None)

    def test_roundtrip_keeps_unit_fields(self) -> None:
        info = ProcessInfo(
            session_id="a", pid=1, group=1, hosted_by="r", launch_id="l", state=ProcessState.RUNNING
        )
        assert ProcessInfo.from_dict(info.to_dict()) == info
