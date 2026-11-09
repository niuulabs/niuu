"""Tests for the LocalProcessPodManager adapter."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from tests.support.workflow_steps import (
    MEMBER_STEPS_ENV,
    MEMBER_STEPS_VALUES,
    ROOT_STEPS_ENV,
    ROOT_STEPS_VALUES,
)
from volundr.adapters.outbound import local_process as local_process_mod
from volundr.adapters.outbound.local_process import (
    DEFAULT_CLAUDE_BINARY,
    DEFAULT_SDK_PORT_START,
    DEFAULT_STOP_TIMEOUT,
    LocalProcessPodManager,
    ProcessInfo,
    ProcessState,
    SdkPortAllocator,
    _inject_token_into_url,
)
from volundr.domain.models import (
    GitSource,
    LocalMountSource,
    MountMapping,
    PodSpecAdditions,
    Session,
    SessionSpec,
    SessionStatus,
)
from volundr.domain.ports import PodStartResult
from volundr.domain.services.session import SessionCapacityError

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def tmp_workspaces(tmp_path: Path) -> Path:
    """Temporary workspaces directory."""
    d = tmp_path / "workspaces"
    d.mkdir()
    return d


@pytest.fixture
def tmp_state_file(tmp_path: Path) -> Path:
    """Temporary state file path."""
    return tmp_path / "forge-state.json"


@pytest.fixture
def manager(tmp_workspaces: Path, tmp_state_file: Path) -> LocalProcessPodManager:
    """Create a LocalProcessPodManager with temp directories."""
    return LocalProcessPodManager(
        workspaces_dir=str(tmp_workspaces),
        claude_binary="/usr/bin/fake-claude",
        max_concurrent=16,
        sdk_port_start=DEFAULT_SDK_PORT_START,
        stop_timeout=DEFAULT_STOP_TIMEOUT,
        state_file=str(tmp_state_file),
    )


@pytest.fixture
def git_session() -> Session:
    """A session with git source."""
    return Session(
        id=uuid4(),
        name="test-session",
        source=GitSource(
            repo="https://github.com/niuulabs/example",
            branch="feat/test",
            base_branch="main",
        ),
    )


@pytest.fixture
def local_mount_session(tmp_path: Path) -> Session:
    """A session with local mount source."""
    mount_dir = tmp_path / "project"
    mount_dir.mkdir()
    return Session(
        id=uuid4(),
        name="local-session",
        source=LocalMountSource(
            paths=[
                MountMapping(
                    host_path=str(mount_dir),
                    mount_path="/workspace/project",
                ),
            ],
        ),
    )


@pytest.fixture
def default_spec() -> SessionSpec:
    """A minimal SessionSpec for tests."""
    return SessionSpec(
        values={"session": {"systemPrompt": "You are a helpful assistant."}},
        pod_spec=PodSpecAdditions(),
    )


WS = Path("/tmp/ws")


def _mock_provision(mgr: LocalProcessPodManager) -> patch:
    """Patch _provision_workspace to return a fake path."""
    return patch.object(
        mgr,
        "_provision_workspace",
        new_callable=AsyncMock,
        return_value=WS,
    )


def _reaped_child_pid() -> int:
    """Spawn and immediately reap a child so its pid is guaranteed dead."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _mock_spawn(
    mgr: LocalProcessPodManager,
    pid: int = 42,
    side_effect: Exception | None = None,
) -> patch:
    """Patch _spawn_skuld to return a fake PID."""
    kwargs: dict = {"new_callable": AsyncMock}
    if side_effect:
        kwargs["side_effect"] = side_effect
    else:
        kwargs["return_value"] = pid
    return patch.object(mgr, "_spawn_skuld", **kwargs)


# ------------------------------------------------------------------
# Init tests
# ------------------------------------------------------------------


class TestInit:
    def test_coerces_string_kwargs_from_env_backed_config(self, tmp_path: Path) -> None:
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=True):
            mgr = LocalProcessPodManager(
                workspaces_dir=str(tmp_path / "workspaces"),
                claude_binary="claude",
                max_concurrent="4",
                sdk_port_start="9200",
                stop_timeout="15",
                state_file=str(tmp_path / "state.json"),
                allowed_mount_prefixes="/repo-a,/repo-b",
            )

            assert mgr._max_concurrent == 4
            assert mgr._stop_timeout == 15
            assert mgr._port_allocator.allocate() == 9200
            assert mgr._allowed_mount_prefixes == ["/repo-a", "/repo-b"]


class TestSkuldEnv:
    @pytest.mark.parametrize("builder", ["_build_env", "_session_env"])
    @pytest.mark.parametrize("overlay", ["none", "env", "envVars"])
    def test_capture_secret_never_enters_session_environment(
        self, builder: str, overlay: str
    ) -> None:
        parent = {
            "FORGE_EXP_CAPTURE_SECRET": "test-only-parent-capture-secret",
            "PARENT_SETTING": "retained",
        }
        values = {
            "env": {"SESSION_SETTING": "retained", "OPENAI_API_KEY": "test-only-engine-auth"},
        }
        if overlay == "env":
            values["env"]["FORGE_EXP_CAPTURE_SECRET"] = "test-only-spec-capture-secret"
        if overlay == "envVars":
            values["envVars"] = [
                {
                    "name": "FORGE_EXP_CAPTURE_SECRET",
                    "value": "test-only-contributor-capture-secret",
                }
            ]
        before = deepcopy(values)
        spec = SessionSpec(values=values, pod_spec=PodSpecAdditions())
        with patch.dict(os.environ, parent, clear=True):
            env = getattr(LocalProcessPodManager, builder)(spec, Path("/tmp/ws"))
            assert "FORGE_EXP_CAPTURE_SECRET" not in env
            assert env["SESSION_SETTING"] == "retained"
            assert env["OPENAI_API_KEY"] == "test-only-engine-auth"
            if builder == "_build_env":
                assert env["PARENT_SETTING"] == "retained"
            assert dict(os.environ) == parent
        assert spec.values == before

    def test_build_env_does_not_inherit_parent_permission_overrides(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Skuld permission knobs must come from session config, not parent env."""
        monkeypatch.setenv("SKULD__SKIP_PERMISSIONS", "true")
        monkeypatch.setenv("SKULD__APPROVAL_POLICY", "never")
        monkeypatch.setenv("SKULD__SANDBOX", "danger-full-access")

        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert "SKULD__SKIP_PERMISSIONS" not in env
        assert "SKULD__APPROVAL_POLICY" not in env
        assert "SKULD__SANDBOX" not in env

    def test_build_env_includes_broker_overrides(self) -> None:
        """Broker values from session definitions are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": False,
                    "approvalPolicy": "untrusted",
                    "sandbox": "workspace-write",
                    "agentTeams": True,
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__CLI_TYPE"] == "codex-ws"
        assert env["SKULD__TRANSPORT"] == "sdk"
        assert env["SKULD__TRANSPORT_ADAPTER"] == (
            "skuld.transports.codex_ws.CodexWebSocketTransport"
        )
        assert env["SKULD__SKIP_PERMISSIONS"] == "false"
        assert env["SKULD__APPROVAL_POLICY"] == "untrusted"
        assert env["SKULD__SANDBOX"] == "workspace-write"
        assert env["SKULD__AGENT_TEAMS"] == "true"

    def test_build_env_maps_claude_permission_mode_override(self) -> None:
        """A definition can override the node's claude_permission_mode for its sessions."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "claude",
                    "transport": "tmux-interactive",
                    "skipPermissions": True,
                    "claudePermissionMode": "auto",
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__CLAUDE_PERMISSION_MODE"] == "auto"

    def test_build_env_leaves_claude_permission_mode_to_the_node(self, monkeypatch) -> None:
        """Without an override the broker reads the node config file's setting."""
        monkeypatch.delenv("SKULD__CLAUDE_PERMISSION_MODE", raising=False)
        spec = SessionSpec(
            values={"broker": {"cliType": "claude", "skipPermissions": True}},
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert "SKULD__CLAUDE_PERMISSION_MODE" not in env

    def test_build_env_includes_telegram_runtime_channel(self) -> None:
        """Broker telegram values are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "bot-token",
                        "chatId": "chat-123",
                        "notifyOnly": True,
                        "topicMode": "topic_per_session",
                    }
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__TELEGRAM__ENABLED"] == "true"
        assert env["SKULD__TELEGRAM__BOT_TOKEN"] == "bot-token"
        assert env["SKULD__TELEGRAM__CHAT_ID"] == "chat-123"
        assert env["SKULD__TELEGRAM__NOTIFY_ONLY"] == "true"
        assert env["SKULD__TELEGRAM__TOPIC_MODE"] == "topic_per_session"

    def test_build_env_includes_telegram_thread_override(self) -> None:
        spec = SessionSpec(
            values={
                "broker": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "bot-token",
                        "chatId": "chat-123",
                        "notifyOnly": True,
                        "topicMode": "fixed_topic",
                        "messageThreadId": 77,
                    }
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__TELEGRAM__TOPIC_MODE"] == "fixed_topic"
        assert env["SKULD__TELEGRAM__MESSAGE_THREAD_ID"] == "77"

    def test_build_env_includes_resume_session_id(self) -> None:
        """Imported sessions thread their native CLI session id to Skuld."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "claude",
                    "resumeSessionId": "2e877b9f-4b8a-4d46-8f00-03f6163addd5",
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__SESSION__RESUME_SESSION_ID"] == ("2e877b9f-4b8a-4d46-8f00-03f6163addd5")

    def test_build_env_omits_resume_session_id_when_absent(self) -> None:
        spec = SessionSpec(
            values={"broker": {"cliType": "claude"}},
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert "SKULD__SESSION__RESUME_SESSION_ID" not in env

    def test_build_env_includes_localized_mcp_servers(self) -> None:
        spec = SessionSpec(
            values={
                "mcpServers": [
                    {
                        "name": "mimir-local",
                        "type": "stdio",
                        "command": "python3",
                        "args": ["-m", "mimir", "mcp", "--path", "/mimir/local", "--name", "local"],
                    }
                ]
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        servers = json.loads(env["SKULD__MCP_SERVERS"])
        assert servers[0]["args"] == [
            "-m",
            "mimir",
            "mcp",
            "--path",
            "/tmp/ws/.mimir-local",
            "--name",
            "local",
        ]


# ------------------------------------------------------------------
# SdkPortAllocator tests
# ------------------------------------------------------------------


class TestSdkPortAllocator:
    """Tests for the SDK port allocator."""

    def test_allocate_returns_start_port(self) -> None:
        """First allocation should return the start port when free."""
        alloc = SdkPortAllocator(start_port=59100)
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=True):
            port = alloc.allocate()
        assert port == 59100

    def test_allocate_increments(self) -> None:
        """Successive allocations return incrementing ports."""
        alloc = SdkPortAllocator(start_port=59100)
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=True):
            p1 = alloc.allocate()
            p2 = alloc.allocate()
        assert p1 == 59100
        assert p2 == 59101

    def test_release_frees_port(self) -> None:
        """Released ports are removed from the allocated set."""
        alloc = SdkPortAllocator(start_port=59100)
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=True):
            port = alloc.allocate()
        assert port in alloc.allocated
        alloc.release(port)
        assert port not in alloc.allocated

    def test_allocate_skips_occupied_port(self) -> None:
        """Ports reported as not free are skipped."""
        alloc = SdkPortAllocator(start_port=59100)
        # First port busy, second free
        with patch.object(
            SdkPortAllocator,
            "_is_port_free",
            side_effect=[False, True],
        ):
            port = alloc.allocate()
        assert port == 59101

    def test_allocate_skips_already_allocated(self) -> None:
        """Already-allocated ports are skipped."""
        alloc = SdkPortAllocator(start_port=59100)
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=True):
            p1 = alloc.allocate()
            p2 = alloc.allocate()
        assert p1 != p2

    def test_allocate_raises_when_exhausted(self) -> None:
        """Raises RuntimeError if no free port found in range."""
        alloc = SdkPortAllocator(start_port=59100)
        with patch.object(SdkPortAllocator, "_is_port_free", return_value=False):
            with pytest.raises(RuntimeError, match="No free SDK port found"):
                alloc.allocate()

    def test_is_port_free_real(self) -> None:
        """Integration test: _is_port_free works with real sockets."""
        # Pick a high ephemeral port that is very likely free
        assert SdkPortAllocator._is_port_free(59999) is True

    def test_is_port_free_detects_loopback_listener(self) -> None:
        """Loopback listeners must block reuse of the same port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen(1)
            port = sock.getsockname()[1]
            assert SdkPortAllocator._is_port_free(port) is False

    def test_release_nonexistent_port_is_noop(self) -> None:
        """Releasing a port that was never allocated is a no-op."""
        alloc = SdkPortAllocator(start_port=59100)
        alloc.release(59999)  # Should not raise


# ------------------------------------------------------------------
# Token injection tests
# ------------------------------------------------------------------


class TestTokenInjection:
    """Tests for _inject_token_into_url."""

    def test_github_url(self) -> None:
        url = _inject_token_into_url("https://github.com/org/repo", "tok123")
        assert url == "https://x-access-token:tok123@github.com/org/repo"

    def test_gitlab_url(self) -> None:
        url = _inject_token_into_url("https://gitlab.com/org/repo", "tok123")
        assert url == "https://x-access-token:tok123@gitlab.com/org/repo"

    def test_no_token(self) -> None:
        url = _inject_token_into_url("https://github.com/org/repo", "")
        assert url == "https://github.com/org/repo"

    def test_unknown_host(self) -> None:
        url = _inject_token_into_url("https://bitbucket.org/org/repo", "tok123")
        assert url == "https://bitbucket.org/org/repo"

    def test_ssh_url_unchanged(self) -> None:
        url = _inject_token_into_url("git@github.com:org/repo.git", "tok123")
        assert url == "git@github.com:org/repo.git"


# ------------------------------------------------------------------
# ProcessInfo serialization tests
# ------------------------------------------------------------------


class TestProcessInfo:
    """Tests for ProcessInfo serialization."""

    def test_to_dict(self) -> None:
        info = ProcessInfo(
            session_id="abc",
            pid=1234,
            port=9100,
            workspace="/tmp/ws",
            state=ProcessState.RUNNING,
        )
        d = info.to_dict()
        assert d["session_id"] == "abc"
        assert d["pid"] == 1234
        assert d["state"] == "running"

    def test_from_dict_roundtrip(self) -> None:
        info = ProcessInfo(
            session_id="abc",
            pid=1234,
            port=9100,
            workspace="/tmp/ws",
            state=ProcessState.RUNNING,
            error=None,
            managed_by="local_process",
        )
        restored = ProcessInfo.from_dict(info.to_dict())
        assert restored.session_id == info.session_id
        assert restored.pid == info.pid
        assert restored.port == info.port
        assert restored.state == info.state
        assert restored.managed_by == info.managed_by

    def test_from_dict_preserves_non_local_owner(self) -> None:
        info = ProcessInfo.from_dict({"session_id": "x", "managed_by": "external"})
        assert info.managed_by == "external"

    def test_from_dict_defaults(self) -> None:
        info = ProcessInfo.from_dict({"session_id": "x"})
        assert info.pid is None
        assert info.port is None
        assert info.state == ProcessState.STOPPED
        assert info.managed_by == "local_process"


# ------------------------------------------------------------------
# Workspace provisioning tests
# ------------------------------------------------------------------


class TestWorkspaceProvisioning:
    """Tests for workspace creation and setup."""

    async def test_creates_workspace_dir(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """Workspace directory is created for the session."""
        with patch.object(manager, "_clone_repo", new_callable=AsyncMock):
            workspace = await manager._provision_workspace(git_session, default_spec)
        assert workspace.exists()
        assert workspace == tmp_workspaces / str(git_session.id)

    async def test_does_not_write_claude_md(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """Prompts are delivered via the transport — CLAUDE.md is never written
        (it used to clobber real project files and bleed into later sessions)."""
        with patch.object(manager, "_clone_repo", new_callable=AsyncMock):
            workspace = await manager._provision_workspace(git_session, default_spec)
        assert not (workspace / "CLAUDE.md").exists()

    async def test_local_mount_path_outside_allowed_prefixes_is_rejected(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
        default_spec: SessionSpec,
        tmp_path: Path,
    ) -> None:
        """Mini-mode local_path workspaces obey the allowed mount prefix policy."""
        allowed = tmp_path / "allowed"
        outside = tmp_path / "outside"
        allowed.mkdir()
        outside.mkdir()
        manager = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            allowed_mount_prefixes=[str(allowed)],
        )
        session = Session(
            id=uuid4(),
            name="denied-local-mount",
            source=LocalMountSource(local_path=str(outside)),
        )

        with pytest.raises(RuntimeError, match="allowed mount prefix"):
            await manager._provision_workspace(session, default_spec)

    async def test_local_mount_path_under_allowed_prefix_is_used(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
        default_spec: SessionSpec,
        tmp_path: Path,
    ) -> None:
        allowed = tmp_path / "allowed"
        workspace_dir = allowed / "project"
        workspace_dir.mkdir(parents=True)
        manager = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            allowed_mount_prefixes=[str(allowed)],
        )
        session = Session(
            id=uuid4(),
            name="allowed-local-mount",
            source=LocalMountSource(local_path=str(workspace_dir)),
        )

        workspace = await manager._provision_workspace(session, default_spec)

        assert workspace == workspace_dir

    async def test_git_source_local_path_uses_directory_directly(
        self,
        manager: LocalProcessPodManager,
        default_spec: SessionSpec,
        tmp_path: Path,
    ) -> None:
        """A local path in GitSource.repo is treated like mini/local mode."""
        project = tmp_path / "project"
        project.mkdir()
        session = Session(
            id=uuid4(),
            name="local-git-path",
            source=GitSource(repo=str(project), branch="feat/local"),
        )

        with patch.object(manager, "_clone_repo", new_callable=AsyncMock) as clone_repo:
            workspace = await manager._provision_workspace(session, default_spec)

        clone_repo.assert_not_called()
        assert workspace == project.resolve()
        assert not (manager._workspaces_dir / str(session.id)).exists()
        # The user's real project directory is left untouched.
        assert not (project / "CLAUDE.md").exists()

    async def test_does_not_write_claude_md_with_initial_prompt(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
    ) -> None:
        """Even with prompts configured, CLAUDE.md is not written — prompts go
        through the transport (system_prompt option / send_message)."""
        spec = SessionSpec(
            values={
                "session": {
                    "systemPrompt": "System.",
                    "initialPrompt": "Do the thing.",
                },
            },
            pod_spec=PodSpecAdditions(),
        )
        with patch.object(manager, "_clone_repo", new_callable=AsyncMock):
            workspace = await manager._provision_workspace(git_session, spec)
        assert not (workspace / "CLAUDE.md").exists()

    async def test_no_claude_md_when_no_prompts(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
    ) -> None:
        """No CLAUDE.md is written when there are no prompts."""
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
        with patch.object(manager, "_clone_repo", new_callable=AsyncMock):
            workspace = await manager._provision_workspace(git_session, spec)
        assert not (workspace / "CLAUDE.md").exists()

    async def test_local_mount_creates_symlinks(
        self,
        manager: LocalProcessPodManager,
        local_mount_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """Local mount source creates symlinks in workspace."""
        workspace = await manager._provision_workspace(local_mount_session, default_spec)
        source = local_mount_session.source
        assert isinstance(source, LocalMountSource)
        link = workspace / Path(source.paths[0].host_path).name
        assert link.is_symlink()

    async def test_local_mount_skips_nonexistent(
        self,
        manager: LocalProcessPodManager,
        default_spec: SessionSpec,
    ) -> None:
        """Non-existent mount paths are skipped."""
        session = Session(
            id=uuid4(),
            name="missing-mount",
            source=LocalMountSource(
                paths=[
                    MountMapping(
                        host_path="/nonexistent/path",
                        mount_path="/workspace/x",
                    ),
                ],
            ),
        )
        workspace = await manager._provision_workspace(session, default_spec)
        # Should not raise, just skip
        assert workspace.exists()


class TestAllowedMountPrefixes:
    """Tests for mount prefix validation."""

    def test_all_allowed_when_no_prefixes(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        assert manager._is_allowed_mount(Path("/any/path")) is True

    def test_allowed_prefix_match(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            allowed_mount_prefixes=["/home/user/projects"],
        )
        assert mgr._is_allowed_mount(Path("/home/user/projects/repo")) is True

    def test_disallowed_prefix(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            allowed_mount_prefixes=["/home/user/projects"],
        )
        assert mgr._is_allowed_mount(Path("/etc/secrets")) is False

    def test_prefix_traversal_rejected(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """Path with prefix-similar name is rejected (no traversal)."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            allowed_mount_prefixes=["/home/user/projects"],
        )
        # This path starts with the prefix string but is a sibling dir
        assert mgr._is_allowed_mount(Path("/home/user/projects-evil/malicious")) is False


# ------------------------------------------------------------------
# Git clone tests
# ------------------------------------------------------------------


class TestGitClone:
    """Tests for git clone and branch checkout."""

    @pytest.mark.parametrize("detached", [False, True])
    async def test_restart_preserves_existing_checkout(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_path: Path,
        detached: bool,
    ) -> None:
        """Restart keeps local commits, the index, and uncommitted files offline."""

        def git(*args: str) -> str:
            return subprocess.run(
                ["git", *args], check=True, capture_output=True, text=True
            ).stdout.strip()

        remote = tmp_path / "remote"
        git("init", "--initial-branch=main", str(remote))
        (remote / "tracked.txt").write_text("original\n")
        git("-C", str(remote), "add", "tracked.txt")
        git(
            "-C",
            str(remote),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Initial commit",
        )
        git_session.source = GitSource(repo=remote.as_uri(), branch="main")
        workspace = await manager._provision_workspace(git_session, default_spec)
        repo = workspace / "repo"
        git("-C", str(repo), "checkout", "-b", "session-work")
        git(
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--allow-empty",
            "-m",
            "Local work",
        )
        if detached:
            git("-C", str(repo), "checkout", "--detach")
        (repo / "tracked.txt").write_text("staged\n")
        git("-C", str(repo), "add", "tracked.txt")
        (repo / "tracked.txt").write_text("unstaged\n")
        (repo / "untracked.txt").write_text("unsaved work\n")
        before = {
            path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
        }
        remote.rename(tmp_path / "offline")

        assert await manager._provision_workspace(git_session, default_spec) == workspace

        after = {
            path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()
        }
        assert after == before

    @pytest.mark.parametrize("git_metadata", [False, True])
    async def test_restart_rejects_incomplete_checkout_without_deleting_files(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        git_metadata: bool,
    ) -> None:
        repo = manager._workspaces_dir / str(git_session.id) / "repo"
        repo.mkdir(parents=True)
        if git_metadata:
            (repo / ".git").mkdir()
        work = repo / "unsaved.txt"
        work.write_text("keep this work\n")

        with pytest.raises(RuntimeError, match="Git clone failed|Existing git checkout"):
            await manager._provision_workspace(git_session, default_spec)

        assert work.read_text() == "keep this work\n"

    async def test_clone_calls_git(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """_clone_repo calls git clone with correct args."""
        source = GitSource(
            repo="https://github.com/org/repo",
            branch="feat",
            base_branch="main",
        )
        workspace = manager._workspaces_dir / "test-clone"
        workspace.mkdir(parents=True)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        checkout_proc = AsyncMock()
        checkout_proc.returncode = 0
        checkout_proc.communicate = AsyncMock(return_value=(b"", b""))

        spec = SessionSpec(
            values={
                "git": {
                    "cloneUrl": "https://x-access-token:tok123@github.com/org/repo",
                    "repoUrl": "https://github.com/org/repo",
                    "branch": "feat",
                    "baseBranch": "main",
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = [
                mock_proc,
                checkout_proc,
                checkout_proc,
                checkout_proc,
                checkout_proc,
                checkout_proc,
            ]
            await manager._clone_repo(source, workspace, spec)

        clone_call = mock_exec.call_args_list[0]
        args = clone_call[0]
        assert "git" in args
        assert "clone" in args
        assert "--no-single-branch" in args
        assert "x-access-token:tok123@github.com/org/repo" in args[5]
        # The branch existed, so the workspace records the session's base branch.
        assert mock_exec.call_args_list[3][0][-3:] == ("config", "niuu.baseBranch", "main")
        assert mock_exec.call_args_list[4][0][-4:] == (
            "remote",
            "set-url",
            "origin",
            "https://github.com/org/repo",
        )
        assert mock_exec.call_args_list[5][0][-5:] == (
            "remote",
            "set-url",
            "--push",
            "origin",
            "https://x-access-token:tok123@github.com/org/repo",
        )

    async def test_clone_failure_sanitizes_token(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Token is stripped from git clone error messages."""
        source = GitSource(
            repo="https://github.com/org/repo",
        )
        workspace = manager._workspaces_dir / "token-leak"
        workspace.mkdir(parents=True)
        spec = SessionSpec(
            values={"git_token": "ghp_SECRET"},
            pod_spec=PodSpecAdditions(),
        )

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        stderr_msg = (
            b"fatal: could not read from https://x-access-token:ghp_SECRET@github.com/org/repo"
        )
        mock_proc.communicate = AsyncMock(
            return_value=(b"", stderr_msg),
        )

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with pytest.raises(RuntimeError, match="Git clone failed") as exc:
                await manager._clone_repo(source, workspace, spec)

        assert "ghp_SECRET" not in str(exc.value)
        assert "***" in str(exc.value)

    async def test_clone_failure_raises(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Git clone failure raises RuntimeError."""
        source = GitSource(repo="https://github.com/org/repo")
        workspace = manager._workspaces_dir / "fail-clone"
        workspace.mkdir(parents=True)
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: error"))

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(RuntimeError, match="Git clone failed"),
        ):
            await manager._clone_repo(source, workspace, spec)

    async def test_branch_fallback_to_base(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Falls back to base_branch and creates the feature branch locally."""
        source = GitSource(
            repo="https://github.com/org/repo",
            branch="feat/missing",
            base_branch="main",
        )
        workspace = manager._workspaces_dir / "fallback"
        workspace.mkdir(parents=True)
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        clone_proc = AsyncMock()
        clone_proc.returncode = 0
        clone_proc.communicate = AsyncMock(return_value=(b"", b""))

        # Feature branch missing on remote.
        fail_proc = AsyncMock()
        fail_proc.returncode = 1
        fail_proc.communicate = AsyncMock(return_value=(b"", b""))

        head_proc = AsyncMock()
        head_proc.returncode = 0
        head_proc.communicate = AsyncMock(return_value=(b"origin/main\n", b""))

        ok_proc = AsyncMock()
        ok_proc.returncode = 0
        ok_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = [
                clone_proc,
                fail_proc,
                head_proc,
                ok_proc,
                ok_proc,
                ok_proc,
                ok_proc,
            ]
            await manager._clone_repo(source, workspace, spec)

        assert mock_exec.call_args_list[4][0][-3:] == ("checkout", "-b", "feat/missing")
        assert mock_exec.call_args_list[5][0][-3:] == ("config", "niuu.baseBranch", "main")

    async def test_failing_to_record_the_base_branch_raises(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """A workspace that cannot record its base branch does not start silently without it."""
        source = GitSource(repo="https://github.com/org/repo", branch="dev", base_branch="dev")
        workspace = manager._workspaces_dir / "no-base"
        workspace.mkdir(parents=True)
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        ok_proc = AsyncMock()
        ok_proc.returncode = 0
        ok_proc.communicate = AsyncMock(return_value=(b"", b""))
        config_proc = AsyncMock()
        config_proc.returncode = 1
        config_proc.communicate = AsyncMock(return_value=(b"", b"error: could not lock config"))

        with (
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec,
            pytest.raises(RuntimeError, match="could not lock config"),
        ):
            # clone, rev-parse origin/dev, checkout dev, then the config write fails.
            mock_exec.side_effect = [ok_proc, ok_proc, ok_proc, config_proc]
            await manager._clone_repo(source, workspace, spec)


# ------------------------------------------------------------------
# Process spawning tests
# ------------------------------------------------------------------


class TestProcessSpawning:
    """Tests for Skuld process spawning."""

    def test_resolve_skuld_command_from_source(self, manager: LocalProcessPodManager) -> None:
        assert manager._resolve_skuld_command() == [sys.executable, "-m", "skuld"]

    def test_resolve_skuld_command_from_nuitka_binary(
        self, manager: LocalProcessPodManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(local_process_mod, "__compiled__", object(), raising=False)
        monkeypatch.setattr(sys, "executable", "/tmp/niuu")

        assert manager._resolve_skuld_command() == ["/tmp/niuu", "platform", "skuld"]

    async def test_shared_workspace_spawn_preserves_both_brokers_logs(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """A second launch must retain history and the first child's open log stream."""
        workspace = tmp_workspaces / "shared-project"
        workspace.mkdir()
        log_path = workspace / ".skuld.log"
        log_path.write_text("existing diagnostics\n", encoding="utf-8")
        child_descriptors: list[int] = []

        async def inherit_log_stream(*_args, **kwargs):
            assert kwargs["stdout"] is kwargs["stderr"]
            # A spawned child inherits the open file description, including
            # O_APPEND. Keep that descriptor alive after the parent closes its copy.
            descriptor = os.dup(kwargs["stdout"].fileno())
            child_descriptors.append(descriptor)
            os.write(descriptor, f"broker {len(child_descriptors)} started\n".encode())
            return MagicMock(pid=40 + len(child_descriptors))

        second_session = git_session.model_copy(update={"id": uuid4(), "name": "second"})
        try:
            with (
                patch.object(manager, "_resolve_claude_binary", return_value="/fixture/claude"),
                patch("asyncio.create_subprocess_exec", side_effect=inherit_log_stream),
            ):
                first_pid = await manager._spawn_skuld(git_session, default_spec, workspace, 9100)
                assert log_path.read_text() == "existing diagnostics\nbroker 1 started\n"
                second_pid = await manager._spawn_skuld(
                    second_session, default_spec, workspace, 9101
                )
            assert (first_pid, second_pid) == (41, 42)
            assert log_path.read_text() == (
                "existing diagnostics\nbroker 1 started\nbroker 2 started\n"
            )
            # The already-running first broker continues at the end, preserving
            # the second broker's newer output rather than overwriting it.
            os.write(child_descriptors[0], b"broker 1 continued\n")
            assert log_path.read_text() == (
                "existing diagnostics\nbroker 1 started\nbroker 2 started\nbroker 1 continued\n"
            )
        finally:
            for descriptor in child_descriptors:
                os.close(descriptor)

    async def test_spawn_skuld_returns_pid(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """_spawn_skuld returns the PID of the subprocess."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc),
        ):
            pid = await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        assert pid == 42

    async def test_spawn_skuld_points_the_broker_at_the_real_home(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Local brokers get HOME as their session home and a per-session staging dir."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)
        monkeypatch.setenv("HOME", "/home/local-user")

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=MagicMock(pid=7),
            ) as spawn,
        ):
            await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        env = spawn.call_args.kwargs["env"]
        assert env["SKULD__HOME_DIR"] == "/home/local-user"
        assert env["SKULD__PRESENTED_FILES_DIR"] == str(
            tmp_workspaces / ".forge-presented" / str(git_session.id)
        )

    async def test_spawn_skuld_hands_the_broker_the_pinned_persona(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        """The persona document rides beside the request's prompt; Skuld composes them."""
        persona = {
            "schema_version": 1,
            "id": "reviewer",
            "revision": "content-0123456789abcdef",
            "definition": {"name": "reviewer", "permission_mode": "read_only"},
        }
        spec = SessionSpec(
            values={"session": {"persona": persona, "systemPrompt": "Focus on migrations."}},
            pod_spec=PodSpecAdditions(),
        )
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=MagicMock(pid=7),
            ) as spawn,
        ):
            await manager._spawn_skuld(git_session, spec, workspace, 9100)

        env = spawn.call_args.kwargs["env"]
        assert json.loads(env["SKULD__SESSION__PERSONA"]) == persona
        assert env["SKULD__SESSION__SYSTEM_PROMPT"] == "Focus on migrations."

    async def test_spawn_skuld_enables_workflow_steps_only_for_a_step_host(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)
        step_host = SessionSpec(
            values={"workflowSteps": MEMBER_STEPS_VALUES}, pod_spec=PodSpecAdditions()
        )
        root = SessionSpec(values={"workflowSteps": ROOT_STEPS_VALUES}, pod_spec=PodSpecAdditions())
        envs = []

        for spec in (default_spec, step_host, root):
            with (
                patch.object(
                    manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"
                ),
                patch(
                    "asyncio.create_subprocess_exec",
                    new_callable=AsyncMock,
                    return_value=MagicMock(pid=7),
                ) as spawn,
            ):
                await manager._spawn_skuld(git_session, spec, workspace, 9100)
            envs.append(spawn.call_args.kwargs["env"])

        assert not [name for name in envs[0] if name.startswith("SKULD__WORKFLOW_STEPS__")]
        assert MEMBER_STEPS_ENV.items() <= envs[1].items()
        assert ROOT_STEPS_ENV.items() <= envs[2].items()
        assert "SKULD__WORKFLOW_STEPS__ENABLED" not in envs[2]

    async def test_spawn_skuld_has_no_persona_without_one(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=MagicMock(pid=7),
            ) as spawn,
        ):
            await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        assert "SKULD__SESSION__PERSONA" not in spawn.call_args.kwargs["env"]

    async def test_spawn_sets_sdk_url_arg(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """The --sdk-url argument is passed to the Claude binary."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(
                manager,
                "_resolve_skuld_command",
                return_value=["python", "-m", "skuld"],
            ),
            patch.object(
                manager,
                "_resolve_claude_binary",
                return_value="/usr/bin/fake-claude",
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        call_args = mock_exec.call_args[0]
        assert call_args == ("python", "-m", "skuld")
        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__PORT"] == "9100"
        assert env["SKULD__SESSION__ID"] == str(git_session.id)

    async def test_spawn_renders_proxy_room_role_source(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """This pod is reached exclusively through niuu.session_proxy on the
        process backend, never a Kubernetes-style Gateway/ext_authz
        boundary — the one backend where the proxy's own room-role header
        is the correct source of truth for a missing header
        (skuld.config.WsAuthConfig.room_role_source; every other backend
        keeps the "deployment" default and renders nothing here)."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(manager, "_resolve_skuld_command", return_value=["python", "-m", "skuld"]),
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__WS_AUTH__ROOM_ROLE_SOURCE"] == "proxy"

    async def test_spawn_closes_log_file(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_workspaces: Path,
    ) -> None:
        """Log file handle is closed after subprocess is spawned."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        mock_proc = MagicMock()
        mock_proc.pid = 42
        mock_file = MagicMock()

        with (
            patch.object(
                manager,
                "_resolve_claude_binary",
                return_value="/usr/bin/fake-claude",
            ),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ),
            patch("pathlib.Path.open", return_value=mock_file),
        ):
            await manager._spawn_skuld(git_session, default_spec, workspace, 9100)

        mock_file.close.assert_called_once()

    def test_build_env_includes_api_key(self) -> None:
        """Environment includes ANTHROPIC_API_KEY when provided."""
        spec = SessionSpec(
            values={"anthropic_api_key": "sk-test"},
            pod_spec=PodSpecAdditions(),
        )
        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))
        assert env["ANTHROPIC_API_KEY"] == "sk-test"

    def test_build_env_includes_extra(self) -> None:
        """Extra env vars from spec are included."""
        spec = SessionSpec(
            values={"env": {"FOO": "bar", "NUM": 42}},
            pod_spec=PodSpecAdditions(),
        )
        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))
        assert env["FOO"] == "bar"
        assert env["NUM"] == "42"

    def test_build_env_applies_contributed_env_vars(self) -> None:
        """The integrations contributor's envVars list (SKULD__CLAUDE_AUTH, the
        model gateway URL) reaches single-host sessions, not only Helm ones."""
        spec = SessionSpec(
            values={
                "env": {"FOO": "bar"},
                "envVars": [
                    {
                        "name": "SKULD__MODEL_GATEWAY__URL",
                        "value": "http://niuu:8080/api/v1/bifrost",
                    },
                    {"name": "SKULD__CLAUDE_AUTH", "value": "api_key"},
                ],
            },
            pod_spec=PodSpecAdditions(),
        )
        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))
        assert env["SKULD__MODEL_GATEWAY__URL"] == "http://niuu:8080/api/v1/bifrost"
        assert env["SKULD__CLAUDE_AUTH"] == "api_key"
        assert env["FOO"] == "bar"

    def test_build_env_rejects_nameless_env_vars(self) -> None:
        spec = SessionSpec(values={"envVars": [{"value": "x"}]}, pod_spec=PodSpecAdditions())
        with pytest.raises(ValueError, match="need a name"):
            LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

    def test_build_env_sets_structured_workspace_dir(self) -> None:
        """The Skuld workspace is set through structured broker config."""
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))
        assert env["SKULD__SESSION__WORKSPACE_DIR"] == "/tmp/ws"

    def test_build_env_includes_broker_overrides(self) -> None:
        """Broker values from session definitions are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": False,
                    "approvalPolicy": "untrusted",
                    "sandbox": "workspace-write",
                    "agentTeams": True,
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__CLI_TYPE"] == "codex-ws"
        assert env["SKULD__TRANSPORT"] == "sdk"
        assert env["SKULD__TRANSPORT_ADAPTER"] == (
            "skuld.transports.codex_ws.CodexWebSocketTransport"
        )
        assert env["SKULD__SKIP_PERMISSIONS"] == "false"
        assert env["SKULD__APPROVAL_POLICY"] == "untrusted"
        assert env["SKULD__SANDBOX"] == "workspace-write"
        assert env["SKULD__AGENT_TEAMS"] == "true"

    def test_build_env_includes_telegram_runtime_channel(self) -> None:
        """Broker telegram values are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "telegram": {
                        "enabled": True,
                        "botToken": "bot-token",
                        "chatId": "chat-123",
                        "notifyOnly": False,
                    }
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))

        assert env["SKULD__TELEGRAM__ENABLED"] == "true"
        assert env["SKULD__TELEGRAM__BOT_TOKEN"] == "bot-token"
        assert env["SKULD__TELEGRAM__CHAT_ID"] == "chat-123"
        assert env["SKULD__TELEGRAM__NOTIFY_ONLY"] == "false"

    async def test_spawn_skuld_preserves_broker_transport_overrides(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        """Session-definition broker config must survive mini-mode spawn."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": False,
                    "approvalPolicy": "untrusted",
                    "sandbox": "workspace-write",
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            await manager._spawn_skuld(git_session, spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__CLI_TYPE"] == "codex-ws"
        assert env["SKULD__TRANSPORT_ADAPTER"] == (
            "skuld.transports.codex_ws.CodexWebSocketTransport"
        )
        assert env["SKULD__SKIP_PERMISSIONS"] == "false"
        assert env["SKULD__APPROVAL_POLICY"] == "untrusted"
        assert env["SKULD__SANDBOX"] == "workspace-write"
        assert "SKULD__CLI_BINARY" not in env

    async def test_spawn_skuld_does_not_default_to_skip_permissions(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        """Local Codex sessions should delegate permission behavior to the CLI config."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                }
            },
            pod_spec=PodSpecAdditions(),
        )

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ) as mock_exec:
            await manager._spawn_skuld(git_session, spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__CLI_TYPE"] == "codex-ws"
        assert "SKULD__SKIP_PERMISSIONS" not in env
        assert "SKULD__APPROVAL_POLICY" not in env
        assert "SKULD__SANDBOX" not in env
        assert "SKULD__CLI_BINARY" not in env

    async def test_spawn_skuld_applies_pod_spec_env_without_enabling_room_or_mesh(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        """Contributor env reaches the broker; a plain session gets no room or mesh wiring."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)
        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(env=({"name": "CONTRIBUTED", "value": "yes"},)),
        )
        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await manager._spawn_skuld(git_session, spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["CONTRIBUTED"] == "yes"
        assert "SKULD__ROOM__ENABLED" not in env
        assert "SKULD__MESH__ADAPTERS" not in env

    async def test_spawn_skuld_uses_definition_default_model_without_hardcoded_claude_fallback(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        """The session model env should come from merged spec values."""
        session = Session(id=uuid4(), name="codex-test", model="")
        workspace = tmp_workspaces / str(session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(values={"model": "gpt-5.5"}, pod_spec=PodSpecAdditions())

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await manager._spawn_skuld(session, spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__SESSION__MODEL"] == "gpt-5.5"

    async def test_spawn_skuld_allows_blank_model_for_runtime_selected_sessions(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        """Codex-style sessions with no explicit model should not inject a Claude fallback."""
        session = Session(id=uuid4(), name="codex-test", model="")
        workspace = tmp_workspaces / str(session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(manager, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await manager._spawn_skuld(session, spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__SESSION__MODEL"] == ""


class TestResolveClaude:
    """Tests for claude binary resolution."""

    def test_absolute_path_exists(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        with patch("os.path.isfile", return_value=True):
            result = manager._resolve_claude_binary()
        assert result == "/usr/bin/fake-claude"

    def test_absolute_path_missing_raises(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="/nonexistent/claude",
            state_file=str(tmp_state_file),
        )
        with pytest.raises(FileNotFoundError, match="not found"):
            mgr._resolve_claude_binary()

    def test_path_lookup(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="claude",
            state_file=str(tmp_state_file),
        )
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = mgr._resolve_claude_binary()
        assert result == "/usr/local/bin/claude"

    def test_path_lookup_fails(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="claude",
            state_file=str(tmp_state_file),
        )
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(FileNotFoundError, match="not found in PATH"),
        ):
            mgr._resolve_claude_binary()


# ------------------------------------------------------------------
# Full start / stop lifecycle tests
# ------------------------------------------------------------------


class TestStartStop:
    """Tests for the full start/stop lifecycle."""

    async def test_start_returns_pod_start_result(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """start() returns a PodStartResult with correct endpoints."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            result = await manager.start(git_session, default_spec)

        assert isinstance(result, PodStartResult)
        assert "ws://localhost:" in result.chat_endpoint
        assert str(git_session.id) in result.chat_endpoint
        assert result.code_endpoint == "file:///tmp/ws"
        assert result.pod_name.startswith("local-")

    async def test_start_prefers_public_host_for_browser_endpoint(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Browser-facing session endpoints should use NIUU_SERVER_PUBLIC_HOST when set."""
        manager._server_host = "0.0.0.0"
        manager._server_public_host = "100.66.123.128"

        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            result = await manager.start(git_session, default_spec)

        assert result.chat_endpoint == f"ws://100.66.123.128:8080/s/{git_session.id}/session"

    async def test_set_skuld_registry_rehydrates_running_sessions(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        tmp_state_file.write_text(
            json.dumps(
                {
                    "sess-1": {
                        "session_id": "sess-1",
                        "pid": 1234,
                        "port": 9100,
                        "workspace": str(tmp_workspaces / "sess-1"),
                        "state": "running",
                    }
                }
            ),
            encoding="utf-8",
        )
        with (
            patch.object(LocalProcessPodManager, "_is_process_alive", return_value=True),
            patch.object(
                LocalProcessPodManager,
                "_process_command",
                return_value="python -m skuld",
            ),
        ):
            mgr = LocalProcessPodManager(
                workspaces_dir=str(tmp_workspaces),
                claude_binary="/usr/bin/fake-claude",
                state_file=str(tmp_state_file),
            )
        registry = MagicMock()

        mgr.set_skuld_registry(registry)

        registry.register.assert_called_once_with("sess-1", 9100)

    async def test_start_tracks_process(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """start() stores process info in the internal dict."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        session_id = str(git_session.id)
        assert session_id in manager._processes
        assert manager._processes[session_id].pid == 42
        assert manager._processes[session_id].state == ProcessState.RUNNING

    async def test_stop_terminates_process(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """stop() calls _terminate_process and releases the port."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        with patch.object(manager, "_terminate_process", new_callable=AsyncMock) as mock_term:
            result = await manager.stop(git_session)

        assert result is True
        mock_term.assert_called_once_with(42)
        info = manager._processes[str(git_session.id)]
        assert info.state == ProcessState.STOPPED

    async def test_stop_unknown_session(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """stop() returns False for unknown sessions."""
        session = Session(id=uuid4(), name="unknown")
        result = await manager.stop(session)
        assert result is False

    async def test_stop_already_stopped(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """stop() returns True for already-stopped sessions."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        with patch.object(manager, "_terminate_process", new_callable=AsyncMock):
            await manager.stop(git_session)

        result = await manager.stop(git_session)
        assert result is True

    async def test_max_concurrent_enforced(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """start() raises when max concurrent sessions reached."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="/usr/bin/fake-claude",
            max_concurrent=1,
            state_file=str(tmp_state_file),
        )
        session1 = Session(id=uuid4(), name="s1")
        session2 = Session(id=uuid4(), name="s2")
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        with (
            _mock_provision(mgr),
            # A live pid — the cap reconciler reaps sessions whose process is
            # dead, so a fake dead pid would silently free the slot.
            _mock_spawn(mgr, pid=os.getpid()),
        ):
            await mgr.start(session1, spec)
            capacity = await mgr.capacity()
            assert (capacity.limit, capacity.active, capacity.available) == (1, 1, 0)
            assert "pod_manager.max_concurrent" in capacity.remedy
            with pytest.raises(SessionCapacityError, match="1 of 1 sessions are running") as info:
                await mgr.start(session2, spec)
            # the refusal tells the person where the limit is raised
            assert "pod_manager.max_concurrent" in str(info.value)
            assert info.value.capacity.available == 0

    async def test_dead_process_frees_concurrent_slot(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """A session whose broker died no longer holds a phantom slot."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="/usr/bin/fake-claude",
            max_concurrent=1,
            state_file=str(tmp_state_file),
        )
        session1 = Session(id=uuid4(), name="s1")
        session2 = Session(id=uuid4(), name="s2")
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

        with (
            _mock_provision(mgr),
            # pid that is guaranteed dead: spawn a child and wait for it
            _mock_spawn(mgr, pid=_reaped_child_pid()),
        ):
            await mgr.start(session1, spec)
            # The dead session is reaped, freeing the slot — no raise.
            await mgr.start(session2, spec)

        assert mgr._processes[str(session1.id)].state == ProcessState.STOPPED

    async def test_start_failure_releases_port(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """If spawn fails, port is released and state is FAILED."""
        err = RuntimeError("spawn failed")
        with (
            _mock_provision(manager),
            _mock_spawn(manager, side_effect=err),
            pytest.raises(RuntimeError, match="spawn failed"),
        ):
            await manager.start(git_session, default_spec)

        info = manager._processes[str(git_session.id)]
        assert info.state == ProcessState.FAILED
        assert info.port not in manager._port_allocator.allocated


# ------------------------------------------------------------------
# Status and wait_for_ready tests
# ------------------------------------------------------------------


class TestStatus:
    """Tests for status() and wait_for_ready()."""

    async def test_status_unknown_session(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        session = Session(id=uuid4(), name="unknown")
        assert await manager.status(session) == SessionStatus.STOPPED

    async def test_status_provisioning_until_broker_ready(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        with patch.object(manager, "_broker_healthy", AsyncMock(return_value=False)):
            assert await manager.status(git_session) == SessionStatus.PROVISIONING
        with patch.object(manager, "_broker_healthy", AsyncMock(return_value=True)):
            assert await manager.wait_for_ready(git_session, timeout=1.0) == SessionStatus.RUNNING
        assert await manager.status(git_session) == SessionStatus.RUNNING

    async def test_status_reobserves_readiness_of_adopted_process(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """A process adopted after an API restart has no in-memory readiness."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)
        manager._ready.clear()

        healthy = AsyncMock(return_value=True)
        with patch.object(manager, "_broker_healthy", healthy):
            assert await manager.status(git_session) == SessionStatus.RUNNING
            assert await manager.status(git_session) == SessionStatus.RUNNING
        healthy.assert_awaited_once()

    async def test_status_after_stop(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        with patch.object(manager, "_terminate_process", new_callable=AsyncMock):
            await manager.stop(git_session)

        assert await manager.status(git_session) == SessionStatus.STOPPED

    async def test_wait_for_ready_already_running(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        with patch.object(manager, "_broker_healthy", AsyncMock(return_value=True)):
            result = await manager.wait_for_ready(git_session, timeout=5.0)
        assert result == SessionStatus.RUNNING

    async def test_wait_for_ready_surfaces_terminal_broker_failure(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        with _mock_provision(manager), _mock_spawn(manager):
            await manager.start(git_session, default_spec)

        sid = str(git_session.id)

        async def _failed(_session_id: str) -> bool:
            manager._broker_startup_failures[sid] = "workflow kickoff failed"
            return False

        with (
            patch.object(manager, "_broker_healthy", _failed),
            patch.object(manager, "_terminate_process", AsyncMock()) as terminate,
        ):
            result = await manager.wait_for_ready(git_session, timeout=1.0)

        assert result == SessionStatus.FAILED
        assert manager._processes[sid].state == ProcessState.FAILED
        assert manager._processes[sid].error == "workflow kickoff failed"
        assert await manager.status_detail(git_session) == "workflow kickoff failed"
        terminate.assert_awaited_once()

    async def test_wait_for_ready_unknown_session(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        session = Session(id=uuid4(), name="unknown")
        result = await manager.wait_for_ready(session, timeout=1.0)
        assert result == SessionStatus.FAILED

    async def test_broker_readiness_probe_fails_closed(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        sid = "probe-session"
        manager._processes[sid] = ProcessInfo(session_id=sid, port=9191)

        class _Response:
            status_code = 200

            def __init__(self, payload: object) -> None:
                self.payload = payload

            def json(self) -> object:
                if isinstance(self.payload, Exception):
                    raise self.payload
                return self.payload

        class _Client:
            def __init__(self, payload: object, **_kwargs: object) -> None:
                self.payload = payload

            async def __aenter__(self) -> _Client:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def get(self, _url: str) -> _Response:
                return _Response(self.payload)

        for payload in ({}, {"ready": "true"}, ValueError("invalid json")):
            with patch.object(
                local_process_mod.httpx,
                "AsyncClient",
                lambda **kwargs: _Client(payload, **kwargs),
            ):
                assert await manager._broker_healthy(sid) is False

        with patch.object(
            local_process_mod.httpx,
            "AsyncClient",
            lambda **kwargs: _Client({"ready": True, "startup_state": "ready"}, **kwargs),
        ):
            assert await manager._broker_healthy(sid) is True

    async def test_wait_for_ready_timeout(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
    ) -> None:
        """Timeout returns FAILED."""
        sid = str(git_session.id)
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            state=ProcessState.STARTING,
        )
        result = await manager.wait_for_ready(git_session, timeout=0.6)
        assert result == SessionStatus.FAILED


# ------------------------------------------------------------------
# Shutdown sequence tests
# ------------------------------------------------------------------


class TestShutdownSequence:
    """Tests for SIGTERM -> SIGKILL shutdown."""

    async def test_sigterm_then_exit(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """SIGTERM is sent and process exits before timeout."""
        with patch("os.kill") as mock_kill:
            # SIGTERM succeeds, then process is gone
            mock_kill.side_effect = [None, OSError("No such process")]
            await manager._terminate_process(1234)

        mock_kill.assert_any_call(1234, signal.SIGTERM)

    async def test_sigkill_after_timeout(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """SIGKILL is sent when process doesn't exit after SIGTERM."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="/usr/bin/fake-claude",
            stop_timeout=1,
            state_file=str(tmp_state_file),
        )

        call_count = 0

        def kill_side_effect(pid: int, sig: int) -> None:
            nonlocal call_count
            call_count += 1
            if sig == signal.SIGKILL:
                return
            # Process stays alive for all SIGTERM and probe calls
            return

        with patch("os.kill", side_effect=kill_side_effect):
            await mgr._terminate_process(1234)

        # Should have called SIGTERM + probes + SIGKILL
        assert call_count >= 3

    async def test_sigterm_oserror_returns(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """OSError on SIGTERM means process already gone."""
        with patch("os.kill", side_effect=OSError("No such process")):
            await manager._terminate_process(1234)
        # Should not raise


# ------------------------------------------------------------------
# State persistence tests
# ------------------------------------------------------------------


class TestStatePersistence:
    """Tests for JSON state file persistence and recovery."""

    def test_persist_creates_file(
        self,
        manager: LocalProcessPodManager,
        tmp_state_file: Path,
    ) -> None:
        manager._processes["abc"] = ProcessInfo(
            session_id="abc",
            pid=1234,
            port=9100,
            state=ProcessState.RUNNING,
        )
        manager._persist_state()
        assert tmp_state_file.exists()
        data = json.loads(tmp_state_file.read_text())
        assert "abc" in data
        assert data["abc"]["pid"] == 1234

    def test_load_marks_dead_as_stopped(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """On load, running sessions with dead processes are marked stopped."""
        data = {
            "sess1": {
                "session_id": "sess1",
                "pid": 999999,
                "port": 9100,
                "workspace": "/tmp/ws",
                "state": "running",
            }
        }
        tmp_state_file.write_text(json.dumps(data))

        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
        )
        assert mgr._processes["sess1"].state == ProcessState.STOPPED

    def test_load_marks_non_skuld_process_as_stopped(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """Running PIDs that are alive but not local Skuld processes should not count."""
        data = {
            "sess1": {
                "session_id": "sess1",
                "pid": 4242,
                "port": 9100,
                "workspace": "/tmp/ws",
                "state": "running",
            }
        }
        tmp_state_file.write_text(json.dumps(data))

        with (
            patch.object(LocalProcessPodManager, "_is_process_alive", return_value=True),
            patch.object(
                LocalProcessPodManager,
                "_process_command",
                return_value="containerd-shim",
            ),
        ):
            mgr = LocalProcessPodManager(
                workspaces_dir=str(tmp_workspaces),
                state_file=str(tmp_state_file),
            )
        assert mgr._processes["sess1"].state == ProcessState.STOPPED

    def test_load_corrupt_file(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """Corrupt state file is handled gracefully."""
        tmp_state_file.write_text("not json{{{")
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
        )
        assert len(mgr._processes) == 0

    def test_load_missing_file(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """Missing state file is handled gracefully."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
        )
        assert len(mgr._processes) == 0

    async def test_persist_state_on_start(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
        tmp_state_file: Path,
    ) -> None:
        """State file is updated after start."""
        with (
            _mock_provision(manager),
            _mock_spawn(manager),
        ):
            await manager.start(git_session, default_spec)

        assert tmp_state_file.exists()
        data = json.loads(tmp_state_file.read_text())
        assert str(git_session.id) in data


# ------------------------------------------------------------------
# Process monitor tests
# ------------------------------------------------------------------


class TestProcessMonitor:
    """Tests for the process monitoring task."""

    async def test_monitor_detects_exit(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Monitor updates state when process exits."""
        sid = "monitor-test"
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            pid=99999,
            port=9100,
            state=ProcessState.RUNNING,
        )
        manager._port_allocator._allocated.add(9100)

        with patch("os.kill", side_effect=OSError("No such process")):
            await manager._monitor_process(sid, 99999)

        assert manager._processes[sid].state == ProcessState.STOPPED
        assert 9100 not in manager._port_allocator.allocated

    async def test_monitor_cancellation(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Monitor handles cancellation gracefully."""
        sid = "cancel-test"
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            pid=99999,
            state=ProcessState.RUNNING,
        )

        # Kill always succeeds (process alive), so monitor loops
        with patch("os.kill", return_value=None):
            task = asyncio.create_task(manager._monitor_process(sid, 99999))
            await asyncio.sleep(0.1)
            task.cancel()
            # Monitor catches CancelledError and returns cleanly
            _ = await task
        # State should remain RUNNING (not updated on cancel)
        assert manager._processes[sid].state == ProcessState.RUNNING

    async def test_monitor_notifies_death_callback_on_exit(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """A broker exit propagates to the injected death callback (INV-9)."""
        sid = "death-cb"
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            pid=99999,
            port=9100,
            state=ProcessState.RUNNING,
        )
        manager._port_allocator._allocated.add(9100)

        notified: list[str] = []

        async def _on_death(session_id: str) -> None:
            notified.append(session_id)

        manager.set_death_callback(_on_death)

        with patch("os.kill", side_effect=OSError("No such process")):
            await manager._monitor_process(sid, 99999)

        assert manager._processes[sid].state == ProcessState.STOPPED
        assert notified == [sid]

    async def test_monitor_swallows_death_callback_errors(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """A failing death callback must not tear down the monitor."""
        sid = "death-cb-err"
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            pid=99999,
            port=9100,
            state=ProcessState.RUNNING,
        )

        async def _boom(_session_id: str) -> None:
            raise RuntimeError("reconcile blew up")

        manager.set_death_callback(_boom)

        with patch("os.kill", side_effect=OSError("No such process")):
            await manager._monitor_process(sid, 99999)

        # Process still reaped despite the callback failure.
        assert manager._processes[sid].state == ProcessState.STOPPED

    async def test_monitor_no_callback_is_noop(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """Without a death callback the monitor still reaps cleanly."""
        sid = "no-cb"
        manager._processes[sid] = ProcessInfo(
            session_id=sid,
            pid=99999,
            port=9100,
            state=ProcessState.RUNNING,
        )

        with patch("os.kill", side_effect=OSError("No such process")):
            await manager._monitor_process(sid, 99999)

        assert manager._processes[sid].state == ProcessState.STOPPED


# ------------------------------------------------------------------
# Constructor / config tests
# ------------------------------------------------------------------


class TestConstructor:
    """Tests for constructor and configuration."""

    def test_default_values(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
        )
        # No limit unless the operator sets one.
        assert mgr._max_concurrent is None
        assert mgr._stop_timeout == DEFAULT_STOP_TIMEOUT
        assert mgr._claude_binary == DEFAULT_CLAUDE_BINARY

    def test_extra_kwargs_ignored(
        self,
        tmp_workspaces: Path,
        tmp_state_file: Path,
    ) -> None:
        """Extra kwargs from dynamic config are ignored."""
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            state_file=str(tmp_state_file),
            unknown_key="should not raise",
        )
        assert mgr._max_concurrent is None

    def test_expanduser_paths(self) -> None:
        """Paths with ~ are expanded."""
        with patch("pathlib.Path.exists", return_value=False):
            mgr = LocalProcessPodManager(
                workspaces_dir="~/test-ws",
                state_file="~/test-state.json",
            )
        assert "~" not in str(mgr._workspaces_dir)
        assert "~" not in str(mgr._state_file)


class TestPreviewEnv:
    """Skuld live-preview settings for a session on the shared local host."""

    def _manager(self, tmp_workspaces, tmp_state_file, registry=None, **kwargs):
        mgr = LocalProcessPodManager(
            workspaces_dir=str(tmp_workspaces),
            claude_binary="/usr/bin/fake-claude",
            state_file=str(tmp_state_file),
            **kwargs,
        )
        if registry is not None:
            mgr.set_skuld_registry(registry)
        return mgr

    def test_dev_identity_registry_needs_no_router_credential(self, tmp_workspaces, tmp_state_file):
        registry = MagicMock()
        registry.dev_identity = True
        mgr = self._manager(tmp_workspaces, tmp_state_file, registry)

        env = mgr._preview_env("sess-1")

        assert env["SKULD__PREVIEW__ENABLED"] == "true"
        assert json.loads(env["SKULD__PREVIEW__ROUTER_AUTH"]) == {
            "adapter": "skuld.preview_adapters.NoRouterAuth"
        }
        assert json.loads(env["SKULD__PREVIEW__PORT_SCANNER"]) == {
            "adapter": "skuld.preview_adapters.LsofSessionPortScanner",
            "kwargs": {"session_id": "sess-1"},
        }

    def test_dev_identity_wins_over_a_configured_router_auth(self, tmp_workspaces, tmp_state_file):
        registry = MagicMock()
        registry.dev_identity = True
        configured = {"adapter": "skuld.preview_adapters.WorkloadJwtRouterAuth", "kwargs": {}}
        mgr = self._manager(
            tmp_workspaces, tmp_state_file, registry, preview_router_auth=configured
        )

        auth = json.loads(mgr._preview_env("s")["SKULD__PREVIEW__ROUTER_AUTH"])

        assert auth == {"adapter": "skuld.preview_adapters.NoRouterAuth"}

    def test_an_authenticated_host_uses_the_configured_router_auth(
        self, tmp_workspaces, tmp_state_file
    ):
        registry = MagicMock()
        registry.dev_identity = False
        configured = {
            "adapter": "skuld.preview_adapters.WorkloadJwtRouterAuth",
            "kwargs": {"issuer": "i", "audience": "a", "jwks_uri": "http://x/jwks"},
        }
        mgr = self._manager(
            tmp_workspaces, tmp_state_file, registry, preview_router_auth=configured
        )

        env = mgr._preview_env("sess-2")

        assert env["SKULD__PREVIEW__ENABLED"] == "true"
        assert json.loads(env["SKULD__PREVIEW__ROUTER_AUTH"]) == configured
        assert json.loads(env["SKULD__PREVIEW__PORT_SCANNER"])["kwargs"] == {"session_id": "sess-2"}

    def test_an_authenticated_host_without_router_auth_turns_preview_off(
        self, tmp_workspaces, tmp_state_file, caplog
    ):
        registry = MagicMock()
        registry.dev_identity = False
        mgr = self._manager(tmp_workspaces, tmp_state_file, registry)

        with caplog.at_level("INFO", logger=local_process_mod.logger.name):
            env = mgr._preview_env("sess-3")

        assert env == {"SKULD__PREVIEW__ENABLED": "false"}
        assert "preview_router_auth" in caplog.text

    def test_no_registry_at_all_counts_as_an_authenticated_host(
        self, tmp_workspaces, tmp_state_file
    ):
        mgr = self._manager(tmp_workspaces, tmp_state_file)

        assert mgr._preview_env("s") == {"SKULD__PREVIEW__ENABLED": "false"}

    async def test_the_spawned_skuld_environment_carries_it(
        self, tmp_workspaces, tmp_state_file, git_session, default_spec
    ):
        registry = MagicMock()
        registry.dev_identity = True
        mgr = self._manager(tmp_workspaces, tmp_state_file, registry)
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)
        mock_proc = MagicMock()
        mock_proc.pid = 42

        with (
            patch.object(mgr, "_resolve_skuld_command", return_value=["python", "-m", "skuld"]),
            patch.object(mgr, "_resolve_claude_binary", return_value="/usr/bin/fake-claude"),
            patch(
                "asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
                return_value=mock_proc,
            ) as mock_exec,
        ):
            await mgr._spawn_skuld(git_session, default_spec, workspace, 9100)

        env = mock_exec.call_args.kwargs["env"]
        assert env["SKULD__PREVIEW__ENABLED"] == "true"
        scanner = json.loads(env["SKULD__PREVIEW__PORT_SCANNER"])
        assert scanner["kwargs"] == {"session_id": str(git_session.id)}
