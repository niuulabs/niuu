"""Meta-tool for authoring resident learned tools during an agent session."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from ravn.domain.models import ToolResult
from ravn.odin.review import ReviewItem, ReviewKind, ReviewRequester
from ravn.ports.tool import ToolPort
from ravn.valkyrie_evolution.adapters import PolicyCourtReviewer
from ravn.valkyrie_evolution.learned_tools import (
    ForgeSandboxLearnedToolRunner,
    LearnedToolError,
    load_learned_tool,
    manifest_safety_class,
    write_learned_tool,
    write_learned_tool_artifact,
)
from ravn.valkyrie_evolution.models import LearnedToolArtifact, LearnedToolManifest, ReviewResult
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningArtifact,
    ResidentLearningIdentity,
    flock_learning_proposed_event,
    review_allows_install,
    review_inputs,
)

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
        review_requester: ReviewRequester | None = None,
        autonomy_mode: str = "autonomous",
        environment_id: str = "",
        valkyrie_id: str = "",
        flock_id: str = "",
        domain: str = "",
        execution_backend: str = "local",
        workspace_root: str | Path | None = None,
        sandbox_shell: Any | None = None,
        reviewer: Any | None = None,
    ) -> None:
        self._tools_dir = Path(tools_dir)
        self._artifacts_dir = (
            Path(artifacts_dir) if artifacts_dir else self._tools_dir / "artifacts"
        )
        self._register_tool = register_tool
        self._timeout_seconds = timeout_seconds
        self._publisher = publisher
        self._review_requester = review_requester
        self._autonomy_mode = autonomy_mode
        self._environment_id = environment_id
        self._valkyrie_id = valkyrie_id
        self._flock_id = flock_id
        self._domain = domain
        self._execution_backend = execution_backend
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._sandbox_shell = sandbox_shell
        self._reviewer = reviewer or PolicyCourtReviewer(reviewer="odin:build-tool")

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
            artifact_path = write_learned_tool_artifact(
                artifacts_dir=self._artifacts_dir,
                artifact=artifact,
            )
            canary_input = input.get("canary_input")
            canary_sample = canary_input if isinstance(canary_input, dict) else {}

            # The one gate: the same PolicyCourtReviewer / AutonomyPolicy that
            # decides every other resident self-improvement, keyed on autonomy
            # mode + the tool's declared reach (mutating access + hard
            # boundaries). No build_tool-local allow/deny list.
            resident_artifact = _resident_learning_artifact(
                artifact,
                artifact_path=artifact_path,
                canary_input=canary_sample,
                environment_id=self._environment_id,
                valkyrie_id=self._valkyrie_id,
                flock_id=self._flock_id,
                domain=self._domain,
            )
            review = await self._review(resident_artifact)
            if review.blocking_findings:
                return ToolResult(
                    tool_call_id="",
                    content="build_tool rejected by review: " + "; ".join(review.blocking_findings),
                    is_error=True,
                )
            if not review_allows_install(review, self._autonomy_mode):
                review_filed = await self._file_install_review(resident_artifact, artifact, review)
                return ToolResult(
                    tool_call_id="",
                    content=_review_summary(artifact, artifact_path, review_filed=review_filed),
                    is_error=not review_filed,
                )

            tool_path = write_learned_tool(tools_dir=self._tools_dir, artifact=artifact)
            learned_tool = load_learned_tool(
                artifact=artifact,
                tool_path=tool_path,
                timeout_seconds=self._timeout_seconds,
                runner=self._runner_for_backend(),
            )

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

    async def _review(self, resident_artifact: ResidentLearningArtifact) -> ReviewResult:
        identity = ResidentLearningIdentity(
            environment_id=self._environment_id,
            valkyrie_id=self._valkyrie_id,
            domain=self._domain,
            flock_ids=[self._flock_id] if self._flock_id else [],
            autonomy_mode=self._autonomy_mode,
        )
        request, build = review_inputs(resident_artifact, identity)
        return await self._reviewer.review(
            request=request,
            build=build,
            autonomy_mode=self._autonomy_mode,
        )

    async def _file_install_review(
        self,
        resident_artifact: ResidentLearningArtifact,
        artifact: LearnedToolArtifact,
        review: ReviewResult,
    ) -> bool:
        if self._review_requester is None:
            return False
        item = ReviewItem.new(
            kind=ReviewKind.EVOLUTION_BUILD.value,
            requested_action="install",
            environment_id=self._environment_id,
            valkyrie_id=self._valkyrie_id,
            title=artifact.manifest.name,
            summary=artifact.manifest.description,
            flock_id=self._flock_id,
            domain=self._domain,
            risk_class=_risk_class_for_safety(resident_artifact),
            safety_class=manifest_safety_class(artifact.manifest),
            urgency=0.6,
            dedupe_key=f"build_tool:{self._environment_id}:{artifact.manifest.name}",
            evidence={
                "artifact": asdict(resident_artifact),
                "review": {
                    "outcome": review.outcome,
                    "rationale": review.rationale,
                    "findings": list(review.findings),
                },
                "build_evidence": dict(artifact.provenance),
                "learned_tool_manifest": artifact.manifest.to_dict(),
            },
            requested_by=self._valkyrie_id,
            correlation_id=artifact.artifact_id,
        )
        return await self._review_requester.request(item) is not None

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
    review_requester: ReviewRequester | None = None,
    autonomy_mode: str = "autonomous",
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
        review_requester=review_requester,
        autonomy_mode=autonomy_mode,
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


def _resident_learning_artifact(
    artifact: LearnedToolArtifact,
    *,
    artifact_path: Path,
    canary_input: dict[str, Any],
    environment_id: str,
    valkyrie_id: str,
    flock_id: str,
    domain: str,
) -> ResidentLearningArtifact:
    """Project a learned tool into the shared resident-artifact envelope.

    The same object feeds the review gate (via review_inputs) and the review
    item evidence (via asdict), so the resident rehydrates exactly what was
    reviewed.
    """
    return ResidentLearningArtifact(
        learning_id=artifact.artifact_id,
        title=artifact.manifest.name,
        summary=artifact.manifest.description,
        content="",
        artifact_type=artifact.artifact_type,
        scope="environment",
        confidence=SELF_REGISTERED_TOOL_CONFIDENCE,
        source_environment_id=environment_id,
        source_valkyrie_id=valkyrie_id,
        promotion_id=artifact.artifact_id,
        flock_id=flock_id,
        domain=domain,
        redaction_status="redacted",
        artifact_path=str(artifact_path),
        tool_code=artifact.tool_code,
        tool_entry_point=artifact.manifest.entry_point,
        learned_tool_manifest=artifact.manifest.to_dict(),
        canary_sample=dict(canary_input),
        correlation_id=artifact.artifact_id,
    )


def _risk_class_for_safety(resident_artifact: ResidentLearningArtifact) -> str:
    manifest = LearnedToolManifest.from_dict(resident_artifact.learned_tool_manifest)
    return "high" if manifest_safety_class(manifest) == "mutating" else "low"


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


def _review_summary(
    artifact: LearnedToolArtifact,
    artifact_path: Path,
    *,
    review_filed: bool,
) -> str:
    payload = {
        "artifact_id": artifact.artifact_id,
        "tool_name": artifact.manifest.name,
        "registered": False,
        "review_required": True,
        "review_filed": review_filed,
        "required_permission": artifact.manifest.required_permission,
        "declared_reach": [grant.to_dict() for grant in artifact.manifest.declared_reach],
        "artifact_path": str(artifact_path),
    }
    if not review_filed:
        payload["reason"] = "operator review is required but no review requester is configured"
    return json.dumps(payload, indent=2, sort_keys=True)
