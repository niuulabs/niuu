"""Tests for the OpenShell PodManager adapter."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from volundr.adapters.outbound.openshell import OpenShellClient, OpenShellPodManager
from volundr.domain.models import (
    LocalMountSource,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)


class FakeOpenShellClient:
    def __init__(self, state: str = "Ready"):
        self.state = state
        self.ensure_gateway_calls = 0
        self.create_calls: list[dict] = []
        self.forward_calls: list[dict] = []
        self.service_forward_calls: list[dict] = []
        self.forward_stop_calls: list[dict] = []
        self.delete_calls: list[str] = []

    async def ensure_gateway(self) -> None:
        self.ensure_gateway_calls += 1

    async def create_sandbox(self, **kwargs):
        self.create_calls.append(kwargs)
        return {"state": "Provisioning"}

    async def forward_start(self, **kwargs) -> None:
        self.forward_calls.append(kwargs)

    async def forward_service_start(self, **kwargs) -> None:
        self.service_forward_calls.append(kwargs)

    async def forward_stop(self, **kwargs) -> None:
        self.forward_stop_calls.append(kwargs)

    async def get_sandbox(self, name: str):
        return {"name": name, "state": self.state}

    async def delete_sandbox(self, name: str) -> bool:
        self.delete_calls.append(name)
        self.state = "Deleted"
        return True


class RecordingOpenShellClient(OpenShellClient):
    def __init__(self):
        super().__init__(gateway_url="")
        self.calls: list[tuple[str, ...]] = []

    async def _run(self, *args: str, check: bool = True):
        self.calls.append(args)
        if args[:3] == ("sandbox", "list", "-o"):
            return _Completed(
                returncode=0,
                stdout='[{"name":"forge-test","state":"Ready"}]',
                stderr="",
            )
        return _Completed(returncode=0, stdout="", stderr="")


class _Completed:
    def __init__(self, *, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RecordingSkuldRegistry:
    def __init__(self) -> None:
        self.ports: dict[str, int] = {}
        self.register_calls: list[tuple[str, int]] = []
        self.unregister_calls: list[str] = []

    def register(self, session_id: str, port: int) -> None:
        self.ports[session_id] = port
        self.register_calls.append((session_id, port))

    def unregister(self, session_id: str) -> None:
        self.ports.pop(session_id, None)
        self.unregister_calls.append(session_id)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    return path


@pytest.fixture
def session(workspace: Path) -> Session:
    return Session(
        id=uuid4(),
        name="openshell-codex",
        model="gpt-5.4",
        source=LocalMountSource(local_path=str(workspace)),
    )


def _spec(cli_type: str = "codex") -> SessionSpec:
    return SessionSpec(
        pod_spec=PodSpecAdditions(),
        values={
            "broker": {
                "cliType": cli_type,
                "transportAdapter": "skuld.transports.codex.CodexSubprocessTransport",
            },
            "env": {"EXPLICIT_SESSION_ENV": "yes"},
        }
    )


def _manager(tmp_path: Path, client: FakeOpenShellClient) -> OpenShellPodManager:
    return OpenShellPodManager(
        client=client,
        workspaces_dir=str(tmp_path / "workspaces"),
        state_file=str(tmp_path / "openshell-state.json"),
        sandbox_image="skuld:test",
        sdk_port_start=12900,
        require_broker_ready=False,
    )


class TestOpenShellPodManager:
    async def test_start_creates_sandbox_and_returns_volundr_proxy_endpoint(
        self,
        tmp_path: Path,
        session: Session,
        workspace: Path,
    ) -> None:
        client = FakeOpenShellClient()
        manager = _manager(tmp_path, client)

        result = await manager.start(session, _spec("codex"))

        assert client.ensure_gateway_calls == 1
        assert len(client.create_calls) == 1
        create = client.create_calls[0]
        assert create["name"] == f"forge-{session.id}"
        assert create["image"] == "skuld:test"
        assert create["command"] == ["/opt/venv/bin/python", "-m", "skuld"]
        assert create["labels"]["volundr.niuu.io/session"] == str(session.id)
        assert create["labels"]["volundr.niuu.io/runtime"] == "codex"
        assert create["env"]["SKULD__CLI_TYPE"] == "codex"
        assert create["env"]["SKULD__SESSION__ID"] == str(session.id)
        assert create["env"]["SKULD__SESSION__WORKSPACE_DIR"] == "/sandbox/workspace"
        assert create["env"]["EXPLICIT_SESSION_ENV"] == "yes"
        assert "PATH" not in create["env"]
        assert client.forward_calls == [
            {"sandbox_name": f"forge-{session.id}", "port": 12900}
        ]
        assert result.chat_endpoint == f"ws://localhost:8080/s/{session.id}/session"
        assert result.code_endpoint == f"file://{workspace}"
        assert result.pod_name == f"forge-{session.id}"

    async def test_start_can_upload_workspace_for_vm_driver(
        self,
        tmp_path: Path,
        session: Session,
        workspace: Path,
    ) -> None:
        client = FakeOpenShellClient()
        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            sandbox_command=(
                "/bin/sh /sandbox/workspace/scripts/openshell-run-skuld-from-workspace.sh"
            ),
            mount_workspace="false",
            upload_workspace="true",
            upload_workspace_target="/sandbox/workspace",
            upload_no_git_ignore="false",
            sandbox_workspace="/sandbox/workspace/repo",
            sandbox_uploads="/host/bootstrap.sh:/sandbox/bootstrap.sh",
            policy_file="/host/policy.yaml",
        )

        await manager.start(session, _spec("codex"))

        create = client.create_calls[0]
        assert create["command"] == [
            "/bin/sh",
            "/sandbox/workspace/scripts/openshell-run-skuld-from-workspace.sh",
        ]
        assert create["env"]["SKULD__SESSION__WORKSPACE_DIR"] == "/sandbox/workspace/repo"
        assert create["driver_config_json"] == ""
        assert create["uploads"] == [
            f"{workspace}:/sandbox/workspace",
            "/host/bootstrap.sh:/sandbox/bootstrap.sh",
        ]
        assert create["upload_no_git_ignore"] is False
        assert create["policy_file"] == "/host/policy.yaml"

    async def test_start_can_mount_local_cli_auth_dirs_without_mounting_workspace(
        self,
        tmp_path: Path,
        session: Session,
        workspace: Path,
    ) -> None:
        client = FakeOpenShellClient()
        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            mount_workspace=False,
            upload_workspace=True,
            upload_workspace_target="/sandbox/workspace",
            sandbox_workspace="/sandbox/workspace/repo",
            sandbox_mounts=[
                f"{tmp_path}/codex:/home/sandbox/.codex",
                {
                    "source": str(tmp_path / "claude"),
                    "target": "/home/sandbox/.claude",
                },
            ],
        )

        await manager.start(session, _spec("codex"))

        create = client.create_calls[0]
        driver_config = json.loads(create["driver_config_json"])
        expected_mounts = [
            {
                "type": "bind",
                "source": str(tmp_path / "codex"),
                "target": "/home/sandbox/.codex",
            },
            {
                "type": "bind",
                "source": str(tmp_path / "claude"),
                "target": "/home/sandbox/.claude",
            },
        ]
        assert driver_config["docker"]["mounts"] == expected_mounts
        assert driver_config["podman"]["mounts"] == expected_mounts
        assert create["uploads"] == [f"{workspace}:/sandbox/workspace"]

    async def test_start_can_parse_sandbox_mounts_from_json(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient()
        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            sandbox_mounts=json.dumps(
                [
                    {
                        "source": str(tmp_path / "codex"),
                        "target": "/home/sandbox/.codex",
                    }
                ]
            ),
        )

        await manager.start(session, _spec("codex"))

        create = client.create_calls[0]
        driver_config = json.loads(create["driver_config_json"])
        assert driver_config["docker"]["mounts"][-1] == {
            "type": "bind",
            "source": str(tmp_path / "codex"),
            "target": "/home/sandbox/.codex",
        }

    async def test_start_supports_claude_runtime(self, tmp_path: Path, session: Session) -> None:
        client = FakeOpenShellClient()
        manager = _manager(tmp_path, client)

        await manager.start(session, _spec("claude"))

        create = client.create_calls[0]
        assert create["labels"]["volundr.niuu.io/runtime"] == "claude"
        assert create["env"]["SKULD__CLI_TYPE"] == "claude"

    async def test_start_can_use_service_forward_mode(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient()
        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            sdk_port_start=12900,
            forward_mode="service",
            require_broker_ready=False,
        )

        await manager.start(session, _spec("codex"))

        assert client.forward_calls == []
        assert client.service_forward_calls == [
            {"sandbox_name": f"forge-{session.id}", "port": 12900}
        ]

    async def test_status_maps_openshell_states(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient(state="Provisioning")
        manager = _manager(tmp_path, client)
        await manager.start(session, _spec())

        assert await manager.status(session) == SessionStatus.PROVISIONING

        client.state = "Ready"
        assert await manager.status(session) == SessionStatus.RUNNING

        client.state = "Error"
        assert await manager.status(session) == SessionStatus.FAILED

    async def test_status_waits_for_broker_health_after_sandbox_ready(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient(state="Ready")
        healthy = False

        async def healthcheck(_port: int) -> bool:
            return healthy

        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            sdk_port_start=12900,
            healthcheck=healthcheck,
        )
        await manager.start(session, _spec())

        assert await manager.status(session) == SessionStatus.PROVISIONING

        healthy = True
        assert await manager.status(session) == SessionStatus.RUNNING

    async def test_status_stays_provisioning_until_start_completes(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        state_file = tmp_path / "openshell-state.json"
        state_file.write_text(
            json.dumps(
                {
                    str(session.id): {
                        "session_id": str(session.id),
                        "sandbox_name": f"forge-{session.id}",
                        "port": 12900,
                        "workspace": str(tmp_path),
                        "runtime": "codex",
                        "state": "starting",
                        "start_complete": False,
                    }
                }
            ),
            encoding="utf-8",
        )
        manager = OpenShellPodManager(
            client=FakeOpenShellClient(state="Ready"),
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(state_file),
            sandbox_image="skuld:test",
            sdk_port_start=12900,
            require_broker_ready=False,
        )

        assert await manager.status(session) == SessionStatus.PROVISIONING

    async def test_max_concurrent_counts_starting_sessions(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient(state="Ready")

        async def healthcheck(_port: int) -> bool:
            return False

        manager = OpenShellPodManager(
            client=client,
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(tmp_path / "openshell-state.json"),
            sandbox_image="skuld:test",
            sdk_port_start=12900,
            max_concurrent=1,
            healthcheck=healthcheck,
        )
        await manager.start(session, _spec())

        second = Session(
            id=uuid4(),
            name="openshell-codex-2",
            model="gpt-5.4",
            source=session.source,
        )
        with pytest.raises(RuntimeError, match="Max concurrent OpenShell sessions"):
            await manager.start(second, _spec())

    async def test_stop_deletes_sandbox(self, tmp_path: Path, session: Session) -> None:
        client = FakeOpenShellClient()
        manager = _manager(tmp_path, client)
        await manager.start(session, _spec())

        stopped = await manager.stop(session)

        assert stopped is True
        assert client.forward_stop_calls == [
            {
                "sandbox_name": f"forge-{session.id}",
                "port": 12900,
                "mode": "start",
            }
        ]
        assert client.delete_calls == [f"forge-{session.id}"]
        assert await manager.status(session) == SessionStatus.STOPPED

    async def test_initial_chat_endpoint_uses_persisted_forward_port(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient()
        manager = _manager(tmp_path, client)

        assert manager.initial_chat_endpoint(session) is None
        await manager.start(session, _spec())

        assert manager.initial_chat_endpoint(session) == (
            f"ws://localhost:8080/s/{session.id}/session"
        )

    async def test_registers_forward_port_with_skuld_proxy_registry(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        client = FakeOpenShellClient()
        manager = _manager(tmp_path, client)
        registry = RecordingSkuldRegistry()
        manager.set_skuld_registry(registry)

        await manager.start(session, _spec())
        await manager.stop(session)

        assert registry.register_calls == [(str(session.id), 12900)]
        assert registry.unregister_calls == [str(session.id)]

    async def test_set_skuld_registry_rehydrates_running_sessions(
        self,
        tmp_path: Path,
        session: Session,
    ) -> None:
        state_file = tmp_path / "openshell-state.json"
        state_file.write_text(
            json.dumps(
                {
                    str(session.id): {
                        "session_id": str(session.id),
                        "sandbox_name": f"forge-{session.id}",
                        "port": 12942,
                        "workspace": str(tmp_path),
                        "runtime": "claude",
                        "state": "running",
                    }
                }
            ),
            encoding="utf-8",
        )
        manager = OpenShellPodManager(
            client=FakeOpenShellClient(),
            workspaces_dir=str(tmp_path / "workspaces"),
            state_file=str(state_file),
            sandbox_image="skuld:test",
            require_broker_ready=False,
        )
        registry = RecordingSkuldRegistry()

        manager.set_skuld_registry(registry)

        assert registry.register_calls == [(str(session.id), 12942)]


class TestOpenShellClient:
    async def test_create_sandbox_uses_current_cli_without_json_output_flag(self) -> None:
        client = RecordingOpenShellClient()

        await client.create_sandbox(
            name="forge-test",
            image="skuld:test",
            command=["python", "-m", "skuld"],
            env={"SKULD__PORT": "9200"},
            labels={"volundr.niuu.io/session": "abc"},
            uploads=["/host/repo:/sandbox/workspace"],
            upload_no_git_ignore=True,
            policy_file="/host/policy.yaml",
        )

        call = client.calls[0]
        assert call[:5] == ("sandbox", "create", "--name", "forge-test", "--from")
        assert "-o" not in call
        assert "--no-git-ignore" in call
        policy_index = call.index("--policy")
        assert call[policy_index + 1] == "/host/policy.yaml"
        upload_index = call.index("--upload")
        assert call[upload_index + 1] == "/host/repo:/sandbox/workspace"
        assert call[-4:] == ("--", "python", "-m", "skuld")

    async def test_get_sandbox_filters_json_list_by_name(self) -> None:
        client = RecordingOpenShellClient()

        sandbox = await client.get_sandbox("forge-test")

        assert sandbox == {"name": "forge-test", "state": "Ready"}
        assert client.calls == [("sandbox", "list", "-o", "json")]

    async def test_forward_start_runs_in_background(self) -> None:
        client = RecordingOpenShellClient()

        await client.forward_start(sandbox_name="forge-test", port=9200)

        assert client.calls == [("forward", "start", "9200", "forge-test", "-d")]
