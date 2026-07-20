"""Sandboxed execution for resident-built tool implementations.

A Valkyrie-built tool is a single Python module exposing an entry-point
function (default ``run``) that takes the signal payload dict and returns a
JSON-serializable judgment dict.  Tools execute out-of-process with an
isolated interpreter (``python -I``) and a hard timeout, so a broken or slow
self-built probe cannot take the resident down with it.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
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

_BOOTSTRAP = """
import importlib.util
import json
import sys

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
    #: backend makes no reach claim at all (plain local subprocess).
    enforcement: str = ""


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


async def run_tool(
    tool_path: str | Path,
    payload: dict[str, Any],
    *,
    entry_point: str = "run",
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_TOOL_OUTPUT_LIMIT_BYTES,
    python_executable: str | Path | None = None,
) -> ToolRunResult:
    """Execute a tool implementation in an isolated subprocess.

    ``python_executable`` selects the interpreter (a per-tool venv's python
    for tools with dependencies); the default is the resident's own
    interpreter — exactly the historical behavior.
    """
    path = Path(tool_path)
    if not path.is_file():
        return ToolRunResult(ok=False, error=f"tool implementation missing: {path}")

    interpreter = str(python_executable) if python_executable is not None else sys.executable
    process = await asyncio.create_subprocess_exec(
        interpreter,
        "-I",
        "-c",
        _BOOTSTRAP,
        str(path),
        entry_point,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=sandbox_env(),
    )
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

    stderr_text = stderr.decode("utf-8", errors="replace")[:output_limit_bytes]
    if process.returncode != 0:
        return ToolRunResult(
            ok=False,
            error=f"tool exited with status {process.returncode}: {path.name}",
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
