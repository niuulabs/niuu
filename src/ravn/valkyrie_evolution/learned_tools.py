"""Runtime adapter for resident-authored agent tools."""

from __future__ import annotations

import json
import re
import shlex
import uuid
from pathlib import Path
from typing import Any, Protocol

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.valkyrie_evolution.adapters import tool_implementation_findings
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest
from ravn.valkyrie_evolution.tool_runtime import (
    DEFAULT_TOOL_TIMEOUT_SECONDS,
    ToolRunResult,
    run_tool,
    write_tool,
)


class LearnedToolError(ValueError):
    """Raised when a learned tool artifact cannot be installed or loaded."""


class LearnedToolRunner(Protocol):
    """Execution backend for a resident-authored learned tool."""

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
    ) -> ToolRunResult:
        """Execute a learned tool and return a structured run result."""


class LocalLearnedToolRunner:
    """Run learned tools through the existing local isolated subprocess."""

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
    ) -> ToolRunResult:
        return await run_tool(
            tool_path,
            payload,
            entry_point=entry_point,
            timeout_seconds=timeout_seconds,
        )


class ForgeSandboxLearnedToolRunner:
    """Run learned tools inside the Forge/devrunner Docker sandbox.

    This is the Phase 2 execution path: generated code is still the same Python
    module, but it runs in an ephemeral workspace-mounted devrunner container
    instead of the resident process namespace.
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

    async def run(
        self,
        tool_path: Path,
        payload: dict[str, Any],
        *,
        entry_point: str,
        timeout_seconds: float,
    ) -> ToolRunResult:
        if not tool_path.resolve().is_relative_to(self._workspace_root):
            return ToolRunResult(
                ok=False,
                error=(
                    f"forge sandbox runner requires learned tool path inside workspace: {tool_path}"
                ),
            )

        run_dir = self._runs_dir / uuid.uuid4().hex
        run_dir.mkdir(parents=True, exist_ok=True)
        payload_path = run_dir / "payload.json"
        runner_path = run_dir / "runner.py"
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        runner_path.write_text(_FORGE_RUNNER_SCRIPT, encoding="utf-8")

        shell = await self._shell_instance(timeout_seconds)
        command = _forge_runner_command(
            runner_path=runner_path,
            tool_path=tool_path,
            entry_point=entry_point,
            payload_path=payload_path,
        )
        try:
            output, exit_code = await shell.run(command)
        except Exception as exc:  # noqa: BLE001
            return ToolRunResult(ok=False, error=f"forge sandbox execution failed: {exc}")

        if exit_code != 0:
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool exited with status {exit_code}: {tool_path.name}",
                stderr=str(output),
            )
        try:
            result = json.loads(str(output))
        except json.JSONDecodeError as exc:
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool produced non-JSON output: {exc}",
                stderr=str(output),
            )
        if not isinstance(result, dict):
            return ToolRunResult(
                ok=False,
                error=f"forge sandbox tool must return a JSON object, got {type(result).__name__}",
                stderr=str(output),
            )
        return ToolRunResult(ok=True, result=result)

    async def _shell_instance(self, timeout_seconds: float) -> Any:
        if self._shell is not None:
            return self._shell
        from ravn.adapters.tools.terminal_docker import DockerPersistentShell  # noqa: PLC0415
        from ravn.config import DockerTerminalConfig  # noqa: PLC0415

        config = self._docker_config or DockerTerminalConfig(
            image="ghcr.io/niuulabs/devrunner:latest"
        )
        self._shell = DockerPersistentShell(
            config=config,
            workspace_root=self._workspace_root,
            timeout_seconds=timeout_seconds,
        )
        await self._shell.start()
        return self._shell


class LearnedTool(ToolPort):
    """Expose a resident-authored artifact through the normal agent tool port."""

    def __init__(
        self,
        *,
        manifest: LearnedToolManifest,
        tool_path: str | Path,
        timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        runner: LearnedToolRunner | None = None,
    ) -> None:
        _validate_manifest(manifest)
        self._manifest = manifest
        self._tool_path = Path(tool_path)
        self._timeout_seconds = timeout_seconds
        self._runner = runner or LocalLearnedToolRunner()

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
    """Persist the full manifest + code envelope for review or flock exchange."""
    _validate_artifact(artifact)
    directory = Path(artifacts_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_filename_for_tool(artifact.manifest.name)}.json"
    path.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


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
) -> LearnedTool:
    """Create an agent-callable ToolPort from a learned artifact."""
    return LearnedTool(
        manifest=artifact.manifest,
        tool_path=tool_path,
        timeout_seconds=timeout_seconds,
        runner=runner,
    )


def learned_tool_path(tools_dir: str | Path, tool_name: str) -> Path:
    """Return the conventional code path for a learned tool name."""
    return Path(tools_dir) / f"{_filename_for_tool(tool_name)}.py"


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
        safety_class="declared_reach",
    )
    if findings:
        raise LearnedToolError("; ".join(findings))


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


def learned_tool_storage(state_dir: str | Path) -> tuple[Path, Path]:
    """Return the one canonical (code_dir, artifacts_dir) for learned tools.

    Every writer (build_tool authoring, peer-adoption install) and the daemon
    loader resolve the location here so a learned tool lives in exactly one
    place on disk.
    """
    base = Path(state_dir)
    return base / "learned_tools", base / "learned_tool_artifacts"


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
) -> str:
    parts = [
        "python",
        "-I",
        str(runner_path),
        str(tool_path),
        entry_point,
        str(payload_path),
    ]
    return " ".join(shlex.quote(part) for part in parts)
