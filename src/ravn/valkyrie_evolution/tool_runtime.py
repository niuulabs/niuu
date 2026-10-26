"""Sandboxed execution for resident-built tool implementations.

A Valkyrie-built tool is a single Python module exposing an entry-point
function (default ``run``) that takes the signal payload dict and returns a
JSON-serializable judgment dict.  Tools execute out-of-process with an
isolated interpreter (``python -I``) and a hard timeout, so a broken or slow
self-built probe cannot take the resident down with it.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
DEFAULT_TOOL_OUTPUT_LIMIT_BYTES = 256 * 1024

#: How long ``pip install`` of a learned tool's requirements into its
#: dedicated venv may take before provisioning fails loudly.
DEFAULT_TOOL_VENV_PIP_TIMEOUT_SECONDS = 300.0

#: How long ``python -m venv`` creation of a per-tool venv may take.
DEFAULT_TOOL_VENV_CREATE_TIMEOUT_SECONDS = 120.0

#: Stamp file inside a per-tool venv recording the exact requirements it was
#: provisioned with. A matching stamp makes re-provisioning a no-op; a
#: mismatch rebuilds the venv from scratch.
TOOL_VENV_REQUIREMENTS_STAMP = ".requirements.txt"

#: Environment variables a tool subprocess is allowed to inherit. Everything
#: else — bearer tokens, PATs, cloud credentials in the resident's ambient
#: environment — is withheld.
#:
#: Be honest about what this is: hygiene against ACCIDENTAL leakage (tool code
#: that dumps os.environ into results, logs, or an HTTP call — a common LLM
#: failure mode), proven by test to keep secrets out of the child env. It is
#: NOT a wall against a deliberately malicious tool: the subprocess runs as
#: the same user, so files (mounted token paths, state dirs) remain readable.
#: Containment of hostile learned code comes from
#: ``ContainedLearnedToolRunner``. This environment remains only the explicit
#: local compatibility path and independent verifier hygiene.
#:
#: The TLS/proxy entries are non-secret transport config a corporate
#: deployment needs for any outbound call. This is the ONE env policy —
#: verification (tool_verification) and execution both import it so the two
#: can never drift.
SANDBOX_ENV_PASSTHROUGH = (
    "PATH",
    "SYSTEMROOT",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)

#: Name of the host-call module the sandbox exposes. Residents were already
#: writing ``from ravn.sdk import tool`` against an SDK that did not exist and
#: swallowing the ImportError, so the tool reported success while returning
#: nothing. This makes the module they reach for real.
HOST_SDK_MODULE = "ravn.sdk"

#: Environment variable carrying the inherited host-call socket descriptor.
HOST_CALL_FD_ENV = "RAVN_HOST_CALL_FD"

#: ``ToolRunResult.enforcement`` values (mirrored by the learned-tool runners).
_REACH_ENFORCEMENT_ENFORCED = "enforced"
_REACH_ENFORCEMENT_UNAVAILABLE = "unavailable"

#: Exit status the network-isolation prelude uses when the kernel refuses to
#: create the tool's network namespace. Only trusted together with
#: :data:`NETWORK_ISOLATION_FAILURE_MARKER` on stderr.
NETWORK_ISOLATION_FAILURE_EXIT = 97

#: stderr prefix the prelude writes before exiting with
#: :data:`NETWORK_ISOLATION_FAILURE_EXIT`.
NETWORK_ISOLATION_FAILURE_MARKER = "ravn-sandbox: network isolation failed:"

#: Linux: runs before any tool code for a tool that declares no network
#: reach. The process moves into a fresh user namespace (so no privilege is
#: needed) and a fresh network namespace that holds only a downed loopback:
#: every IP socket the tool opens afterwards fails with "Network is
#: unreachable", and name resolution fails. The tool cannot leave it again —
#: re-joining the pod's network namespace needs CAP_SYS_ADMIN in the
#: namespace that owns it, which an unprivileged process never has. The
#: uid/gid maps keep the tool's own uid so file ownership reads the same
#: inside as out. Unix sockets reached by filesystem path are files, not
#: network, and stay as reachable as the rest of the filesystem.
_LINUX_NETWORK_ISOLATION_PRELUDE = f"""
import os as _os
import sys as _sys

try:
    _uid, _gid = _os.getuid(), _os.getgid()
    _os.unshare(_os.CLONE_NEWUSER | _os.CLONE_NEWNET)
    with open("/proc/self/setgroups", "w") as _f:
        _f.write("deny")
    with open("/proc/self/uid_map", "w") as _f:
        _f.write(f"{{_uid}} {{_uid}} 1")
    with open("/proc/self/gid_map", "w") as _f:
        _f.write(f"{{_gid}} {{_gid}} 1")
except Exception as _exc:
    _sys.stderr.write(
        "{NETWORK_ISOLATION_FAILURE_MARKER} " + type(_exc).__name__ + ": " + str(_exc) + "\\n"
    )
    _sys.stderr.flush()
    _os._exit({NETWORK_ISOLATION_FAILURE_EXIT})
"""

#: macOS: the Seatbelt profile a no-network tool runs under. It denies every
#: socket operation (IP and unix-domain alike) and allows everything else; the
#: profile is inherited by any process the tool starts and cannot be lifted.
_DARWIN_SANDBOX_EXEC = "/usr/bin/sandbox-exec"
_DARWIN_NO_NETWORK_PROFILE = "(version 1)(allow default)(deny network*)"

#: stderr prefix ``sandbox-exec`` writes when it cannot apply the profile.
_DARWIN_SANDBOX_FAILURE_PREFIX = "sandbox-exec:"

#: Proves the boundary from inside it: a connect to the loopback must fail
#: with a policy error, not "connection refused" (which would mean the
#: network is still there and merely nothing listens).
_NETWORK_ISOLATION_CHECK = f"""
import errno as _errno
import socket as _socket
import sys as _sys

_probe = _socket.socket()
try:
    _probe.connect(("127.0.0.1", 9))
except OSError as _exc:
    if _exc.errno in (_errno.ENETUNREACH, _errno.EPERM, _errno.EACCES):
        _sys.exit(0)
    _sys.stderr.write("{NETWORK_ISOLATION_FAILURE_MARKER} loopback still reachable: " + str(_exc))
    _sys.exit({NETWORK_ISOLATION_FAILURE_EXIT})
_sys.stderr.write("{NETWORK_ISOLATION_FAILURE_MARKER} loopback connect succeeded")
_sys.exit({NETWORK_ISOLATION_FAILURE_EXIT})
"""


class NetworkIsolationUnavailableError(RuntimeError):
    """This host cannot run a process without network access."""


def network_isolated_command(interpreter: str, script: str, *args: str) -> list[str]:
    """``interpreter -I -c script args…`` with every network socket denied.

    Raises :class:`NetworkIsolationUnavailableError` on a platform with no
    isolation mechanism; callers check :func:`network_isolation_unavailable_reason`
    first so a tool is refused before it is ever started.
    """
    if sys.platform.startswith("linux"):
        return [interpreter, "-I", "-c", _LINUX_NETWORK_ISOLATION_PRELUDE + script, *args]
    if sys.platform == "darwin":
        return [
            _DARWIN_SANDBOX_EXEC,
            "-p",
            _DARWIN_NO_NETWORK_PROFILE,
            interpreter,
            "-I",
            "-c",
            script,
            *args,
        ]
    raise NetworkIsolationUnavailableError(
        f"no network isolation mechanism on platform {sys.platform}"
    )


@functools.cache
def network_isolation_unavailable_reason() -> str | None:
    """Why this host cannot run a tool without network, or None when it can.

    Probed once per process with the exact isolation every no-network tool
    run uses, then checked from inside: the answer reflects what the kernel,
    seccomp profile, AppArmor policy or Seatbelt of THIS host actually
    enforce — not what the platform nominally supports.
    """
    try:
        command = network_isolated_command(sys.executable, _NETWORK_ISOLATION_CHECK)
    except NetworkIsolationUnavailableError as exc:
        return str(exc)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
            command,
            capture_output=True,
            timeout=DEFAULT_TOOL_TIMEOUT_SECONDS,
            check=False,
            env=sandbox_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"isolation probe could not run: {exc}"
    if completed.returncode == 0:
        return None
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    return detail.removeprefix(NETWORK_ISOLATION_FAILURE_MARKER).strip() or (
        f"isolation probe exited with status {completed.returncode}"
    )


_BOOTSTRAP = """
import importlib.util
import json
import os
import socket
import sys
import types

_fd = os.environ.get("RAVN_HOST_CALL_FD")
if _fd:
    _channel = socket.socket(fileno=int(_fd)).makefile("rwb")

    class _HostTool:
        \"\"\"Call a tool of the resident that launched this subprocess.\"\"\"

        def __getattr__(self, name):
            def call(**kwargs):
                _channel.write(
                    (json.dumps({"tool": name, "arguments": kwargs}) + "\\n").encode("utf-8")
                )
                _channel.flush()
                line = _channel.readline()
                if not line:
                    raise RuntimeError("host call channel closed by the resident")
                reply = json.loads(line)
                if not reply.get("ok"):
                    raise RuntimeError(reply.get("error") or "host call failed")
                return reply.get("result")

            return call

    _sdk = types.ModuleType("ravn.sdk")
    _sdk.tool = _HostTool()
    _pkg = types.ModuleType("ravn")
    _pkg.sdk = _sdk
    _pkg.__path__ = []
    sys.modules["ravn"] = _pkg
    sys.modules["ravn.sdk"] = _sdk

spec = importlib.util.spec_from_file_location("valkyrie_tool", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
entry_point = getattr(module, sys.argv[2])
payload = json.load(sys.stdin)
json.dump(entry_point(payload), sys.stdout)
"""


class ToolVenvError(RuntimeError):
    """Raised when a learned tool's dedicated venv cannot be provisioned.

    A tool that declares requirements must never silently run without them —
    provisioning failures abort the run instead of degrading it.
    """


@dataclass(frozen=True)
class ToolRunResult:
    """Outcome of one sandboxed tool execution."""

    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    stderr: str = ""
    #: How declared reach was enforced for this run: ``"enforced"`` when the
    #: sandbox boundary applied it, ``"unavailable"`` when the backend could
    #: not express it (recorded honestly, never faked), ``""`` when the
    #: run makes no reach claim (a local subprocess granted the network).
    enforcement: str = ""
    #: True when ``ok=False`` because the RUNNER/BACKEND could not execute
    #: the tool at all (docker unavailable, venv/dependency provisioning
    #: failed, the backend refused the call) — never because the tool's own
    #: code failed. Callers must not count this toward regression/rollback
    #: bookkeeping: a backend outage is not evidence the tool is broken.
    infrastructure: bool = False


def write_tool(*, tools_dir: str | Path, skill_name: str, tool_code: str) -> Path:
    """Persist a built tool implementation under the resident tools directory."""
    if not tool_code.strip():
        raise ValueError(f"tool implementation for {skill_name!r} is empty")
    directory = Path(tools_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{skill_name}.py"
    path.write_text(tool_code, encoding="utf-8")
    return path


def tool_path_for_skill(tools_dir: str | Path, skill_name: str) -> Path:
    """Return the conventional implementation path for a skill name."""
    return Path(tools_dir) / f"{skill_name}.py"


def sandbox_env() -> dict[str, str]:
    """Minimal environment for a sandboxed tool run.

    A learned tool must never inherit the resident's ambient environment, where
    bearer tokens and credentials live. Pass only what a Python subprocess needs
    to start, resolve executables, and (for network-reach tools) speak TLS
    through corporate proxies/CA bundles.
    """
    env = {key: os.environ[key] for key in SANDBOX_ENV_PASSTHROUGH if key in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


def tool_venv_python(venv_dir: str | Path) -> Path:
    """Return the interpreter path inside a per-tool venv."""
    directory = Path(venv_dir)
    if os.name == "nt":
        return directory / "Scripts" / "python.exe"
    return directory / "bin" / "python"


def ensure_tool_venv(
    *,
    venvs_dir: str | Path,
    tool_name: str,
    requirements: list[str],
    pip_timeout_seconds: float = DEFAULT_TOOL_VENV_PIP_TIMEOUT_SECONDS,
    venv_timeout_seconds: float = DEFAULT_TOOL_VENV_CREATE_TIMEOUT_SECONDS,
) -> Path:
    """Provision the dedicated venv for a learned tool; return its python.

    Creates ``{venvs_dir}/{tool_name}`` once, pip-installs ``requirements``
    into it, and records the exact requirement list in a
    :data:`TOOL_VENV_REQUIREMENTS_STAMP` file. An unchanged requirement list
    is a no-op; a changed one rebuilds the venv from scratch so the
    environment always matches exactly what the tool declares.

    Raises :class:`ToolVenvError` when venv creation or pip install fails —
    the tool must never silently run without its dependencies.
    """
    venv_dir = Path(venvs_dir) / _venv_dirname(tool_name)
    python = tool_venv_python(venv_dir)
    stamp_path = venv_dir / TOOL_VENV_REQUIREMENTS_STAMP
    desired_stamp = "\n".join(requirements)

    # Serialize the check→rmtree→rebuild critical section per venv: two
    # concurrent runs of the same tool must not destroy each other's
    # half-provisioned environment.
    with _venv_provision_lock(venv_dir):
        if (
            python.is_file()
            and stamp_path.is_file()
            and stamp_path.read_text(encoding="utf-8") == desired_stamp
        ):
            return python

        if venv_dir.exists():
            # Requirements changed, or a previous provisioning attempt died before
            # writing its stamp: rebuild from scratch for an exact environment.
            shutil.rmtree(venv_dir)
        venv_dir.parent.mkdir(parents=True, exist_ok=True)
        _create_tool_venv(venv_dir, timeout_seconds=venv_timeout_seconds)
        if requirements:
            _pip_install_tool_requirements(
                venv_dir,
                python,
                requirements,
                timeout_seconds=pip_timeout_seconds,
            )
        # The stamp is written last so a killed provisioning run never masquerades
        # as a complete one.
        stamp_path.write_text(desired_stamp, encoding="utf-8")
        return python


def remove_tool_venv(*, venvs_dir: str | Path, tool_name: str) -> bool:
    """Delete a tool's dedicated venv; True when something was removed.

    Called when the tool leaves the resident (rollback, archive) so dependency
    environments never outlive the tools they served.
    """
    venv_dir = Path(venvs_dir) / _venv_dirname(tool_name)
    with _venv_provision_lock(venv_dir):
        if not venv_dir.exists():
            return False
        shutil.rmtree(venv_dir, ignore_errors=True)
        return True


def prune_orphaned_tool_venvs(*, venvs_dir: str | Path, tools_dir: str | Path) -> list[str]:
    """Remove venvs whose learned tool no longer exists; return pruned names.

    Bounded-growth guarantee for the state volume: a venv only lives as long
    as ``{tools_dir}/{name}.py`` does. The shared uv cache directory is never
    pruned — it is the deduplication substrate, not a per-tool artifact.
    """
    root = Path(venvs_dir)
    if not root.is_dir():
        return []
    code_dir = Path(tools_dir)
    pruned: list[str] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (code_dir / f"{child.name}.py").exists():
            continue
        if remove_tool_venv(venvs_dir=root, tool_name=child.name):
            pruned.append(child.name)
    return pruned


_VENV_PROVISION_LOCKS: dict[str, threading.Lock] = {}
_VENV_PROVISION_LOCKS_GUARD = threading.Lock()


def _venv_provision_lock(venv_dir: Path) -> threading.Lock:
    """One lock per venv directory, shared across threads in this process."""
    key = str(venv_dir)
    with _VENV_PROVISION_LOCKS_GUARD:
        return _VENV_PROVISION_LOCKS.setdefault(key, threading.Lock())


def _venv_dirname(tool_name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", tool_name.strip())
    if not name:
        raise ToolVenvError("cannot provision a venv for an empty tool name")
    return name


#: Shared uv cache beside the per-tool venvs. With uv, a venv is hardlinked
#: views into this one cache, so N tools sharing a package cost one copy on
#: disk and one download — the whole point of preferring uv over pip here.
TOOL_VENV_UV_CACHE_DIRNAME = ".uv-cache"


def _uv_executable() -> str | None:
    """The uv binary when available (both runtime images ship it), else None."""
    return shutil.which("uv")


def provision_env(cache_root: Path) -> dict[str, str]:
    """Scrubbed env for venv provisioning, with the shared uv cache configured.

    The ONE provisioning environment — execution (per-tool venvs) and
    verification (ephemeral venvs) both use it so uv caching and env hygiene
    cannot drift between the two.
    """
    env = sandbox_env()
    env["UV_CACHE_DIR"] = str(cache_root / TOOL_VENV_UV_CACHE_DIRNAME)
    # Hardlink from the cache (same filesystem) so venvs deduplicate packages.
    env["UV_LINK_MODE"] = "hardlink"
    return env


def venv_create_argv(venv_dir: Path) -> list[str]:
    """Command to create a venv: uv when available, stock venv otherwise."""
    uv = _uv_executable()
    if uv:
        return [uv, "venv", str(venv_dir)]
    return [sys.executable, "-m", "venv", str(venv_dir)]


def pip_install_argv(python: Path, requirements: list[str]) -> list[str]:
    """Command to install requirements into a venv: uv-first, pip fallback."""
    uv = _uv_executable()
    if uv:
        return [uv, "pip", "install", "--python", str(python), *requirements]
    return [str(python), "-m", "pip", "install", "--disable-pip-version-check", *requirements]


def _create_tool_venv(venv_dir: Path, *, timeout_seconds: float) -> None:
    try:
        completed = subprocess.run(  # noqa: S603
            venv_create_argv(venv_dir),
            check=False,
            capture_output=True,
            text=True,
            env=provision_env(venv_dir.parent),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolVenvError(
            f"venv creation timed out after {timeout_seconds}s: {venv_dir}"
        ) from exc
    if completed.returncode != 0:
        raise ToolVenvError(
            f"venv creation failed for {venv_dir}:\n{completed.stdout}\n{completed.stderr}".strip()
        )


def _pip_install_tool_requirements(
    venv_dir: Path,
    python: Path,
    requirements: list[str],
    *,
    timeout_seconds: float,
) -> None:
    try:
        completed = subprocess.run(  # noqa: S603
            pip_install_argv(python, requirements),
            check=False,
            capture_output=True,
            text=True,
            env=provision_env(venv_dir.parent),
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise ToolVenvError(
            f"pip install timed out after {timeout_seconds}s: {', '.join(requirements)}"
        ) from exc
    if completed.returncode != 0:
        # Never leave a half-provisioned venv behind: without its stamp it
        # would be rebuilt anyway, but a lingering directory invites debugging
        # against an environment the tool will never actually run in.
        shutil.rmtree(venv_dir, ignore_errors=True)
        raise ToolVenvError(
            "pip install failed for "
            f"{', '.join(requirements)}:\n{completed.stdout}\n{completed.stderr}".strip()
        )


#: Serves one sandboxed tool's host-call requests. Given a tool name and its
#: arguments, returns the result the tool sees; raising denies the call and the
#: tool receives the message.
HostCall = Callable[[str, dict[str, Any]], Awaitable[Any]]


async def _serve_host_calls(sock: socket.socket, host_call: HostCall) -> None:
    """Answer newline-delimited JSON host calls until the tool exits.

    One request at a time, matching the tool's blocking client: a learned tool
    is straight-line code, and letting it pipeline would buy nothing for the
    complexity of correlating replies.
    """
    reader, writer = await asyncio.open_unix_connection(sock=sock)
    try:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                request = json.loads(line)
                name = str(request.get("tool") or "")
                arguments = request.get("arguments") or {}
                if not isinstance(arguments, dict):
                    raise TypeError("host call arguments must be an object")
                reply = {"ok": True, "result": await host_call(name, arguments)}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            writer.write((json.dumps(reply, default=str) + "\n").encode("utf-8"))
            await writer.drain()
    except (asyncio.CancelledError, ConnectionError):
        return
    finally:
        writer.close()


async def run_tool(
    tool_path: str | Path,
    payload: dict[str, Any],
    *,
    entry_point: str = "run",
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_TOOL_OUTPUT_LIMIT_BYTES,
    python_executable: str | Path | None = None,
    host_call: HostCall | None = None,
    deny_network: bool = False,
) -> ToolRunResult:
    """Execute a tool implementation in an isolated subprocess.

    ``python_executable`` selects the interpreter (a per-tool venv's python
    for tools with dependencies); the default is the resident's own
    interpreter — exactly the historical behavior.

    ``host_call``, when given, exposes :data:`HOST_SDK_MODULE` inside the
    sandbox so the tool can ask the resident to run one of its own tools. The
    callable is the whole boundary: it decides what may be invoked and answers
    the tool's request. Without it the module is absent and any import of it
    fails loudly, which is the behavior every existing tool was written
    against — badly, by swallowing it.

    ``deny_network`` runs the tool with every network socket denied (see
    :func:`network_isolated_command`). If the host cannot apply that the
    tool never starts and the run is an infrastructure failure — it is
    never retried with the network on.
    """
    path = Path(tool_path)
    if not path.is_file():
        return ToolRunResult(
            ok=False,
            error=f"tool implementation missing: {path}",
            infrastructure=True,
        )
    interpreter = str(python_executable) if python_executable is not None else sys.executable
    command = [interpreter, "-I", "-c", _BOOTSTRAP, str(path), entry_point]
    if deny_network:
        try:
            command = network_isolated_command(interpreter, _BOOTSTRAP, str(path), entry_point)
        except NetworkIsolationUnavailableError as exc:
            return ToolRunResult(
                ok=False,
                error=f"{path.name} declares no network reach: {exc}",
                enforcement=_REACH_ENFORCEMENT_UNAVAILABLE,
                infrastructure=True,
            )

    env = sandbox_env()
    parent_sock: socket.socket | None = None
    child_sock: socket.socket | None = None
    pass_fds: tuple[int, ...] = ()
    if host_call is not None:
        parent_sock, child_sock = socket.socketpair()
        child_sock.set_inheritable(True)
        pass_fds = (child_sock.fileno(),)
        env[HOST_CALL_FD_ENV] = str(child_sock.fileno())

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            pass_fds=pass_fds,
        )
    finally:
        # The child owns its end once spawned; holding it here would keep the
        # channel open after the tool exits and hang the server task.
        if child_sock is not None:
            child_sock.close()

    server: asyncio.Task | None = None
    if parent_sock is not None and host_call is not None:
        server = asyncio.create_task(_serve_host_calls(parent_sock, host_call))
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode("utf-8")),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return ToolRunResult(
            ok=False,
            error=f"tool timed out after {timeout_seconds}s: {path.name}",
        )
    finally:
        if server is not None:
            server.cancel()
        if parent_sock is not None:
            parent_sock.close()

    stderr_text = stderr.decode("utf-8", errors="replace")[:output_limit_bytes]
    if deny_network and _isolation_failed(process.returncode, stderr_text):
        reason = stderr_text.removeprefix(NETWORK_ISOLATION_FAILURE_MARKER).strip()
        return ToolRunResult(
            ok=False,
            error=(
                f"{path.name} declares no network reach and its network namespace "
                f"could not be created: {reason}"
            ),
            enforcement=_REACH_ENFORCEMENT_UNAVAILABLE,
            infrastructure=True,
        )
    result = _tool_run_result(process.returncode, stdout, stderr_text, path, output_limit_bytes)
    if deny_network:
        return replace(result, enforcement=_REACH_ENFORCEMENT_ENFORCED)
    return result


def _isolation_failed(returncode: int | None, stderr_text: str) -> bool:
    """True when the isolation wrapper, not the tool, ended the run."""
    if returncode == 0:
        return False
    if returncode == NETWORK_ISOLATION_FAILURE_EXIT and stderr_text.startswith(
        NETWORK_ISOLATION_FAILURE_MARKER
    ):
        return True
    return sys.platform == "darwin" and stderr_text.startswith(_DARWIN_SANDBOX_FAILURE_PREFIX)


def _tool_run_result(
    returncode: int | None,
    stdout: bytes,
    stderr_text: str,
    path: Path,
    output_limit_bytes: int,
) -> ToolRunResult:
    if returncode != 0:
        return ToolRunResult(
            ok=False,
            error=f"tool exited with status {returncode}: {path.name}",
            stderr=stderr_text,
        )

    stdout_text = stdout.decode("utf-8", errors="replace")
    if len(stdout_text) > output_limit_bytes:
        return ToolRunResult(
            ok=False,
            error=f"tool output exceeded {output_limit_bytes} bytes: {path.name}",
            stderr=stderr_text,
        )
    try:
        result = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        return ToolRunResult(
            ok=False,
            error=f"tool produced non-JSON output: {exc}",
            stderr=stderr_text,
        )
    if not isinstance(result, dict):
        return ToolRunResult(
            ok=False,
            error=f"tool must return a JSON object, got {type(result).__name__}",
            stderr=stderr_text,
        )
    return ToolRunResult(ok=True, result=result, stderr=stderr_text)
