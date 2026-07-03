"""Runtime adapter for resident-authored agent tools."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
import shlex
import uuid
from collections.abc import Sequence
from dataclasses import replace
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


class ForgeSandboxLearnedToolRunner:
    """Run learned tools inside the Forge/devrunner Docker sandbox.

    This is the Phase 2 execution path: generated code is still the same Python
    module, but it runs in an ephemeral workspace-mounted devrunner container
    instead of the resident process namespace.

    Phase 5b: declared reach is enforced at this boundary. A tool whose
    manifest grants network reach runs in a container with
    :data:`NETWORK_ALLOWED_DOCKER_NETWORK`; everything else runs with
    :data:`NETWORK_DENIED_DOCKER_NETWORK`. When a caller injects a shell whose
    network mode cannot be determined (or cannot be switched off), the run
    result records ``enforcement="unavailable"`` instead of faking isolation.
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
    ) -> ToolRunResult:
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
    ) -> None:
        _validate_manifest(manifest)
        self._manifest = manifest
        self._tool_path = Path(tool_path)
        self._timeout_seconds = timeout_seconds
        self._runner = runner or LocalLearnedToolRunner()
        self._requirements = list(requirements)

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
        return artifact
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
    """
    venv = shlex.quote(str(venv_dir))
    python = shlex.quote(str(venv_dir / "bin" / "python"))
    install = " ".join(shlex.quote(req) for req in requirements)
    return (
        f"rm -rf {venv} && python -m venv {venv} && "
        f"{python} -m pip install --disable-pip-version-check {install}"
    )
