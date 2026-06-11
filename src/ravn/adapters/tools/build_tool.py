"""Meta-tool for authoring resident learned tools during an agent session."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort
from ravn.valkyrie_evolution.learned_tools import (
    ForgeSandboxLearnedToolRunner,
    LearnedToolError,
    load_learned_tool,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest
from ravn.valkyrie_evolution.resident_learning import flock_learning_proposed_event

#: Confidence a freshly self-registered learned tool travels to the flock
#: with — matching what the resident install pipeline assigns its own builds.
SELF_REGISTERED_TOOL_CONFIDENCE = 0.74

ToolRegistrar = Callable[[ToolPort], None]


class BuildTool(ToolPort):
    """Create a learned agent tool and register it into the active toolbox."""

    def __init__(
        self,
        *,
        tools_dir: str | Path,
        artifacts_dir: str | Path | None = None,
        register_tool: ToolRegistrar | None = None,
        timeout_seconds: float = 10.0,
        publisher: Any | None = None,
        environment_id: str = "",
        valkyrie_id: str = "",
        flock_id: str = "",
        domain: str = "",
        execution_backend: str = "local",
        workspace_root: str | Path | None = None,
        sandbox_shell: Any | None = None,
    ) -> None:
        self._tools_dir = Path(tools_dir)
        self._artifacts_dir = (
            Path(artifacts_dir) if artifacts_dir else self._tools_dir / "artifacts"
        )
        self._register_tool = register_tool
        self._timeout_seconds = timeout_seconds
        self._publisher = publisher
        self._environment_id = environment_id
        self._valkyrie_id = valkyrie_id
        self._flock_id = flock_id
        self._domain = domain
        self._execution_backend = execution_backend
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._sandbox_shell = sandbox_shell

    @property
    def name(self) -> str:
        return "build_tool"

    @property
    def description(self) -> str:
        return (
            "Author and install a reusable agent tool during the current investigation. "
            "Provide a manifest with name, description, input_schema, required_permission, "
            "declared_reach, and Python tool_code exposing the manifest entry_point "
            "(default run(input)). The tool is canaried, persisted, and registered so it "
            "can be called by name on the next model iteration."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "manifest": {
                    "type": "object",
                    "description": (
                        "Learned tool manifest. Required keys: name, description, "
                        "input_schema, required_permission. Optional: declared_reach, "
                        "output_schema, entry_point."
                    ),
                },
                "tool_code": {
                    "type": "string",
                    "description": (
                        "Python implementation. It must define the manifest entry_point "
                        "and return a JSON object."
                    ),
                },
                "artifact_id": {
                    "type": "string",
                    "description": "Optional stable artifact id for provenance.",
                },
                "canary_input": {
                    "type": "object",
                    "description": "Optional sample input used before registration.",
                },
                "provenance": {
                    "type": "object",
                    "description": "Optional builder/session/signal metadata.",
                },
                "replace": {
                    "type": "boolean",
                    "description": "Replace an existing tool of the same name.",
                },
            },
            "required": ["manifest", "tool_code"],
        }

    @property
    def required_permission(self) -> str:
        return "tool:build"

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        try:
            artifact = _artifact_from_input(input)
            tool_path = write_learned_tool(tools_dir=self._tools_dir, artifact=artifact)
            artifact_path = write_learned_tool_artifact(
                artifacts_dir=self._artifacts_dir,
                artifact=artifact,
            )
            learned_tool = load_learned_tool(
                artifact=artifact,
                tool_path=tool_path,
                timeout_seconds=self._timeout_seconds,
                runner=self._runner_for_backend(),
            )

            canary_input = input.get("canary_input")
            if isinstance(canary_input, dict):
                canary = await learned_tool.execute(canary_input)
                if canary.is_error:
                    return ToolResult(
                        tool_call_id="",
                        content=(
                            "Canary failed; learned tool was persisted but not registered.\n"
                            f"{canary.content}"
                        ),
                        is_error=True,
                    )

            if self._register_tool is None:
                return ToolResult(
                    tool_call_id="",
                    content=(
                        "Learned tool was built, but no active-session registrar is available.\n"
                        + _summary(artifact, tool_path, artifact_path, registered=False)
                    ),
                    is_error=True,
                )

            replace = bool(input.get("replace"))
            self._register_tool(learned_tool, replace=replace)  # type: ignore[call-arg]
            await self._publish_flock_proposal(artifact)
        except (LearnedToolError, TypeError, ValueError) as exc:
            return ToolResult(tool_call_id="", content=f"build_tool failed: {exc}", is_error=True)

        return ToolResult(
            tool_call_id="",
            content=_summary(artifact, tool_path, artifact_path, registered=True),
        )

    async def _publish_flock_proposal(self, artifact: LearnedToolArtifact) -> None:
        if self._publisher is None or not self._flock_id:
            return
        await self._publisher.publish(
            flock_learning_proposed_event(
                source=self._valkyrie_id or "build_tool",
                learning_id=artifact.artifact_id,
                title=artifact.manifest.name,
                summary=artifact.manifest.description,
                flock_id=self._flock_id,
                artifact_type=artifact.artifact_type,
                content="",
                domain=self._domain,
                environment_id=self._environment_id,
                source_valkyrie_id=self._valkyrie_id,
                confidence=SELF_REGISTERED_TOOL_CONFIDENCE,
                redaction_status="redacted",
                promotion_id=artifact.artifact_id,
                tool_code=artifact.tool_code,
                tool_entry_point=artifact.manifest.entry_point,
                learned_tool_manifest=artifact.manifest.to_dict(),
                review_outcome="self_registered",
                builder_evidence=artifact.provenance,
                correlation_id=artifact.artifact_id,
            )
        )

    def _runner_for_backend(self) -> Any | None:
        if self._execution_backend in {"", "local"}:
            return None
        if self._execution_backend in {"forge", "devrunner"}:
            workspace_root = self._workspace_root or self._tools_dir.parent
            return ForgeSandboxLearnedToolRunner(
                workspace_root=workspace_root,
                shell=self._sandbox_shell,
            )
        raise ValueError(f"unknown learned tool execution backend: {self._execution_backend}")


def attach_build_tool(
    agent: Any,
    *,
    tools_dir: str | Path,
    artifacts_dir: str | Path | None = None,
    timeout_seconds: float = 10.0,
    replace: bool = True,
    publisher: Any | None = None,
    environment_id: str = "",
    valkyrie_id: str = "",
    flock_id: str = "",
    domain: str = "",
    execution_backend: str = "local",
    workspace_root: str | Path | None = None,
    sandbox_shell: Any | None = None,
) -> BuildTool:
    """Attach build_tool to an agent supporting register_tool()."""
    registrar = getattr(agent, "register_tool", None)
    if registrar is None:
        raise TypeError("agent does not support active tool registration")
    tool = BuildTool(
        tools_dir=tools_dir,
        artifacts_dir=artifacts_dir,
        register_tool=registrar,
        timeout_seconds=timeout_seconds,
        publisher=publisher,
        environment_id=environment_id,
        valkyrie_id=valkyrie_id,
        flock_id=flock_id,
        domain=domain,
        execution_backend=execution_backend,
        workspace_root=workspace_root,
        sandbox_shell=sandbox_shell,
    )
    registrar(tool, replace=replace)
    return tool


def _artifact_from_input(input: dict) -> LearnedToolArtifact:  # noqa: A002
    manifest_raw = input.get("manifest")
    if not isinstance(manifest_raw, dict):
        raise ValueError("manifest must be an object")
    manifest = LearnedToolManifest.from_dict(manifest_raw)
    tool_code = str(input.get("tool_code") or "")
    provenance = input.get("provenance")
    if provenance is not None and not isinstance(provenance, dict):
        raise ValueError("provenance must be an object when provided")
    artifact_id = str(input.get("artifact_id") or "").strip()
    if not artifact_id:
        artifact_id = f"learned-tool:{manifest.name}:{uuid4().hex[:12]}"
    return LearnedToolArtifact(
        artifact_id=artifact_id,
        manifest=manifest,
        tool_code=tool_code,
        provenance=dict(provenance or {}),
    )


def _summary(
    artifact: LearnedToolArtifact,
    tool_path: Path,
    artifact_path: Path,
    *,
    registered: bool,
) -> str:
    payload = {
        "artifact_id": artifact.artifact_id,
        "tool_name": artifact.manifest.name,
        "registered": registered,
        "required_permission": artifact.manifest.required_permission,
        "declared_reach": [grant.to_dict() for grant in artifact.manifest.declared_reach],
        "tool_path": str(tool_path),
        "artifact_path": str(artifact_path),
    }
    return json.dumps(payload, indent=2, sort_keys=True)
