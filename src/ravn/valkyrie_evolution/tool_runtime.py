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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TOOL_TIMEOUT_SECONDS = 10.0
DEFAULT_TOOL_OUTPUT_LIMIT_BYTES = 256 * 1024

#: Environment variables a sandboxed tool subprocess is allowed to inherit.
#: Everything else — bearer tokens, PATs, cloud credentials, all of which live
#: in the resident's ambient environment — is withheld. A learned tool that
#: legitimately needs a credential receives it through reach-scoped injection
#: (Phase 5), never by inheriting the whole process environment.
_SANDBOX_ENV_PASSTHROUGH = ("PATH", "SYSTEMROOT", "LANG", "LC_ALL", "LC_CTYPE", "TZ")

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


@dataclass(frozen=True)
class ToolRunResult:
    """Outcome of one sandboxed tool execution."""

    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    stderr: str = ""


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


def _sandbox_env() -> dict[str, str]:
    """Minimal environment for a sandboxed tool run.

    A learned tool must never inherit the resident's ambient environment, where
    bearer tokens and credentials live. Pass only what a Python subprocess needs
    to start and resolve executables.
    """
    env = {key: os.environ[key] for key in _SANDBOX_ENV_PASSTHROUGH if key in os.environ}
    env.setdefault("PATH", os.defpath)
    return env


async def run_tool(
    tool_path: str | Path,
    payload: dict[str, Any],
    *,
    entry_point: str = "run",
    timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
    output_limit_bytes: int = DEFAULT_TOOL_OUTPUT_LIMIT_BYTES,
) -> ToolRunResult:
    """Execute a tool implementation in an isolated subprocess."""
    path = Path(tool_path)
    if not path.is_file():
        return ToolRunResult(ok=False, error=f"tool implementation missing: {path}")

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        _BOOTSTRAP,
        str(path),
        entry_point,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=_sandbox_env(),
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
