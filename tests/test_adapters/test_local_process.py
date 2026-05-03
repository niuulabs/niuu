"""Tests for the LocalProcessPodManager adapter."""

from __future__ import annotations

import asyncio
import json
import signal
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import yaml

from niuu.mesh.ipc import skuld_mesh_addresses
from volundr.adapters.outbound.local_process import (
    DEFAULT_CLAUDE_BINARY,
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_SDK_PORT_START,
    DEFAULT_STOP_TIMEOUT,
    FlockPortPlan,
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
        max_concurrent=DEFAULT_MAX_CONCURRENT,
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
    def test_build_env_includes_broker_overrides(self) -> None:
        """Broker values from session definitions are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": False,
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


class TestFlockPortAllocation:
    def test_pick_flock_base_port_skips_taken_ranges(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        """The flock allocator reserves ports for Skuld and all ravn peers."""
        free_checks: list[int] = []
        taken_ports = {7480, 7481, 7580, 7482}

        def fake_is_port_free(port: int) -> bool:
            free_checks.append(port)
            return port not in taken_ports

        with patch.object(SdkPortAllocator, "_is_port_free", side_effect=fake_is_port_free):
            base_port = manager._pick_flock_base_port(node_count=1)

        assert base_port == 7483
        assert free_checks[:4] == [7480, 7481, 7482, 7483]

    def test_flock_port_plan_offsets_ravn_after_skuld(self) -> None:
        """Ravn sidecars are shifted off Skuld's mesh slot in mini mode."""
        plan = LocalProcessPodManager._flock_port_plan(7484)
        assert plan == FlockPortPlan(
            session_base_port=7484,
            ravn_base_port=7486,
            skuld_pub_port=7484,
            skuld_rep_port=7485,
            skuld_handshake_port=7584,
        )


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

    def test_is_port_free_detects_wildcard_listener(self) -> None:
        """Wildcard listeners must block flock reuse of the same port."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("0.0.0.0", 0))
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
        )
        restored = ProcessInfo.from_dict(info.to_dict())
        assert restored.session_id == info.session_id
        assert restored.pid == info.pid
        assert restored.port == info.port
        assert restored.state == info.state

    def test_from_dict_defaults(self) -> None:
        info = ProcessInfo.from_dict({"session_id": "x"})
        assert info.pid is None
        assert info.port is None
        assert info.state == ProcessState.STOPPED


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

    async def test_writes_claude_md(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        default_spec: SessionSpec,
    ) -> None:
        """CLAUDE.md is written with system prompt."""
        with patch.object(manager, "_clone_repo", new_callable=AsyncMock):
            workspace = await manager._provision_workspace(git_session, default_spec)
        claude_md = workspace / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "You are a helpful assistant." in content

    async def test_writes_claude_md_with_initial_prompt(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
    ) -> None:
        """CLAUDE.md includes initial prompt when provided."""
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
        content = (workspace / "CLAUDE.md").read_text()
        assert "Initial Task" in content
        assert "Do the thing." in content

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
            values={"git_token": "tok123"},
            pod_spec=PodSpecAdditions(),
        )

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = [mock_proc, checkout_proc]
            await manager._clone_repo(source, workspace, spec)

        clone_call = mock_exec.call_args_list[0]
        args = clone_call[0]
        assert "git" in args
        assert "clone" in args
        assert "--no-single-branch" in args
        assert "x-access-token:tok123@github.com" in args[5]

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
        """Falls back to base_branch when feature branch checkout fails."""
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

        # First checkout fails (feature branch), second succeeds (base)
        fail_proc = AsyncMock()
        fail_proc.returncode = 1
        fail_proc.communicate = AsyncMock(return_value=(b"", b""))

        ok_proc = AsyncMock()
        ok_proc.returncode = 0
        ok_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_exec.side_effect = [clone_proc, fail_proc, ok_proc]
            await manager._clone_repo(source, workspace, spec)

        # 3 calls: clone, checkout feat, checkout main
        assert mock_exec.call_count == 3


# ------------------------------------------------------------------
# Process spawning tests
# ------------------------------------------------------------------


class TestProcessSpawning:
    """Tests for Skuld process spawning."""

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

    def test_build_env_sets_workspace_dir(self) -> None:
        """WORKSPACE_DIR is set in the environment."""
        spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())
        env = LocalProcessPodManager._build_env(spec, Path("/tmp/ws"))
        assert env["WORKSPACE_DIR"] == "/tmp/ws"

    def test_build_env_includes_broker_overrides(self) -> None:
        """Broker values from session definitions are mapped to Skuld env vars."""
        spec = SessionSpec(
            values={
                "broker": {
                    "cliType": "codex-ws",
                    "transport": "sdk",
                    "transportAdapter": "skuld.transports.codex_ws.CodexWebSocketTransport",
                    "skipPermissions": False,
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
        assert "SKULD__CLI_BINARY" not in env

    async def test_spawn_skuld_overrides_mesh_ports_for_local_flock(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        """Mini-mode local flocks must share one explicit mesh layout."""
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_ENABLED", "value": "true"},
                    {"name": "MESH_PUB_ADDRESS", "value": "tcp://0.0.0.0:7480"},
                    {"name": "MESH_REP_ADDRESS", "value": "tcp://0.0.0.0:7481"},
                    {"name": "MESH_HANDSHAKE_PORT", "value": "7580"},
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
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
            await manager._spawn_skuld(
                git_session,
                spec,
                workspace,
                9100,
                flock_plan=FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
            )

        env = mock_exec.call_args.kwargs["env"]
        expected_pub, expected_rep = skuld_mesh_addresses(workspace / ".flock")
        assert env["SKULD__MESH__NNG__PUB_SUB_ADDRESS"] == expected_pub
        assert env["SKULD__MESH__NNG__REQ_REP_ADDRESS"] == expected_rep
        assert env["SKULD__MESH__HANDSHAKE_PORT"] == "7584"

    async def test_start_flock_uses_shifted_ravn_ports_and_loopback_peer(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        """Local flock startup must keep Skuld on loopback and shift ravn slots."""
        workspace = tmp_workspaces / "session"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text(
            "peers:\n"
            "- peer_id: flock-reviewer\n"
            "  persona: reviewer\n"
            "  display_name: reviewer\n"
            "  pub_address: tcp://127.0.0.1:7486\n"
            "  rep_address: tcp://127.0.0.1:7487\n",
            encoding="utf-8",
        )

        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with patch("subprocess.run") as mock_run:
            result = await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        assert result == flock_dir
        init_call = mock_run.call_args_list[0]
        assert init_call.args[0][-6:] == [
            "--mesh-transport",
            "ipc",
            "--no-http-gateway",
            "--base-port",
            "7486",
            "--force",
        ]
        cluster = (flock_dir / "cluster.yaml").read_text(encoding="utf-8")
        expected_pub, expected_rep = skuld_mesh_addresses(flock_dir)
        assert "peer_id: skuld-test" in cluster
        assert f"pub_address: {expected_pub}" in cluster
        assert f"rep_address: {expected_rep}" in cluster

    async def test_start_flock_injects_workflow_into_node_configs(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session-with-workflow"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text("peers: []\n", encoding="utf-8")
        (flock_dir / "node-reviewer.yaml").write_text("persona: reviewer\n", encoding="utf-8")

        spec = SessionSpec(
            values={
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Review Flow",
                    "version": "draft",
                    "scope": "user",
                    "initial_context": "Review this change.",
                    "graph": {
                        "nodes": [{"id": "stage-1", "kind": "stage", "label": "Review"}],
                        "edges": [],
                    },
                }
            },
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with patch("subprocess.run"):
            await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        node_config = (flock_dir / "node-reviewer.yaml").read_text(encoding="utf-8")
        assert "workflow_id: wf-1" in node_config
        assert "name: Review Flow" in node_config
        assert "initial_context: Review this change." in node_config

    async def test_start_flock_applies_llm_and_persona_overrides(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session-with-overrides"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text("peers: []\n", encoding="utf-8")
        (flock_dir / "node-reviewer.yaml").write_text(
            "persona: reviewer\ninitiative:\n  enabled: true\n  max_concurrent_tasks: 3\n",
            encoding="utf-8",
        )

        spec = SessionSpec(
            values={
                "flock": {
                    "personas": [
                        {
                            "name": "reviewer",
                            "llm": {"model": "Qwen/Qwen3.6-35B-A3B-FP8"},
                            "system_prompt_extra": "Be extra careful.",
                            "iteration_budget": 40,
                            "max_concurrent_tasks": 1,
                        }
                    ],
                    "llm_config": {
                        "model": "google/gemma-4-26B-A4B-it",
                        "max_tokens": 8192,
                    },
                    "max_concurrent_tasks": 5,
                }
            },
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with (
            patch("subprocess.run"),
            patch("ravn.adapters.personas.loader.FilesystemPersonaAdapter") as loader_cls,
        ):
            loader_cls.return_value.load.return_value = MagicMock(allowed_tools=["file", "git"])
            await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        node_config = (flock_dir / "node-reviewer.yaml").read_text(encoding="utf-8")
        assert "model: Qwen/Qwen3.6-35B-A3B-FP8" in node_config
        assert "max_concurrent_tasks: 1" in node_config
        assert "system_prompt_extra: Be extra careful." in node_config
        assert "iteration_budget: 40" in node_config
        assert "enabled: true" in node_config
        assert "broker_url: ws://127.0.0.1:9101/ws/ravn" in node_config

    async def test_start_flock_materializes_mimir_runtime_and_local_paths(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session-with-mimir"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text("peers: []\n", encoding="utf-8")
        (flock_dir / "node-coder.yaml").write_text("persona: coder\n", encoding="utf-8")

        registry_path = tmp_workspaces / "shared-mimir"
        spec = SessionSpec(
            values={
                "flock": {
                    "personas": [{"name": "coder"}],
                    "llm_config": {"model": "google/gemma-4-26B-A4B-it"},
                    "max_concurrent_tasks": 3,
                },
                "mimir": {
                    "registryRefs": [
                        {
                            "registry_entry_id": "shared-team",
                            "mount_name": "shared-team",
                            "path": str(registry_path),
                            "role": "shared",
                            "categories": ["decision"],
                        }
                    ],
                    "ephemeralLocals": [{"mount_name": "scratchpad"}],
                    "bindings": [
                        {
                            "mount_name": "scratchpad",
                            "access": "read_write",
                            "write_prefixes": ["draft/"],
                        }
                    ],
                },
            },
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-coder"},
                ),
            ),
        )

        with (
            patch("subprocess.run"),
            patch("ravn.adapters.personas.loader.FilesystemPersonaAdapter") as loader_cls,
        ):
            loader_cls.return_value.load.return_value = MagicMock(allowed_tools=["file", "git"])
            await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        node_config = yaml.safe_load((flock_dir / "node-coder.yaml").read_text(encoding="utf-8"))
        mimir_cfg = node_config["mimir"]
        assert mimir_cfg["enabled"] is True
        assert {
            "name": "shared-team",
            "path": str(registry_path),
            "role": "shared",
            "categories": ["decision"],
        } in mimir_cfg["instances"]
        local_instance = next(
            instance for instance in mimir_cfg["instances"] if instance["name"] == "local"
        )
        scratch_instance = next(
            instance for instance in mimir_cfg["instances"] if instance["name"] == "scratchpad"
        )
        assert local_instance["path"] == str(flock_dir / "mimir" / "local")
        assert scratch_instance["path"] == str(flock_dir / "mimir" / "local" / "scratchpad")
        assert Path(local_instance["path"]).is_dir()
        assert Path(scratch_instance["path"]).is_dir()
        assert mimir_cfg["write_routing"]["default"] == ["local"]
        assert {"prefix": "draft/", "mounts": ["scratchpad"]} in mimir_cfg["write_routing"]["rules"]

    async def test_start_flock_enriches_cluster_peers_with_persona_metadata(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session-with-metadata"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text(
            "peers:\n"
            "  - peer_id: flock-coder\n"
            "    persona: coder\n"
            "    display_name: coder\n"
            "  - peer_id: flock-reviewer\n"
            "    persona: reviewer\n"
            "    display_name: reviewer\n",
            encoding="utf-8",
        )
        (flock_dir / "node-coder.yaml").write_text("persona: coder\n", encoding="utf-8")
        (flock_dir / "node-reviewer.yaml").write_text("persona: reviewer\n", encoding="utf-8")

        spec = SessionSpec(
            values={
                "workflow": {
                    "graph": {
                        "nodes": [
                            {
                                "id": "stage-coder",
                                "kind": "stage",
                                "personaIds": ["coder"],
                            },
                            {
                                "id": "stage-reviewer",
                                "kind": "stage",
                                "personaIds": ["reviewer"],
                            },
                            {
                                "id": "trigger-1",
                                "kind": "trigger",
                                "dispatchEvent": "code.requested",
                            },
                        ],
                        "edges": [
                            {
                                "source": "stage-coder",
                                "target": "stage-reviewer",
                                "label": "code.changed -> code.changed",
                            }
                        ],
                    }
                },
                "flock": {
                    "personas": [{"name": "coder"}, {"name": "reviewer"}],
                    "llm_config": {"model": "google/gemma-4-26B-A4B-it"},
                    "max_concurrent_tasks": 3,
                },
            },
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-coder"},
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with (
            patch("subprocess.run"),
            patch("ravn.adapters.personas.loader.FilesystemPersonaAdapter") as loader_cls,
        ):
            loader = loader_cls.return_value
            loader.load.side_effect = lambda persona: MagicMock(
                allowed_tools=["file", "git"] if persona == "coder" else ["file", "ravn"]
            )
            await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        cluster = (flock_dir / "cluster.yaml").read_text(encoding="utf-8")
        assert "capabilities:" in cluster
        assert "consumes_event_types:" in cluster
        assert "emits_event_types:" in cluster
        assert "code.requested" in cluster
        assert "code.changed" in cluster


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
        with patch.object(LocalProcessPodManager, "_is_process_alive", return_value=True):
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
            _mock_spawn(mgr),
        ):
            await mgr.start(session1, spec)
            with pytest.raises(RuntimeError, match="Max concurrent sessions"):
                await mgr.start(session2, spec)

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

    async def test_status_running(
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

        assert await manager.status(git_session) == SessionStatus.RUNNING

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

        result = await manager.wait_for_ready(git_session, timeout=5.0)
        assert result == SessionStatus.RUNNING

    async def test_wait_for_ready_unknown_session(
        self,
        manager: LocalProcessPodManager,
    ) -> None:
        session = Session(id=uuid4(), name="unknown")
        result = await manager.wait_for_ready(session, timeout=1.0)
        assert result == SessionStatus.FAILED

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
            await task
        # State should remain RUNNING (not updated on cancel)
        assert manager._processes[sid].state == ProcessState.RUNNING


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
        assert mgr._max_concurrent == DEFAULT_MAX_CONCURRENT
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
        assert mgr._max_concurrent == DEFAULT_MAX_CONCURRENT

    def test_expanduser_paths(self) -> None:
        """Paths with ~ are expanded."""
        with patch("pathlib.Path.exists", return_value=False):
            mgr = LocalProcessPodManager(
                workspaces_dir="~/test-ws",
                state_file="~/test-state.json",
            )
        assert "~" not in str(mgr._workspaces_dir)
        assert "~" not in str(mgr._state_file)


class TestLocalFlockMeshMode:
    async def test_spawn_skuld_uses_ipc_mesh_addresses_for_local_flock(
        self,
        manager: LocalProcessPodManager,
        git_session: Session,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / str(git_session.id)
        workspace.mkdir(parents=True)

        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_ENABLED", "value": "true"},
                    {"name": "MESH_PUB_ADDRESS", "value": "tcp://0.0.0.0:7480"},
                    {"name": "MESH_REP_ADDRESS", "value": "tcp://0.0.0.0:7481"},
                    {"name": "MESH_HANDSHAKE_PORT", "value": "7580"},
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
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
            await manager._spawn_skuld(
                git_session,
                spec,
                workspace,
                9100,
                flock_plan=FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
            )

        env = mock_exec.call_args.kwargs["env"]
        expected_pub, expected_rep = skuld_mesh_addresses(workspace / ".flock")
        assert env["SKULD__MESH__NNG__PUB_SUB_ADDRESS"] == expected_pub
        assert env["SKULD__MESH__NNG__REQ_REP_ADDRESS"] == expected_rep
        assert env["SKULD__MESH__HANDSHAKE_PORT"] == "7584"

    async def test_start_flock_uses_ipc_mode_and_skuld_socket_addresses(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text(
            "peers:\n"
            "- peer_id: flock-reviewer\n"
            "  persona: reviewer\n"
            "  display_name: reviewer\n"
            "  pub_address: tcp://127.0.0.1:7486\n"
            "  rep_address: tcp://127.0.0.1:7487\n",
            encoding="utf-8",
        )

        spec = SessionSpec(
            values={},
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with patch("subprocess.run") as mock_run:
            result = await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        assert result == flock_dir
        init_call = mock_run.call_args_list[0]
        assert init_call.args[0][-6:] == [
            "--mesh-transport",
            "ipc",
            "--no-http-gateway",
            "--base-port",
            "7486",
            "--force",
        ]
        cluster = (flock_dir / "cluster.yaml").read_text(encoding="utf-8")
        expected_pub, expected_rep = skuld_mesh_addresses(flock_dir)
        assert "peer_id: skuld-test" in cluster
        assert f"pub_address: {expected_pub}" in cluster
        assert f"rep_address: {expected_rep}" in cluster

    async def test_start_flock_preserves_workflow_injection_in_ipc_mode(
        self,
        manager: LocalProcessPodManager,
        tmp_workspaces: Path,
    ) -> None:
        workspace = tmp_workspaces / "session-with-workflow"
        workspace.mkdir(parents=True)
        flock_dir = workspace / ".flock"
        flock_dir.mkdir()
        (flock_dir / "cluster.yaml").write_text("peers: []\n", encoding="utf-8")
        (flock_dir / "node-reviewer.yaml").write_text("persona: reviewer\n", encoding="utf-8")

        spec = SessionSpec(
            values={
                "workflow": {
                    "workflow_id": "wf-1",
                    "name": "Review Flow",
                    "version": "draft",
                    "scope": "user",
                    "initial_context": "Review this change.",
                    "graph": {
                        "nodes": [{"id": "stage-1", "kind": "stage", "label": "Review"}],
                        "edges": [],
                    },
                }
            },
            pod_spec=PodSpecAdditions(
                env=(
                    {"name": "MESH_PEER_ID", "value": "skuld-test"},
                ),
                extra_containers=(
                    {"name": "ravn-reviewer"},
                ),
            ),
        )

        with patch("subprocess.run"):
            await manager._start_flock(
                spec,
                workspace,
                FlockPortPlan(
                    session_base_port=7484,
                    ravn_base_port=7486,
                    skuld_pub_port=7484,
                    skuld_rep_port=7485,
                    skuld_handshake_port=7584,
                ),
                skuld_port=9101,
            )

        node_config = (flock_dir / "node-reviewer.yaml").read_text(encoding="utf-8")
        assert "workflow_id: wf-1" in node_config
        assert "name: Review Flow" in node_config
        assert "initial_context: Review this change." in node_config
