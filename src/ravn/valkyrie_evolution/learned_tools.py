"""Runtime adapter for resident-authored agent tools."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.valkyrie_evolution.models import (
    LearnedToolArtifact,
    LearnedToolManifest,
    ToolReachGrant,
)
from ravn.valkyrie_evolution.tool_runtime import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    DEFAULT_TOOL_VENV_PIP_TIMEOUT_SECONDS,
    TOOL_VENV_REQUIREMENTS_STAMP,
    TOOL_VENV_UV_CACHE_DIRNAME,
    HostCall,
    ToolRunResult,
    ToolVenvError,
    ensure_tool_venv,
    run_tool,
    write_tool,
)

logger = logging.getLogger(__name__)

#: Reach kinds whose declaration grants a learned tool outbound network
#: access. Any grant whose kind is in this set — or starts with ``http``
#: ("http_get", "https_post", …) — flips the sandbox to a networked
#: container; every other tool runs with networking off.
NETWORK_REACH_KINDS = frozenset({"network", "http", "https", "api", "external_read"})

#: Docker network mode used when a tool's declared reach grants network access.
NETWORK_ALLOWED_DOCKER_NETWORK = "bridge"

#: Docker network mode used when a tool declares no network reach.
NETWORK_DENIED_DOCKER_NETWORK = "none"

#: Default devrunner image for the Forge sandbox execution path.
DEFAULT_FORGE_SANDBOX_IMAGE = "ghcr.io/niuulabs/devrunner:latest"

#: Immutable default image for the fail-closed, one-container-per-run backend.
#: ``--pull=never`` is used, so an operator must preload this exact reviewed
#: image rather than letting an autonomous tool execution silently change its
#: own runtime. Tests and alternate composition roots may inject another policy.
DEFAULT_CONTAINED_TOOL_IMAGE = (
    "ghcr.io/niuulabs/devrunner@"
    "sha256:ec7a32ffd8ca1f3ddb8bd4983198988538ab74804201ce45e14e56241adfc518"
)

#: ``ToolRunResult.enforcement`` marker: the sandbox boundary applied the
#: tool's declared reach (network on only when granted).
REACH_ENFORCEMENT_ENFORCED = "enforced"

#: ``ToolRunResult.enforcement`` marker: the execution backend could not
#: express the isolation the declared reach requires. Recorded honestly —
#: a bypassable or pretended boundary is worse than none.
REACH_ENFORCEMENT_UNAVAILABLE = "unavailable"


class LearnedToolError(ValueError):
    """Raised when a learned tool artifact cannot be installed or loaded."""


def reach_allows_network(declared_reach: Sequence[ToolReachGrant]) -> bool:
    """True when a manifest's declared reach grants outbound network access."""
    return any(
        grant.kind.lower() in NETWORK_REACH_KINDS or grant.kind.lower().startswith("http")
        for grant in declared_reach
    )


class LearnedToolRunner(Protocol):
    """Execution backend for a resident-authored learned tool."""

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
        host_call: HostCall | None = None,
    ) -> ToolRunResult:
        """Execute a learned tool and return a structured run result."""


class LocalLearnedToolRunner:
    """Run learned tools through the existing local isolated subprocess.

    Local execution is crash isolation, not security isolation: it cannot
    turn networking off for a tool that declares no network reach.
    :attr:`enforces_reach` is honest about that, and executing such a tool
    locally logs a one-time warning instead of pretending a boundary exists.
    Tools with declared ``requirements`` run with the python of a dedicated
    per-tool venv provisioned under ``venvs_dir``.
    """

    #: A local subprocess cannot express network isolation.
    enforces_reach = False

    def __init__(
        self,
        *,
        venvs_dir: str | Path | None = None,
        pip_timeout_seconds: float = DEFAULT_TOOL_VENV_PIP_TIMEOUT_SECONDS,
    ) -> None:
        self._venvs_dir = Path(venvs_dir) if venvs_dir else None
        self._pip_timeout_seconds = pip_timeout_seconds
        self._reach_warned: set[str] = set()

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
        host_call: HostCall | None = None,
    ) -> ToolRunResult:
        self._warn_unenforced_reach(tool_path, declared_reach)
        python_executable: Path | None = None
        if requirements:
            if self._venvs_dir is None:
                return ToolRunResult(
                    ok=False,
                    error=(
                        f"learned tool {tool_path.stem} declares {len(requirements)} "
                        "requirement(s) but the local runner has no venvs_dir; "
                        "refusing to run it without its dependencies"
                    ),
                )
            try:
                python_executable = await asyncio.to_thread(
                    ensure_tool_venv,
                    venvs_dir=self._venvs_dir,
                    tool_name=tool_path.stem,
                    requirements=list(requirements),
                    pip_timeout_seconds=self._pip_timeout_seconds,
                )
            except ToolVenvError as exc:
                return ToolRunResult(
                    ok=False,
                    error=f"per-tool venv provisioning failed for {tool_path.stem}: {exc}",
                )
        return await run_tool(
            tool_path,
            payload,
            entry_point=entry_point,
            timeout_seconds=timeout_seconds,
            python_executable=python_executable,
            host_call=host_call,
        )

    def _warn_unenforced_reach(
        self,
        tool_path: Path,
        declared_reach: Sequence[ToolReachGrant],
    ) -> None:
        """One-time honesty warning where enforcement would have mattered."""
        if reach_allows_network(declared_reach):
            return
        key = tool_path.stem
        if key in self._reach_warned:
            return
        self._reach_warned.add(key)
        logger.warning(
            "learned tool %s declares no network reach, but the local runner "
            "cannot turn networking off — reach is NOT enforced on the local backend",
            key,
        )


@dataclass(frozen=True)
class ContainerRuntimePolicy:
    """Fixed resource and privilege ceiling for one learned-tool run."""

    image: str = DEFAULT_CONTAINED_TOOL_IMAGE
    memory: str = "256m"
    cpus: str = "0.5"
    pids_limit: int = 64
    tmpfs_size: str = "64m"


@dataclass(frozen=True)
class _ContainerProcessResult:
    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""
    error: str = ""
    timed_out: bool = False
    output_exceeded: bool = False


ContainerCommandRunner = Callable[
    [Sequence[str], bytes, float, str], Awaitable[_ContainerProcessResult]
]

_CONTAINER_TOOL_PATH = "/opt/ravn/tool/tool.py"
_CONTAINER_VENV_PATH = "/opt/ravn/venv"
_CONTAINER_WORKDIR = "/work"
_FILESYSTEM_REACH_KINDS = frozenset({"file", "filesystem", "path", "workspace"})
_CREDENTIAL_REACH_KINDS = frozenset({"credential", "credentials", "secret", "secrets"})
_READ_ONLY_ACCESS = frozenset({"none", "read"})
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOCKER_PROXY_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)


class ContainedLearnedToolRunner:
    """Run each learned tool in a fresh, least-reach OCI container.

    This is the hard execution boundary for resident-authored code. The
    container receives no workspace mount, no host environment, no runtime
    socket, and no network by default. A manifest may add only:

    * exact existing filesystem paths, mounted read-only or read-write;
    * explicitly named credential environment variables; and
    * broad outbound network access.

    A target-specific network grant is rejected because a Docker bridge cannot
    enforce a hostname/IP allowlist. Failing closed is intentional: recording
    such a run as "contained" would turn review metadata into pretend safety.
    """

    enforces_reach = True

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        venvs_dir: str | Path | None = None,
        policy: ContainerRuntimePolicy | None = None,
        command_runner: ContainerCommandRunner | None = None,
        output_limit_bytes: int = 256 * 1024,
        provision_timeout_seconds: float = DEFAULT_TOOL_VENV_PIP_TIMEOUT_SECONDS,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._venvs_dir = (
            Path(venvs_dir).resolve()
            if venvs_dir is not None
            else self._workspace_root / ".ravn" / "learned_tool_venvs"
        )
        self._policy = policy or ContainerRuntimePolicy()
        self._command_runner = command_runner or self._run_docker
        self._output_limit_bytes = output_limit_bytes
        self._provision_timeout_seconds = provision_timeout_seconds
        self._venv_locks: dict[str, asyncio.Lock] = {}

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
        host_call: HostCall | None = None,
    ) -> ToolRunResult:
        if host_call is not None:
            return ToolRunResult(
                ok=False,
                error=(
                    "this execution backend cannot provide the host SDK: the tool asks to "
                    "call the resident's own tools and there is no channel back from here. "
                    "Run it on the local backend, or rebuild it self-contained."
                ),
            )
        path = tool_path.resolve()
        if not path.is_file():
            return ToolRunResult(ok=False, error=f"tool implementation missing: {path}")
        try:
            reach_args, credential_names = self._reach_args(declared_reach)
            venv_dir = await self._ensure_container_venv(path.stem, requirements)
        except LearnedToolError as exc:
            return ToolRunResult(
                ok=False,
                error=str(exc),
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
            )

        name = f"ravn-tool-{uuid.uuid4().hex[:16]}"
        argv = self._base_docker_argv(name=name)
        argv.extend(reach_args)
        argv.extend(self._credential_args(credential_names))
        argv.extend(
            [
                "--mount",
                self._bind_mount(path, Path(_CONTAINER_TOOL_PATH), read_only=True),
            ]
        )
        python = "python"
        if venv_dir is not None:
            argv.extend(
                [
                    "--mount",
                    self._bind_mount(
                        venv_dir,
                        Path(_CONTAINER_VENV_PATH),
                        read_only=True,
                    ),
                ]
            )
            python = f"{_CONTAINER_VENV_PATH}/bin/python"
        argv.extend(
            [
                self._policy.image,
                python,
                "-I",
                "-c",
                _CONTAINER_BOOTSTRAP,
                _CONTAINER_TOOL_PATH,
                entry_point,
            ]
        )
        completed = await self._command_runner(
            argv,
            json.dumps(payload).encode("utf-8"),
            timeout_seconds,
            name,
        )
        return self._tool_result(completed, path=path, timeout_seconds=timeout_seconds)

    def _reach_args(
        self,
        declared_reach: Sequence[ToolReachGrant],
    ) -> tuple[list[str], list[str]]:
        args: list[str] = []
        credentials: list[str] = []
        network_grants = [
            grant
            for grant in declared_reach
            if (grant.kind.lower() in NETWORK_REACH_KINDS or grant.kind.lower().startswith("http"))
            and grant.access != "none"
        ]
        targeted_network = [grant.target for grant in network_grants if grant.target.strip()]
        if targeted_network:
            raise LearnedToolError(
                "contained learned-tool runner cannot enforce target-specific network reach "
                f"({', '.join(targeted_network)}); refusing to widen it to unrestricted egress"
            )
        narrow_network = [grant.access for grant in network_grants if grant.access != "read_write"]
        if narrow_network:
            raise LearnedToolError(
                "contained learned-tool runner can enforce only a broad network/read_write "
                "grant; refusing to treat unrestricted sockets as read-only or write-only"
            )
        network = (
            NETWORK_ALLOWED_DOCKER_NETWORK if network_grants else NETWORK_DENIED_DOCKER_NETWORK
        )
        args.append(f"--network={network}")

        for grant in declared_reach:
            kind = grant.kind.lower()
            if kind in _FILESYSTEM_REACH_KINDS:
                if grant.access == "none":
                    continue
                if grant.access not in {"read", "read_write"}:
                    raise LearnedToolError(
                        "contained learned-tool runner can enforce filesystem access only as "
                        f"read or read_write, not {grant.access!r}"
                    )
                target = self._filesystem_target(grant)
                args.extend(
                    [
                        "--mount",
                        self._bind_mount(
                            target,
                            target,
                            read_only=grant.access in _READ_ONLY_ACCESS,
                        ),
                    ]
                )
            elif kind in _CREDENTIAL_REACH_KINDS:
                if grant.access == "none":
                    continue
                if grant.access != "read":
                    raise LearnedToolError(f"credential reach {grant.target!r} must be read-only")
                name = grant.target.strip()
                if not _ENV_NAME_RE.fullmatch(name):
                    raise LearnedToolError(
                        "credential reach target must name one environment variable"
                    )
                if name not in os.environ:
                    raise LearnedToolError(
                        f"credential environment variable {name!r} is not available"
                    )
                credentials.append(name)
        return args, sorted(set(credentials))

    def _filesystem_target(self, grant: ToolReachGrant) -> Path:
        raw = grant.target.strip()
        if not raw:
            raise LearnedToolError("filesystem reach requires an exact target path")
        target = Path(raw)
        if not target.is_absolute():
            target = self._workspace_root / target
        try:
            target = target.resolve(strict=True)
        except FileNotFoundError as exc:
            raise LearnedToolError(
                f"filesystem reach target does not exist: {grant.target}"
            ) from exc
        if "," in str(target):
            raise LearnedToolError("filesystem reach paths containing ',' are unsupported")
        forbidden = (
            Path("/proc").resolve(),
            Path("/sys").resolve(),
            Path("/dev").resolve(),
            Path("/run").resolve(),
            Path("/var/run").resolve(),
            Path("/run/containerd").resolve(),
            Path("/var/run/docker.sock").resolve(),
        )
        if target == Path("/") or any(
            blocked == target or blocked.is_relative_to(target) or target.is_relative_to(blocked)
            for blocked in forbidden
        ):
            raise LearnedToolError(
                f"filesystem reach target crosses a container escape boundary: {target}"
            )
        return target

    def _base_docker_argv(self, *, name: str) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--init",
            "--pull=never",
            "--entrypoint=",
            "--name",
            name,
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={self._policy.pids_limit}",
            f"--memory={self._policy.memory}",
            f"--cpus={self._policy.cpus}",
            f"--user={os.getuid()}:{os.getgid()}",
            "--tmpfs",
            (
                f"{_CONTAINER_WORKDIR}:rw,noexec,nosuid,nodev,"
                f"size={self._policy.tmpfs_size},mode=1777"
            ),
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,nodev,size={self._policy.tmpfs_size},mode=1777",
            "--workdir",
            _CONTAINER_WORKDIR,
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTHONPYCACHEPREFIX=/tmp/pycache",
        ]

    @staticmethod
    def _bind_mount(source: Path, destination: Path, *, read_only: bool) -> str:
        mount = f"type=bind,src={source},dst={destination}"
        return f"{mount},readonly" if read_only else mount

    @staticmethod
    def _credential_args(names: Sequence[str]) -> list[str]:
        args: list[str] = []
        granted = set(names)
        # Docker can inject proxy variables from ~/.docker/config.json even
        # when the caller did not pass them. Blank those unless explicitly
        # present in the credential contract.
        for name in _DOCKER_PROXY_ENV:
            args.extend(["--env", name if name in granted else f"{name}="])
        for name in names:
            if name not in _DOCKER_PROXY_ENV:
                args.extend(["--env", name])
        return args

    async def _ensure_container_venv(
        self,
        tool_name: str,
        requirements: Sequence[str],
    ) -> Path | None:
        if not requirements:
            return None
        for requirement in requirements:
            if not requirement.strip() or requirement.lstrip().startswith("-"):
                raise LearnedToolError(f"unsafe learned-tool requirement argument: {requirement!r}")
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", tool_name.strip())
        if not safe_name:
            raise LearnedToolError("cannot provision a venv for an empty tool name")
        venv_dir = self._venvs_dir / safe_name
        stamp = venv_dir / TOOL_VENV_REQUIREMENTS_STAMP
        desired = "\n".join(requirements)
        lock = self._venv_locks.setdefault(str(venv_dir), asyncio.Lock())
        async with lock:
            if stamp.is_file() and stamp.read_text(encoding="utf-8") == desired:
                return venv_dir
            if venv_dir.exists():
                shutil.rmtree(venv_dir)
            venv_dir.mkdir(parents=True)
            cache_dir = self._venvs_dir / TOOL_VENV_UV_CACHE_DIRNAME
            cache_dir.mkdir(parents=True, exist_ok=True)
            created = await self._run_provision_step(
                venv_dir,
                cache_dir,
                ["python", "-m", "venv", _CONTAINER_VENV_PATH],
                network=NETWORK_DENIED_DOCKER_NETWORK,
            )
            if created.returncode != 0 or created.error or created.timed_out:
                shutil.rmtree(venv_dir, ignore_errors=True)
                raise LearnedToolError(
                    "contained learned-tool venv creation failed: " + self._process_error(created)
                )
            installed = await self._run_provision_step(
                venv_dir,
                cache_dir,
                [
                    f"{_CONTAINER_VENV_PATH}/bin/python",
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    *requirements,
                ],
                network=NETWORK_ALLOWED_DOCKER_NETWORK,
            )
            if installed.returncode != 0 or installed.error or installed.timed_out:
                shutil.rmtree(venv_dir, ignore_errors=True)
                raise LearnedToolError(
                    "contained learned-tool dependency install failed: "
                    + self._process_error(installed)
                )
            stamp.write_text(desired, encoding="utf-8")
            return venv_dir

    async def _run_provision_step(
        self,
        venv_dir: Path,
        cache_dir: Path,
        command: list[str],
        *,
        network: str,
    ) -> _ContainerProcessResult:
        name = f"ravn-tool-provision-{uuid.uuid4().hex[:16]}"
        argv = self._base_docker_argv(name=name)
        argv.extend(self._credential_args(()))
        argv.extend(
            [
                f"--network={network}",
                "--mount",
                self._bind_mount(venv_dir, Path(_CONTAINER_VENV_PATH), read_only=False),
                "--mount",
                self._bind_mount(cache_dir, Path("/opt/ravn/pip-cache"), read_only=False),
                "--env",
                "PIP_CACHE_DIR=/opt/ravn/pip-cache",
                self._policy.image,
                *command,
            ]
        )
        return await self._command_runner(
            argv,
            b"",
            self._provision_timeout_seconds,
            name,
        )

    def _tool_result(
        self,
        completed: _ContainerProcessResult,
        *,
        path: Path,
        timeout_seconds: float,
    ) -> ToolRunResult:
        if completed.timed_out:
            return ToolRunResult(
                ok=False,
                error=f"tool timed out after {timeout_seconds}s: {path.name}",
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        if completed.output_exceeded:
            return ToolRunResult(
                ok=False,
                error=f"tool output exceeded {self._output_limit_bytes} bytes: {path.name}",
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        if completed.error:
            return ToolRunResult(
                ok=False,
                error=f"contained learned-tool execution unavailable: {completed.error}",
                enforcement=REACH_ENFORCEMENT_UNAVAILABLE,
            )
        stderr = completed.stderr.decode("utf-8", errors="replace")[: self._output_limit_bytes]
        if completed.returncode != 0:
            return ToolRunResult(
                ok=False,
                error=f"contained tool exited with status {completed.returncode}: {path.name}",
                stderr=stderr,
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        if len(completed.stdout) > self._output_limit_bytes:
            return ToolRunResult(
                ok=False,
                error=f"tool output exceeded {self._output_limit_bytes} bytes: {path.name}",
                stderr=stderr,
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        try:
            result = json.loads(completed.stdout.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            return ToolRunResult(
                ok=False,
                error=f"contained tool produced non-JSON output: {exc}",
                stderr=stderr,
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        if not isinstance(result, dict):
            return ToolRunResult(
                ok=False,
                error=f"contained tool must return a JSON object, got {type(result).__name__}",
                stderr=stderr,
                enforcement=REACH_ENFORCEMENT_ENFORCED,
            )
        return ToolRunResult(
            ok=True,
            result=result,
            stderr=stderr,
            enforcement=REACH_ENFORCEMENT_ENFORCED,
        )

    @staticmethod
    def _process_error(result: _ContainerProcessResult) -> str:
        if result.timed_out:
            return "timed out"
        if result.error:
            return result.error
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        return stderr or f"container exited with status {result.returncode}"

    async def _run_docker(
        self,
        argv: Sequence[str],
        stdin: bytes,
        timeout_seconds: float,
        container_name: str,
    ) -> _ContainerProcessResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return _ContainerProcessResult(returncode=-1, error=str(exc))

        assert process.stdin is not None  # noqa: S101 - PIPE requested above
        assert process.stdout is not None  # noqa: S101 - PIPE requested above
        assert process.stderr is not None  # noqa: S101 - PIPE requested above
        output_exceeded = asyncio.Event()

        async def _read_bounded(stream: asyncio.StreamReader) -> bytes:
            retained = bytearray()
            while chunk := await stream.read(64 * 1024):
                room = self._output_limit_bytes - len(retained)
                if room > 0:
                    retained.extend(chunk[:room])
                if len(chunk) > room:
                    output_exceeded.set()
                # Continue draining after the ceiling. Otherwise the pipe can
                # block the container before the supervisor gets to kill it.
            return bytes(retained)

        async def _communicate_bounded() -> tuple[bytes, bytes, bool]:
            stdout_task = asyncio.create_task(_read_bounded(process.stdout))
            stderr_task = asyncio.create_task(_read_bounded(process.stderr))
            process.stdin.write(stdin)
            await process.stdin.drain()
            process.stdin.close()
            process_wait = asyncio.create_task(process.wait())
            overflow_wait = asyncio.create_task(output_exceeded.wait())
            done, _ = await asyncio.wait(
                {process_wait, overflow_wait},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if overflow_wait in done and output_exceeded.is_set() and not process_wait.done():
                process.kill()
                await process_wait
                await self._remove_container(container_name)
            else:
                overflow_wait.cancel()
            stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
            return stdout, stderr, output_exceeded.is_set()

        try:
            stdout, stderr, exceeded = await asyncio.wait_for(
                _communicate_bounded(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            await self._remove_container(container_name)
            return _ContainerProcessResult(returncode=124, timed_out=True)
        return _ContainerProcessResult(
            returncode=process.returncode or 0,
            stdout=stdout,
            stderr=stderr,
            output_exceeded=exceeded,
        )

    @staticmethod
    async def _remove_container(name: str) -> None:
        try:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "--force",
                name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=5.0)
        except Exception:  # noqa: BLE001 - best-effort cleanup of an exact run id
            logger.warning("failed to remove timed-out learned-tool container %s", name)


_CONTAINER_BOOTSTRAP = """
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("learned_tool", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload = json.load(sys.stdin)
result = getattr(module, sys.argv[2])(payload)
json.dump(result, sys.stdout)
"""


class ForgeSandboxLearnedToolRunner:
    """OPTIONAL containerized execution backend — only where Docker exists.

    Scope this honestly: this runner requires a Docker daemon, which a
    Kubernetes pod does not (and should not) have, so it is NOT the production
    security boundary. It exists
    for hosts/VMs that already run Docker, where it adds container isolation
    and network scoping (network-reach tools run with
    :data:`NETWORK_ALLOWED_DOCKER_NETWORK`, others with
    :data:`NETWORK_DENIED_DOCKER_NETWORK`; an injected shell whose network
    mode is unknowable records ``enforcement="unavailable"`` — never faked).

    The security boundaries that actually hold EVERYWHERE are upstream and
    downstream of execution: review/policy gating on declared reach before a
    tool is adopted, independent verification of its code, least-privilege
    short-lived credentials so a misbehaving tool's blast radius is bounded,
    the audit trail, and rollback. New deployments use
    :class:`ContainedLearnedToolRunner`; in Kubernetes it must be backed by a
    separate OCI execution service rather than mounting the runtime socket.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        shell: Any | None = None,
        docker_config: Any | None = None,
        runs_dir: str | Path | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._runs_dir = (
            Path(runs_dir) if runs_dir else self._workspace_root / ".ravn" / "tool_runs"
        )
        self._shell = shell
        self._docker_config = docker_config
        #: Self-managed shells, one per docker network mode, so a networked
        #: tool and an isolated tool never share a container.
        self._shells: dict[str, Any] = {}
        self._reach_warned: set[str] = set()
        #: One provisioning lock per tool so concurrent runs of the same tool
        #: cannot rebuild each other's in-container venv mid-install.
        self._venv_locks: dict[str, asyncio.Lock] = {}

    @property
    def enforces_reach(self) -> bool:
        """True when this runner can express network isolation for tool runs."""
        if self._shell is None:
            return True
        return _shell_network_mode(self._shell) is not None

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
        requirements: Sequence[str] = (),
        declared_reach: Sequence[ToolReachGrant] = (),
        host_call: HostCall | None = None,
    ) -> ToolRunResult:
        if host_call is not None:
            return ToolRunResult(
                ok=False,
                error=(
                    "this execution backend cannot provide the host SDK: the tool asks to "
                    "call the resident's own tools and there is no channel back from here. "
                    "Run it on the local backend, or rebuild it self-contained."
                ),
            )
        if not tool_path.resolve().is_relative_to(self._workspace_root):
            return ToolRunResult(
                ok=False,
                error=(
                    f"forge sandbox runner requires learned tool path inside workspace: {tool_path}"
                ),
            )
        python_executable = "python"
        if requirements:
            provisioned, provision_error = await self._ensure_sandbox_venv(
                tool_path,
                requirements,
                timeout_seconds=timeout_seconds,
            )
            if provisioned is None:
                # A tool that declares requirements must never silently run
                # without them — same posture as the local runner.
                return ToolRunResult(
                    ok=False,
                    error=(
                        f"forge sandbox venv provisioning failed for {tool_path.stem}: "
                        f"{provision_error}"
                    ),
                )
            python_executable = provisioned

        network_allowed = reach_allows_network(declared_reach)
        shell, enforcement = await self._shell_and_enforcement(
            network_allowed=network_allowed,
            timeout_seconds=timeout_seconds,
        )
        if enforcement == REACH_ENFORCEMENT_UNAVAILABLE and not network_allowed:
            self._warn_enforcement_unavailable(tool_path)

        run_dir = self._runs_dir / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=True)
        payload_path = run_dir / "payload.json"
        runner_path = run_dir / "runner.py"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        runner_path.write_text(_FORGE_RUNNER_SCRIPT, encoding="utf-8")

        command = _forge_runner_command(
            runner_path=runner_path,
            tool_path=tool_path,
            entry_point=entry_point,
            payload_path=payload_path,
            python_executable=python_executable,
        )
        try:
            output, exit_code = await shell.run(command)
        except Exception as exc:  # noqa: BLE001
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox execution failed: {exc}",
                enforcement=enforcement,
            )

        if exit_code != 0:
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool exited with status {exit_code}: {tool_path.name}",
                stderr=str(output),
                enforcement=enforcement,
            )
        try:
            result = json.loads(str(output))
        except json.JSONDecodeError as exc:
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool produced non-JSON output: {exc}",
                stderr=str(output),
                enforcement=enforcement,
            )
        if not isinstance(result, dict):
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool must return a JSON object, got {type(result).__name__}",
                stderr=str(output),
                enforcement=enforcement,
            )
        return ToolRunResult(ok=True, result=result, enforcement=enforcement)

    async def _shell_and_enforcement(
        self,
        *,
        network_allowed: bool,
        timeout_seconds: float,
    ) -> tuple[Any, str]:
        """Resolve the shell for this run and how honestly reach is enforced."""
        if self._shell is not None:
            # An injected shell cannot be reconfigured; report what its
            # network mode actually provides instead of pretending.
            return self._shell, _reach_enforcement(
                network=_shell_network_mode(self._shell),
                network_allowed=network_allowed,
            )

        mode = NETWORK_ALLOWED_DOCKER_NETWORK if network_allowed else NETWORK_DENIED_DOCKER_NETWORK
        shell = self._shells.get(mode)
        if shell is None:
            from ravn.adapters.tools.terminal_docker import DockerPersistentShell  # noqa: PLC0415

            shell = DockerPersistentShell(
                config=self._network_scoped_config(mode),
                workspace_root=self._workspace_root,
                timeout_seconds=timeout_seconds,
            )
            await shell.start()
            self._shells[mode] = shell
        return shell, _reach_enforcement(
            network=_shell_network_mode(shell),
            network_allowed=network_allowed,
        )

    def _network_scoped_config(self, network_mode: str) -> Any:
        from ravn.config import DockerTerminalConfig  # noqa: PLC0415

        base = self._docker_config or DockerTerminalConfig(image=DEFAULT_FORGE_SANDBOX_IMAGE)
        if hasattr(base, "model_copy"):
            return base.model_copy(update={"network": network_mode})
        # A config object we cannot re-scope is used as-is; the resulting
        # shell's actual network mode drives the enforcement marker honestly.
        return base

    def _warn_enforcement_unavailable(self, tool_path: Path) -> None:
        key = tool_path.stem
        if key in self._reach_warned:
            return
        self._reach_warned.add(key)
        logger.warning(
            "learned tool %s declares no network reach, but the forge sandbox "
            "shell cannot express network isolation — enforcement recorded as "
            "unavailable, never faked",
            key,
        )

    async def _ensure_sandbox_venv(
        self,
        tool_path: Path,
        requirements: Sequence[str],
        *,
        timeout_seconds: float,
    ) -> tuple[str | None, str]:
        """Provision the tool's venv inside the sandbox; return (python, error).

        The venv lives at ``{workspace}/.ravn/tool_venvs/{tool}`` on the shared
        workspace mount. Provisioning always runs through the NETWORKED shell —
        pip needs egress — while the tool run itself stays on its reach-scoped
        shell; the run container sees the venv through the mount. A stamp file
        (same convention as the local runner) makes an unchanged requirement
        list a no-op. Returns ``(None, error)`` when provisioning fails — a
        tool must never silently run without its dependencies.
        """
        stem = tool_path.stem
        venv_dir = self._workspace_root / ".ravn" / "tool_venvs" / stem
        stamp_path = venv_dir / TOOL_VENV_REQUIREMENTS_STAMP
        desired_stamp = "\n".join(requirements)
        python = str(venv_dir / "bin" / "python")

        lock = self._venv_locks.setdefault(stem, asyncio.Lock())
        async with lock:
            if stamp_path.is_file() and stamp_path.read_text(encoding="utf-8") == desired_stamp:
                return python, ""

            shell, _ = await self._shell_and_enforcement(
                network_allowed=True,
                timeout_seconds=timeout_seconds,
            )
            command = _forge_venv_provision_command(
                venv_dir=venv_dir,
                requirements=requirements,
            )
            try:
                output, exit_code = await shell.run(command)
            except Exception as exc:  # noqa: BLE001 — provisioning failure is the error result
                return None, str(exc)
            if exit_code != 0:
                return None, str(output)
            # The stamp is written last (host side, shared mount) so a killed
            # provisioning run never masquerades as a complete one.
            stamp_path.parent.mkdir(parents=True, exist_ok=True)
            stamp_path.write_text(desired_stamp, encoding="utf-8")
            return python, ""


def _shell_network_mode(shell: Any) -> str | None:
    """The docker network mode a shell runs with, or None when unknowable."""
    network = getattr(getattr(shell, "_config", None), "network", None)
    if network is None:
        return None
    return str(network)


def _reach_enforcement(*, network: str | None, network_allowed: bool) -> str:
    """Enforcement marker for a shell whose docker network mode is ``network``.

    ``None`` means the shell cannot tell us its mode — enforcement is honestly
    unavailable, never assumed. A tool granted network reach is satisfied by
    any known mode; a tool without network reach is only enforced when the
    container network is actually off.
    """
    if network is None:
        return REACH_ENFORCEMENT_UNAVAILABLE
    if network_allowed:
        return REACH_ENFORCEMENT_ENFORCED
    if network == NETWORK_DENIED_DOCKER_NETWORK:
        return REACH_ENFORCEMENT_ENFORCED
    return REACH_ENFORCEMENT_UNAVAILABLE


def require_verified_artifact(artifact: LearnedToolArtifact) -> None:
    """Raise unless *artifact* carries a passing independent verification.

    Adoption is supposed to verify, but a second path installed tools without
    it: of 105 artifacts on one resident, 28 carried no verification record at
    all. An unverified tool is not merely unproven — the ones observed here
    call a host SDK that does not exist, swallow the ImportError, and return
    ``None``, so they report success while doing nothing. Refusing to load
    them turns a silent no-op back into a visible failure.
    """
    verification = artifact.provenance.get("verification")
    name = artifact.manifest.name
    if verification is None:
        raise LearnedToolError(
            f"learned tool {name!r} has no verification record and will not be run. "
            f"Rebuild it with build_tool, which verifies before installing."
        )
    if not isinstance(verification, dict) or verification.get("ok") is not True:
        detail = ""
        if isinstance(verification, dict):
            detail = str(verification.get("error") or verification.get("summary") or "").strip()
        raise LearnedToolError(
            f"learned tool {name!r} failed verification and will not be run"
            + (f": {detail}" if detail else "")
        )


class LearnedTool(ToolPort):
    """Expose a resident-authored artifact through the normal agent tool port."""

    def __init__(
        self,
        *,
        manifest: LearnedToolManifest,
        tool_path: str | Path,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        runner: LearnedToolRunner | None = None,
        requirements: Sequence[str] = (),
        host_call: HostCall | None = None,
    ) -> None:
        _validate_manifest(manifest)
        self._manifest = manifest
        self._tool_path = Path(tool_path)
        self._timeout_seconds = timeout_seconds
        self._runner = runner or LocalLearnedToolRunner()
        self._requirements = list(requirements)
        self._host_call = host_call

    @property
    def name(self) -> str:
        return self._manifest.name

    @property
    def description(self) -> str:
        return self._manifest.description

    @property
    def input_schema(self) -> dict:
        return dict(self._manifest.input_schema)

    @property
    def required_permission(self) -> str:
        return self._manifest.required_permission

    @property
    def manifest(self) -> LearnedToolManifest:
        return self._manifest

    @property
    def tool_path(self) -> Path:
        return self._tool_path

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        result = await self._runner.run(
            self._tool_path,
            input,
            entry_point=self._manifest.entry_point,
            timeout_seconds=self._timeout_seconds,
            requirements=self._requirements,
            declared_reach=self._manifest.declared_reach,
            host_call=self._host_call,
        )
        if not result.ok:
            detail = result.error
            if result.stderr:
                detail = f"{detail}\n{result.stderr}"
            return ToolResult(tool_call_id="", content=detail, is_error=True)
        return ToolResult(
            tool_call_id="",
            content=json.dumps(result.result, indent=2, sort_keys=True),
        )


def write_learned_tool(
    *,
    tools_dir: str | Path,
    artifact: LearnedToolArtifact,
) -> Path:
    """Persist an artifact's code at the conventional learned-tool path."""
    _validate_artifact(artifact)
    return write_tool(
        tools_dir=tools_dir,
        skill_name=_filename_for_tool(artifact.manifest.name),
        tool_code=artifact.tool_code,
    )


def write_learned_tool_artifact(
    *,
    artifacts_dir: str | Path,
    artifact: LearnedToolArtifact,
) -> Path:
    """Persist the full manifest + code envelope for review or flock exchange.

    Version chain (P6.3): when the tool already has a persisted artifact with
    a different ``artifact_id``, the previous envelope is preserved under
    :func:`superseded_artifact_path` and the new artifact's ``supersedes``
    field is linked to it automatically — callers never need to know about
    the previous version.
    """
    _validate_artifact(artifact)
    directory = Path(artifacts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = learned_tool_artifact_path(directory, artifact.manifest.name)
    artifact = _link_superseded_artifact(path, artifact, artifacts_dir=directory)
    path.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _link_superseded_artifact(
    path: Path,
    artifact: LearnedToolArtifact,
    *,
    artifacts_dir: Path,
) -> LearnedToolArtifact:
    """Archive the previous version of a tool's envelope and link the chain."""
    if not path.is_file():
        # No envelope under this name — but a rename is still a new version of
        # something. Chaining was keyed purely on the name-derived path, so
        # list_k8s_pods -> list_k8s_pod_names looked like a first build and the
        # predecessor vanished from the chain. When the resident declared what
        # capability the tool serves, that is the better key.
        return _link_by_capability(artifact, artifacts_dir=artifacts_dir)
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LearnedToolError(
            f"existing artifact envelope for {artifact.manifest.name!r} is not valid "
            f"JSON ({exc}); refusing to silently overwrite the version chain: {path}"
        ) from exc
    previous_id = str(previous.get("artifact_id") or "")
    previous_supersedes = str(previous.get("supersedes") or "")
    if not previous_id or previous_id == artifact.artifact_id:
        if previous_supersedes and not artifact.supersedes:
            # Rewriting the same version (e.g. refreshed provenance) must not
            # erase the chain link the previous write established.
            return replace(artifact, supersedes=previous_supersedes)
        return artifact

    archive_path = superseded_artifact_path(artifacts_dir, artifact.manifest.name, previous_id)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(json.dumps(previous, indent=2, sort_keys=True), encoding="utf-8")
    if artifact.supersedes:
        return artifact
    if previous_supersedes == artifact.artifact_id:
        # Writing back the exact predecessor the current version superseded is
        # a rollback restore, not a new version — linking it would create a
        # two-artifact cycle that ping-pongs forever.
        return artifact
    return replace(artifact, supersedes=previous_id)


def find_installed_duplicate(
    *,
    artifacts_dir: str | Path,
    tools_dir: str | Path,
    artifact: LearnedToolArtifact,
) -> LearnedToolArtifact | None:
    """Return an installed artifact this one would merely re-create, if any.

    Residents rebuilt tools they already owned and, twice in one 41-minute
    stretch, produced byte-identical code under a new name. Nothing noticed:
    each build wrote a fresh envelope, so the catalog grew and the same work was
    paid for again.

    Identity is the code plus the behavioural contract — input schema, required
    permission, declared reach, entry point. Name and description are labels: a
    rename with identical code is the same tool wearing a new label, which is
    precisely one of the duplications seen. A changed schema or permission is a
    real revision and must still be written.
    """
    artifacts_path = Path(artifacts_dir)
    if not artifact.tool_code.strip() or not artifacts_path.is_dir():
        return None
    contract = _manifest_contract(artifact.manifest)
    for candidate_path in sorted(artifacts_path.glob("*.json")):
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("tool_code") != artifact.tool_code:
            continue
        candidate = LearnedToolArtifact.from_dict(payload)
        if candidate.artifact_id == artifact.artifact_id:
            continue
        if _manifest_contract(candidate.manifest) != contract:
            continue
        # Only an artifact whose code is actually on disk can be run instead;
        # one persisted after a failed verification is not a usable answer.
        if not learned_tool_path(tools_dir, candidate.manifest.name).is_file():
            continue
        return candidate
    return None


def find_installed_capability(
    *,
    artifacts_dir: str | Path,
    tools_dir: str | Path,
    capability_id: str = "",
    name: str = "",
) -> LearnedToolArtifact | None:
    """Find an installed tool already serving *capability_id* or called *name*.

    Answerable before any code exists, which is the point: the code-level
    duplicate check can only run on a finished build, and a commissioned build
    is a Ting workflow that has already taken half an hour by then. Declared
    intent — the capability being asked for, or the name being asked for — is
    all that is available up front, and it is enough to stop the common case.

    Capability match is preferred over name match: it survives a rename, which
    is the duplication that name-keyed logic misses entirely.
    """
    artifacts_path = Path(artifacts_dir)
    if not artifacts_path.is_dir():
        return None
    wanted_capability = capability_id.strip()
    wanted_name = name.strip()
    if not wanted_capability and not wanted_name:
        return None

    by_name: LearnedToolArtifact | None = None
    for candidate_path in sorted(artifacts_path.glob("*.json")):
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        candidate = LearnedToolArtifact.from_dict(payload)
        if not learned_tool_path(tools_dir, candidate.manifest.name).is_file():
            continue
        if wanted_capability and candidate.source_gap_id.strip() == wanted_capability:
            return candidate
        if wanted_name and candidate.manifest.name == wanted_name and by_name is None:
            by_name = candidate
    return by_name


def _manifest_contract(manifest: LearnedToolManifest) -> str:
    """What a caller is promised, ignoring what the tool happens to be called."""
    return json.dumps(
        {
            "input_schema": manifest.input_schema,
            "output_schema": manifest.output_schema,
            "required_permission": manifest.required_permission,
            "entry_point": manifest.entry_point,
            "artifact_type": manifest.artifact_type,
            "declared_reach": [grant.to_dict() for grant in manifest.declared_reach],
        },
        sort_keys=True,
    )


def _link_by_capability(
    artifact: LearnedToolArtifact,
    *,
    artifacts_dir: Path,
) -> LearnedToolArtifact:
    """Link a differently-named artifact to the last one serving the same need.

    Only ever reads: the predecessor keeps its own envelope and stays installed.
    All this establishes is that the new artifact knows what it follows, so
    ``supersedes`` can be walked back for rollback and review.
    """
    capability_id = artifact.source_gap_id.strip()
    if not capability_id or artifact.supersedes:
        return artifact

    newest: tuple[str, str] = ("", "")
    for candidate_path in sorted(artifacts_dir.glob("*.json")):
        try:
            payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            # A neighbouring envelope being unreadable is not this write's
            # problem; the worst outcome is the chain link we could not prove.
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("source_gap_id") or "").strip() != capability_id:
            continue
        candidate_id = str(payload.get("artifact_id") or "")
        if not candidate_id or candidate_id == artifact.artifact_id:
            continue
        created_at = str(payload.get("created_at") or "")
        if created_at >= newest[0]:
            newest = (created_at, candidate_id)

    if not newest[1]:
        return artifact
    return replace(artifact, supersedes=newest[1])


def read_learned_tool_artifact(path: str | Path) -> LearnedToolArtifact:
    """Load a persisted learned-tool artifact envelope."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    artifact = LearnedToolArtifact.from_dict(payload)
    _validate_artifact(artifact)
    return artifact


def load_learned_tool(
    *,
    artifact: LearnedToolArtifact,
    tool_path: str | Path,
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    runner: LearnedToolRunner | None = None,
    requirements: list[str] | None = None,
    venvs_dir: str | Path | None = None,
    host_call: HostCall | None = None,
) -> LearnedTool:
    """Create an agent-callable ToolPort from a learned artifact.

    ``requirements`` defaults to the artifact's own requirement list; when the
    list is non-empty and no runner is supplied, the local runner provisions a
    per-tool venv under ``venvs_dir`` and executes with its python.
    """
    resolved_requirements = (
        list(artifact.requirements) if requirements is None else list(requirements)
    )
    if runner is None:
        runner = LocalLearnedToolRunner(venvs_dir=venvs_dir)
    return LearnedTool(
        manifest=artifact.manifest,
        tool_path=tool_path,
        timeout_seconds=timeout_seconds,
        runner=runner,
        requirements=resolved_requirements,
        host_call=host_call,
    )


def learned_tool_path(tools_dir: str | Path, tool_name: str) -> Path:
    """Return the conventional code path for a learned tool name."""
    return Path(tools_dir) / f"{_filename_for_tool(tool_name)}.py"


def learned_tool_artifact_path(artifacts_dir: str | Path, tool_name: str) -> Path:
    """Return the conventional envelope path for a learned tool name."""
    return Path(artifacts_dir) / f"{_filename_for_tool(tool_name)}.json"


#: Subdirectory of the artifacts dir where superseded envelope versions are
#: preserved. Deliberately not matched by the daemon loader's ``*.json`` glob.
SUPERSEDED_ARTIFACTS_DIRNAME = "_archive"


def superseded_artifact_path(
    artifacts_dir: str | Path,
    tool_name: str,
    artifact_id: str,
) -> Path:
    """Where a superseded version of a tool's envelope is preserved on disk."""
    stem = _filename_for_tool(tool_name)
    suffix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", artifact_id)
    return Path(artifacts_dir) / SUPERSEDED_ARTIFACTS_DIRNAME / f"{stem}__{suffix}.json"


def _validate_artifact(artifact: LearnedToolArtifact) -> None:
    _validate_manifest(artifact.manifest)
    _validate_tool_code(artifact)


def _validate_manifest(manifest: LearnedToolManifest) -> None:
    if manifest.artifact_type != "agent_tool":
        raise LearnedToolError(f"unsupported learned tool artifact type: {manifest.artifact_type}")
    if not _TOOL_NAME_RE.fullmatch(manifest.name):
        raise LearnedToolError(f"invalid learned tool name: {manifest.name!r}")
    if not manifest.description.strip():
        raise LearnedToolError(f"learned tool {manifest.name!r} is missing a description")
    if not isinstance(manifest.input_schema, dict) or not manifest.input_schema:
        raise LearnedToolError(f"learned tool {manifest.name!r} is missing input_schema")
    if not manifest.required_permission.strip():
        raise LearnedToolError(f"learned tool {manifest.name!r} is missing required_permission")
    if not _ENTRY_POINT_RE.fullmatch(manifest.entry_point):
        raise LearnedToolError(
            f"invalid entry point for {manifest.name!r}: {manifest.entry_point!r}"
        )
    for grant in manifest.declared_reach:
        if not grant.kind.strip():
            raise LearnedToolError(f"learned tool {manifest.name!r} has an empty reach kind")
        if grant.access not in _REACH_ACCESS:
            raise LearnedToolError(
                f"learned tool {manifest.name!r} declares unsupported reach access {grant.access!r}"
            )


def _validate_tool_code(artifact: LearnedToolArtifact) -> None:
    if not artifact.tool_code.strip():
        raise LearnedToolError(f"learned tool {artifact.manifest.name!r} has empty code")
    findings = tool_implementation_findings(
        artifact.tool_code,
        entry_point=artifact.manifest.entry_point,
    )
    if findings:
        raise LearnedToolError("; ".join(findings))


def tool_implementation_findings(
    tool_code: str,
    *,
    entry_point: str = "run",
) -> list[str]:
    """Structurally validate a tool implementation; return blocking findings.

    Structure only: the code parses and defines the declared entry point. A
    tool's capability is gated by its ``declared_reach`` at review time and
    enforced at the execution sandbox boundary — never by a Python-import
    allowlist. Such an allowlist blocks an honest ``import httpx`` while a
    determined tool reaches the same capability through ``__import__`` or
    ``os.system``; a bypassable check is worse than none because it reads as
    safety that is not actually there.
    """
    try:
        tree = ast.parse(tool_code)
    except SyntaxError as exc:
        return [f"tool implementation has a syntax error: {exc}"]

    entry_points = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == entry_point
    ]
    if not entry_points:
        return [f"tool implementation does not define {entry_point}()"]
    return []


def _filename_for_tool(tool_name: str) -> str:
    return tool_name.replace(".", "_").replace("-", "_")


_TOOL_NAME_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.-]{0,127}")
_ENTRY_POINT_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]{0,63}")
_REACH_ACCESS = frozenset({"none", "read", "write", "read_write", "execute", "admin"})

#: Access levels that mutate the world — a learned tool that declares any of
#: them is "mutating" (medium risk to the autonomy ladder).
MUTATING_REACH_ACCESS = frozenset({"write", "read_write", "execute", "admin"})

#: Map a declared reach kind to the AutonomyPolicy hard-gated boundary it
#: crosses. A learned tool's reach is gated by the one policy (via these
#: boundaries + its mutating access level), never a parallel allow/deny list.
#: Reach kinds absent here are gated by access level and autonomy mode alone.
_REACH_BOUNDARY = {
    "credential": "credentials",
    "credentials": "credentials",
    "secret": "credentials",
    "secrets": "credentials",
    "billing": "spending",
    "spending": "spending",
    "destructive": "destructive",
    "external_send": "external_send",
    "external_write": "external_send",
    "admin": "authority_expansion",
}


#: Execution backends the resolver accepts; anything else is a config error.
KNOWN_EXECUTION_BACKENDS = frozenset({"", "container", "local", "forge", "devrunner", "k8s_job"})


def learned_tool_runner_for_backend(
    execution_backend: str,
    *,
    workspace_root: str | Path,
    venvs_dir: str | Path,
    sandbox_shell: Any | None = None,
    backend_kwargs: Mapping[str, Any] | None = None,
) -> LearnedToolRunner:
    """Build the configured runner at the composition boundary.

    ``container`` is the fail-closed production path. ``local`` and the old
    persistent ``forge``/``devrunner`` runner remain explicit compatibility
    choices, but neither is silently selected for autonomous execution.
    """
    if execution_backend == "container":
        return ContainedLearnedToolRunner(
            workspace_root=workspace_root,
            venvs_dir=venvs_dir,
        )
    if execution_backend in {"", "local"}:
        return LocalLearnedToolRunner(venvs_dir=venvs_dir)
    if execution_backend in {"forge", "devrunner"}:
        return ForgeSandboxLearnedToolRunner(
            workspace_root=workspace_root,
            shell=sandbox_shell,
        )
    if execution_backend == "k8s_job":
        from ravn.valkyrie_evolution.k8s_tool_runner import (  # noqa: PLC0415
            KubernetesJobExecutor,
            KubernetesJobLearnedToolRunner,
        )

        kwargs = dict(backend_kwargs or {})
        executor = KubernetesJobExecutor(**kwargs)
        return KubernetesJobLearnedToolRunner(
            executor=executor,
            image=str(kwargs.get("image") or DEFAULT_CONTAINED_TOOL_IMAGE),
        )
    raise LearnedToolError(f"unknown learned tool execution backend: {execution_backend!r}")


class LearnedToolResolver:
    """Resolve persisted learned tools from resident state on demand.

    One resolver owns the storage conventions and execution-backend selection
    for a resident's learned tools, so every consumer — the legacy bulk loader,
    the ``learned_tool_run`` dispatch tool, and the capability catalog — sees
    the same tool in the same place with the same runner. Listing reads only
    the artifact envelopes; a callable :class:`LearnedTool` is constructed
    per-name in :meth:`load`, never eagerly for the whole catalog (NIU-1118).
    """

    def __init__(
        self,
        *,
        state_dir: str | Path,
        execution_backend: str = "local",
        workspace_root: str | Path | None = None,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        execution_backend_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        if execution_backend not in KNOWN_EXECUTION_BACKENDS:
            raise LearnedToolError(f"unknown learned tool execution backend: {execution_backend!r}")
        self._state_dir = Path(state_dir)
        self._execution_backend = execution_backend
        self._workspace_root = Path(workspace_root) if workspace_root else self._state_dir.parent
        self._timeout_seconds = timeout_seconds
        self._execution_backend_kwargs = dict(execution_backend_kwargs or {})
        self._code_dir, self._artifacts_dir = learned_tool_storage(self._state_dir)
        self._venvs_dir = learned_tool_venvs_dir(self._state_dir)

    @property
    def artifacts_dir(self) -> Path:
        return self._artifacts_dir

    def sweep_orphaned_venvs(self) -> None:
        """Garbage-collect venvs whose tool code is gone. Never raises."""
        from ravn.valkyrie_evolution.tool_runtime import prune_orphaned_tool_venvs  # noqa: PLC0415

        try:
            pruned = prune_orphaned_tool_venvs(venvs_dir=self._venvs_dir, tools_dir=self._code_dir)
            if pruned:
                logger.info("Pruned %d orphaned learned-tool venv(s): %s", len(pruned), pruned)
        except Exception as exc:  # noqa: BLE001 — venv GC must never block startup
            logger.warning("Learned-tool venv sweep failed: %s", exc)

    def list_artifacts(self) -> list[LearnedToolArtifact]:
        """Return every loadable artifact envelope, without building callables.

        Envelopes that fail validation are skipped with a warning — a broken
        artifact must not hide the rest of the catalog. One whose code file is
        missing is removed: it can never execute, and left in place it keeps a
        capability looking installable that never resolves.
        """
        if not self._artifacts_dir.exists():
            return []
        artifacts: list[LearnedToolArtifact] = []
        for artifact_file in sorted(self._artifacts_dir.glob("*.json")):
            try:
                artifact = read_learned_tool_artifact(artifact_file)
            except Exception as exc:
                logger.warning("Failed to read learned tool artifact %s: %s", artifact_file, exc)
                continue
            if not self._tool_path(artifact.manifest.name).exists():
                # An artifact with no code file can never execute, and leaving
                # it in place is not harmless: the capability it claims never
                # resolves, so the resident sees the same gap on every sweep
                # and commissions the same build again. One such orphan drove
                # 34 rebuilds of the same tool over five days. Reap it, and say
                # so once, rather than warning about it every minute forever.
                logger.warning(
                    "Learned tool %s has an artifact but no code file; removing "
                    "the orphaned artifact so its capability stops reading as "
                    "installable",
                    artifact.manifest.name,
                )
                artifact_file.unlink(missing_ok=True)
                continue
            artifacts.append(artifact)
        return artifacts

    def load(self, name: str, *, host_call: HostCall | None = None) -> LearnedTool:
        """Load one learned tool as an executable ToolPort, by manifest name.

        Raises :class:`LearnedToolError` when the tool does not exist or its
        artifact cannot be validated.
        """
        if not _TOOL_NAME_RE.fullmatch(name):
            raise LearnedToolError(f"invalid learned tool name: {name!r}")
        artifact_path = learned_tool_artifact_path(self._artifacts_dir, name)
        if not artifact_path.is_file():
            raise LearnedToolError(f"no learned tool named {name!r} is installed")
        artifact = read_learned_tool_artifact(artifact_path)
        require_verified_artifact(artifact)
        tool_path = self._tool_path(artifact.manifest.name)
        if not tool_path.exists():
            raise LearnedToolError(f"learned tool {name!r} has no code file at {tool_path}")
        return load_learned_tool(
            artifact=artifact,
            tool_path=tool_path,
            timeout_seconds=self._timeout_seconds,
            runner=self._runner(),
            venvs_dir=self._venvs_dir,
            host_call=host_call,
        )

    def _tool_path(self, name: str) -> Path:
        return learned_tool_path(self._code_dir, name)

    def _runner(self) -> LearnedToolRunner | None:
        return learned_tool_runner_for_backend(
            self._execution_backend,
            workspace_root=self._workspace_root,
            venvs_dir=self._venvs_dir,
            backend_kwargs=self._execution_backend_kwargs,
        )


def learned_tool_storage(state_dir: str | Path) -> tuple[Path, Path]:
    """Return the one canonical (code_dir, artifacts_dir) for learned tools.

    Every writer (build_tool authoring, peer-adoption install) and the daemon
    loader resolve the location here so a learned tool lives in exactly one
    place on disk.
    """
    base = Path(state_dir)
    return base / "learned_tools", base / "learned_tool_artifacts"


def learned_tool_venvs_dir(state_dir: str | Path) -> Path:
    """The one canonical per-tool venvs directory for learned tools.

    Lives beside :func:`learned_tool_storage` so a tool's code, envelope, and
    dependency environment share the same state root.
    """
    return Path(state_dir) / "learned_tool_venvs"


def manifest_safety_class(manifest: LearnedToolManifest) -> str:
    """Map a manifest's declared reach to a coarse safety class."""
    if any(grant.access in MUTATING_REACH_ACCESS for grant in manifest.declared_reach):
        return "mutating"
    return "read_only"


def manifest_review_boundaries(manifest: LearnedToolManifest) -> list[str]:
    """Hard-gated autonomy boundaries a learned tool's declared reach crosses.

    Fed into the one AutonomyPolicy by the reviewer so a tool that reads
    credentials, spends, or sends outbound is gated the same way every other
    self-improvement is — not by a build_tool-local list.
    """
    boundaries: set[str] = set()
    for grant in manifest.declared_reach:
        boundary = _REACH_BOUNDARY.get(grant.kind.lower())
        if boundary:
            boundaries.add(boundary)
    return sorted(boundaries)


_FORGE_RUNNER_SCRIPT = """
import importlib.util
import json
import sys

tool_path, entry_point, payload_path = sys.argv[1:4]
spec = importlib.util.spec_from_file_location("learned_tool", tool_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload = json.loads(open(payload_path, encoding="utf-8").read())
result = getattr(module, entry_point)(payload)
json.dump(result, sys.stdout)
"""


def _forge_runner_command(
    *,
    runner_path: Path,
    tool_path: Path,
    entry_point: str,
    payload_path: Path,
    python_executable: str = "python",
) -> str:
    parts = [
        python_executable,
        "-I",
        str(runner_path),
        str(tool_path),
        entry_point,
        str(payload_path),
    ]
    return " ".join(shlex.quote(part) for part in parts)


def _forge_venv_provision_command(*, venv_dir: Path, requirements: Sequence[str]) -> str:
    """Shell command that (re)builds a per-tool venv inside the sandbox container.

    The venv lives on the shared workspace mount so the network-scoped run
    container sees what the networked provisioning container installed.
    Prefers uv (the devrunner image ships it) with a shared hardlink cache
    beside the venvs, so tools sharing a package cost one copy on disk; falls
    back to stock venv+pip when uv is absent.
    """
    venv = shlex.quote(str(venv_dir))
    python = shlex.quote(str(venv_dir / "bin" / "python"))
    cache = shlex.quote(str(venv_dir.parent / TOOL_VENV_UV_CACHE_DIRNAME))
    install = " ".join(shlex.quote(req) for req in requirements)
    return (
        f"rm -rf {venv} && "
        f"if command -v uv >/dev/null 2>&1; then "
        f"uv venv {venv} && "
        f"UV_CACHE_DIR={cache} UV_LINK_MODE=hardlink "
        f"uv pip install --python {python} {install}; "
        f"else "
        f"python -m venv {venv} && "
        f"{python} -m pip install --disable-pip-version-check {install}; "
        f"fi"
    )
