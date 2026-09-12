"""Tests for the DockerContainerPodManager adapter (docker SDK mocked)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

import volundr.adapters.outbound.docker_container as dc
from niuu.ports.session_proxy import SessionProxyTarget
from volundr.adapters.outbound.docker_container import (
    MANAGED_BY,
    DockerContainerPodManager,
    _NetworkRegistry,
)
from volundr.adapters.outbound.local_process import ProcessInfo, ProcessState
from volundr.domain.models import GitSource, PodSpecAdditions, Session, SessionSpec, SessionStatus


class _Container:
    def __init__(self, name: str, status: str = "running") -> None:
        self.name = name
        self.status = status
        self.stopped = False
        self.removed = False
        self.reloads = 0

    def reload(self) -> None:
        self.reloads += 1

    def stop(self, timeout: int = 10) -> None:
        del timeout
        self.stopped = True
        self.status = "exited"

    def remove(self, force: bool = False) -> None:
        del force
        self.removed = True

    def logs(self, tail: int = 100) -> bytes:
        del tail
        return b"boom\n"


class _Containers:
    def __init__(self) -> None:
        self.by_name: dict[str, _Container] = {}
        self.run_kwargs: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        self.next_status = "running"

    def get(self, name: str) -> _Container:
        try:
            return self.by_name[name]
        except KeyError:
            raise dc.NotFound(f"no {name}") from None

    def run(self, **kwargs: Any) -> _Container:
        if self.fail_with is not None:
            exc, self.fail_with = self.fail_with, None
            raise exc
        self.run_kwargs.append(kwargs)
        container = _Container(kwargs["name"], status=self.next_status)
        self.by_name[kwargs["name"]] = container
        return container


class _Images:
    def __init__(self) -> None:
        self.pulled: list[str] = []

    def pull(self, image: str) -> None:
        self.pulled.append(image)


class _Client:
    def __init__(self) -> None:
        self.containers = _Containers()
        self.images = _Images()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> _Client:
    fake = _Client()
    monkeypatch.setattr(dc.docker, "from_env", lambda: fake)
    return fake


@pytest.fixture
def workspaces(tmp_path: Path) -> Path:
    path = tmp_path / "workspaces"
    path.mkdir()
    return path


@pytest.fixture
def manager(client: _Client, workspaces: Path, tmp_path: Path) -> DockerContainerPodManager:
    del client
    return DockerContainerPodManager(
        skuld_image="ghcr.io/niuulabs/skuld:test",
        network="niuu_default",
        platform_url="http://niuu:8080/",
        workspaces_dir=str(workspaces),
        state_file=str(tmp_path / "forge-state.json"),
        max_concurrent=4,
    )


@pytest.fixture
def session() -> Session:
    return Session(
        id=uuid4(),
        name="docker-session",
        model="claude-sonnet-5",
        owner_id="dev-user",
        tenant_id="default",
        source=GitSource(repo="https://github.com/niuulabs/example", branch="main"),
    )


@pytest.fixture
def spec() -> SessionSpec:
    return SessionSpec(
        values={
            "session": {"systemPrompt": "be helpful", "initialPrompt": "start"},
            "anthropic_api_key": "sk-ant-test",
            "env": {"EXTRA": "1"},
            "broker": {"cliType": "codex"},
        },
        pod_spec=PodSpecAdditions(env=[{"name": "FROM_POD", "value": "yes"}]),
    )


def _workspace(workspaces: Path, session: Session) -> Path:
    ws = workspaces / str(session.id)
    ws.mkdir()
    return ws


class TestStart:
    @pytest.mark.asyncio
    async def test_runs_sibling_container_on_network(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        ws = _workspace(workspaces, session)
        with (
            patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)),
            patch.dict(os.environ, {"LEAKY_PLATFORM_SECRET": "nope"}),
        ):
            result = await manager.start(session, spec)

        sid = str(session.id)
        kwargs = client.containers.run_kwargs[0]
        assert kwargs["image"] == "ghcr.io/niuulabs/skuld:test"
        assert kwargs["name"] == f"niuu-session-{sid}"
        assert kwargs["network"] == "niuu_default"
        assert "ports" not in kwargs
        assert kwargs["user"] == f"{os.getuid()}:{os.getgid()}"
        assert kwargs["labels"][dc.LABEL_SESSION] == sid
        assert kwargs["volumes"][str(ws)]["bind"] == f"/volundr/sessions/{sid}/workspace"
        assert (workspaces / f"{sid}.home").is_dir()

        env = kwargs["environment"]
        assert "LEAKY_PLATFORM_SECRET" not in env
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"
        assert env["EXTRA"] == "1"
        assert env["FROM_POD"] == "yes"
        assert env["SKULD__CLI_TYPE"] == "codex"
        assert env["SESSION_ID"] == sid
        assert env["WORKSPACE_DIR"] == f"/volundr/sessions/{sid}/workspace"
        assert env["SKULD__SESSION__WORKSPACE_DIR"] == env["WORKSPACE_DIR"]
        assert env["SKULD__HOST"] == "0.0.0.0"
        assert env["SKULD__PORT"] == "8081"
        assert env["SKULD__VOLUNDR_API_URL"] == "http://niuu:8080"
        assert env["SKULD__SESSION__MODEL"] == "claude-sonnet-5"
        assert env["SKULD__SESSION__OWNER_ID"] == "dev-user"
        assert env["SKULD__SESSION__SYSTEM_PROMPT"] == "be helpful"
        assert env["SKULD__SESSION__INITIAL_PROMPT"] == "start"
        assert env["HOME"] == "/home/skuld"
        assert "SKULD__CLI_BINARY" not in env

        assert result.pod_name == f"local-{sid[:8]}"
        assert result.chat_endpoint.endswith(f"/s/{sid}/session")
        info = manager._processes[sid]
        assert info.state == ProcessState.RUNNING
        assert info.managed_by == MANAGED_BY
        assert info.pid == dc.DockerContainerPodManager._synthetic_pid(sid)
        state = json.loads(Path(manager._state_file).read_text())
        assert state[sid]["managed_by"] == MANAGED_BY
        await manager.stop(session)

    @pytest.mark.asyncio
    async def test_publishes_loopback_port_without_network(
        self, client: _Client, workspaces: Path, tmp_path: Path, session: Session, spec: SessionSpec
    ) -> None:
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces),
            state_file=str(tmp_path / "state.json"),
            run_as_host_user=False,
        )
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        kwargs = client.containers.run_kwargs[0]
        assert "network" not in kwargs
        assert "user" not in kwargs
        port = manager._processes[str(session.id)].port
        assert kwargs["ports"] == {"8081/tcp": ("127.0.0.1", port)}
        await manager.stop(session)

    @pytest.mark.asyncio
    async def test_pulls_missing_image(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        client.containers.fail_with = dc.ImageNotFound("missing")
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        assert client.images.pulled == ["ghcr.io/niuulabs/skuld:test"]
        assert len(client.containers.run_kwargs) == 1
        await manager.stop(session)

    @pytest.mark.asyncio
    async def test_missing_image_without_pull_fails(
        self, client: _Client, workspaces: Path, tmp_path: Path, session: Session, spec: SessionSpec
    ) -> None:
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces),
            state_file=str(tmp_path / "state.json"),
            pull_missing="false",
        )
        client.containers.fail_with = dc.ImageNotFound("missing")
        ws = _workspace(workspaces, session)
        with (
            patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)),
            pytest.raises(dc.ImageNotFound),
        ):
            await manager.start(session, spec)
        assert manager._processes[str(session.id)].state == ProcessState.FAILED

    @pytest.mark.asyncio
    async def test_immediate_exit_surfaces_logs(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        client.containers.next_status = "exited"
        ws = _workspace(workspaces, session)
        with (
            patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)),
            pytest.raises(RuntimeError, match="exited immediately") as exc,
        ):
            await manager.start(session, spec)
        assert "boom" in str(exc.value)

    @pytest.mark.asyncio
    async def test_removes_stale_container_first(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        name = manager.container_name(str(session.id))
        stale = _Container(name, status="exited")
        client.containers.by_name[name] = stale
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        assert stale.removed is True
        await manager.stop(session)

    @pytest.mark.asyncio
    async def test_flock_is_rejected(
        self, manager: DockerContainerPodManager, workspaces: Path, session: Session
    ) -> None:
        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(extra_containers=[{"name": "ravn-a", "image": "x"}]),
        )
        ws = _workspace(workspaces, session)
        with (
            patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)),
            pytest.raises(RuntimeError, match="Flock sidecars are not supported"),
        ):
            await manager.start(session, spec)


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_stops_and_removes(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        container = client.containers.by_name[manager.container_name(str(session.id))]
        assert await manager.stop(session) is True
        assert container.stopped and container.removed
        assert await manager.status(session) == SessionStatus.STOPPED

    @pytest.mark.asyncio
    async def test_stop_tolerates_missing_container(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        client.containers.by_name.clear()
        assert await manager.stop(session) is True

    @pytest.mark.asyncio
    async def test_stop_logs_docker_error_and_removes(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        container = client.containers.by_name[manager.container_name(str(session.id))]

        def _boom(timeout: int = 10) -> None:
            del timeout
            raise dc.DockerException("daemon hiccup")

        container.stop = _boom  # type: ignore[method-assign]
        assert await manager.stop(session) is True
        assert container.removed is True

    @pytest.mark.asyncio
    async def test_monitor_marks_stopped_when_container_exits(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        death = AsyncMock()
        manager.set_death_callback(death)
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        sid = str(session.id)
        container = client.containers.by_name[manager.container_name(sid)]
        container.status = "exited"
        with patch.object(dc, "READY_POLL_INTERVAL", 0.01):
            await manager._monitor_process(sid, manager._processes[sid].pid or 0)
        assert manager._processes[sid].state == ProcessState.STOPPED
        death.assert_awaited_once_with(sid)
        monitor = manager._monitors.pop(sid, None)
        if monitor is not None:
            monitor.cancel()

    @pytest.mark.asyncio
    async def test_reconcile_reaps_dead_containers(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        sid = str(session.id)
        client.containers.by_name.clear()
        assert manager._reconcile_active() == []
        assert manager._processes[sid].state == ProcessState.STOPPED
        monitor = manager._monitors.pop(sid, None)
        if monitor is not None:
            monitor.cancel()

    def test_recovers_running_container_from_state_file(
        self, client: _Client, workspaces: Path, tmp_path: Path
    ) -> None:
        sid = str(uuid4())
        name = f"niuu-session-{sid}"
        client.containers.by_name[name] = _Container(name, status="running")
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {
                    sid: ProcessInfo(
                        session_id=sid, pid=123, port=9100, state=ProcessState.RUNNING
                    ).to_dict()
                }
            )
        )
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces), state_file=str(state_file)
        )
        assert manager._processes[sid].state == ProcessState.RUNNING
        assert manager._processes[sid].managed_by == MANAGED_BY

    def test_marks_stopped_when_container_gone(
        self, client: _Client, workspaces: Path, tmp_path: Path
    ) -> None:
        del client
        sid = str(uuid4())
        state_file = tmp_path / "state.json"
        state_file.write_text(
            json.dumps(
                {sid: ProcessInfo(session_id=sid, pid=1, state=ProcessState.RUNNING).to_dict()}
            )
        )
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces), state_file=str(state_file)
        )
        assert manager._processes[sid].state == ProcessState.STOPPED

    def test_foreign_managed_entries_are_not_recovered(
        self, client: _Client, workspaces: Path, tmp_path: Path
    ) -> None:
        sid = str(uuid4())
        name = f"niuu-session-{sid}"
        client.containers.by_name[name] = _Container(name, status="running")
        state_file = tmp_path / "state.json"
        info = ProcessInfo(session_id=sid, pid=1, state=ProcessState.RUNNING, managed_by="k8s")
        state_file.write_text(json.dumps({sid: info.to_dict()}))
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces), state_file=str(state_file)
        )
        assert manager._processes[sid].state == ProcessState.STOPPED


class TestProxyRouting:
    @pytest.mark.asyncio
    async def test_network_mode_installs_resolver_and_suppresses_port(
        self,
        manager: DockerContainerPodManager,
        client: _Client,
        workspaces: Path,
        session: Session,
        spec: SessionSpec,
    ) -> None:
        class _Registry:
            def __init__(self) -> None:
                self.registered: list[tuple[str, int]] = []
                self.unregistered: list[str] = []
                self.resolver: Any = None

            def register(self, session_id: str, port: int) -> None:
                self.registered.append((session_id, port))

            def unregister(self, session_id: str) -> None:
                self.unregistered.append(session_id)

            def set_target_resolver(self, resolver: Any) -> None:
                self.resolver = resolver

        registry = _Registry()
        manager.set_skuld_registry(registry)
        assert isinstance(manager._skuld_registry, _NetworkRegistry)
        assert registry.resolver is not None

        sid = str(session.id)
        assert await registry.resolver(sid) is None

        ws = _workspace(workspaces, session)
        with patch.object(manager, "_provision_workspace", AsyncMock(return_value=ws)):
            await manager.start(session, spec)
        assert registry.registered == []
        target = await registry.resolver(sid)
        assert target == SessionProxyTarget(
            service_url=f"http://niuu-session-{sid}:8081",
            connect_host=f"niuu-session-{sid}",
            connect_port=8081,
        )
        await manager.stop(session)
        assert registry.unregistered == [sid]

    def test_loopback_mode_uses_base_registry(
        self, client: _Client, workspaces: Path, tmp_path: Path
    ) -> None:
        del client
        manager = DockerContainerPodManager(
            workspaces_dir=str(workspaces), state_file=str(tmp_path / "state.json")
        )

        class _Registry:
            def __init__(self) -> None:
                self.registered: list[tuple[str, int]] = []

            def register(self, session_id: str, port: int) -> None:
                self.registered.append((session_id, port))

        registry = _Registry()
        manager.set_skuld_registry(registry)
        assert manager._skuld_registry is registry

    def test_network_registry_unregister_without_method(self) -> None:
        facade = _NetworkRegistry(object())
        facade.register("s", 1)
        facade.unregister("s")


class TestHelpers:
    def test_as_bool(self) -> None:
        assert dc._as_bool(True) is True
        assert dc._as_bool("yes") is True
        assert dc._as_bool("0") is False
        assert dc._as_bool(None) is False

    def test_docker_base_url_client(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        captured: dict[str, str] = {}

        class _Fake:
            def __init__(self, base_url: str) -> None:
                captured["base_url"] = base_url
                self.containers = _Containers()

        monkeypatch.setattr(dc.docker, "DockerClient", _Fake)
        DockerContainerPodManager(
            docker_base_url="unix:///tmp/docker.sock",
            workspaces_dir=str(tmp_path),
            state_file=str(tmp_path / "s.json"),
        )
        assert captured["base_url"] == "unix:///tmp/docker.sock"

    def test_terminate_unknown_pid_is_noop(self, manager: DockerContainerPodManager) -> None:
        import asyncio

        asyncio.run(manager._terminate_process(424242))
