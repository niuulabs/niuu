"""Meta-tool for authoring resident learned tools during an agent session."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from niuu.observability import get_observability
from ravn.domain.models import ToolResult
from ravn.odin.review import ReviewItem, ReviewKind, ReviewRequester
from ravn.ports.tool import ToolPort
from ravn.ports.tool_build_backend import (
    ToolBuildError,
    ToolBuildInputRequiredError,
    ToolBuildPendingError,
)
from ravn.tool_observability import publish_learned_tool_inventory
from ravn.valkyrie_evolution.adapters import PolicyCourtReviewer
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    learned_tool_artifact_path,
    learned_tool_path,
    learned_tool_runner_for_backend,
    learned_tool_venvs_dir,
    load_learned_tool,
    manifest_safety_class,
    read_learned_tool_artifact,
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
    risk_class_for_safety,
)
from ravn.valkyrie_evolution.tool_verification import (
    VerificationResult,
    verify_learned_tool_in_ephemeral_venv,
)

#: Confidence a freshly self-registered learned tool travels to the flock
#: with — matching what the resident install pipeline assigns its own builds.
#: Default for the ``flock_confidence`` kwarg; deployments override it via
#: ``ResidentEvolutionConfig.self_registered_tool_confidence``.
SELF_REGISTERED_TOOL_CONFIDENCE = 0.74

#: How many verify+repair rounds a commissioned/authored build gets before we
#: give up and fail loudly. Default for the ``max_repair_attempts`` kwarg;
#: deployments override it via ``ResidentEvolutionConfig.build_repair_attempts``.
DEFAULT_MAX_REPAIR_ATTEMPTS = 3

ToolRegistrar = Callable[[ToolPort], None]
InstalledArtifactRecorder = Callable[[ResidentLearningArtifact], Awaitable[None]]
logger = logging.getLogger(__name__)


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
        execution_backend_kwargs: Mapping[str, Any] | None = None,
        workspace_root: str | Path | None = None,
        sandbox_shell: Any | None = None,
        reviewer: Any | None = None,
        build_backend: Any | None = None,
        investigation_context: Callable[[], str] | None = None,
        max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
        flock_confidence: float = SELF_REGISTERED_TOOL_CONFIDENCE,
        installed_artifact_recorder: InstalledArtifactRecorder | None = None,
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
        self._execution_backend_kwargs = dict(execution_backend_kwargs or {})
        self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._sandbox_shell = sandbox_shell
        self._reviewer = reviewer or PolicyCourtReviewer(reviewer="odin:build-tool")
        self._build_backend = build_backend
        self._investigation_context = investigation_context
        self._max_repair_attempts = max_repair_attempts
        self._flock_confidence = flock_confidence
        self._installed_artifact_recorder = installed_artifact_recorder

    def _investigation_prompt(self) -> str:
        """The investigation prompt that drove this build, for review provenance."""
        if self._investigation_context is None:
            return ""
        try:
            return str(self._investigation_context() or "")
        except Exception:  # noqa: BLE001 — provenance must never break a build
            return ""

    @property
    def name(self) -> str:
        return "build_tool"

    @property
    def description(self) -> str:
        authoring = (
            "A build backend is configured: provide build_request and omit tool_code "
            "so the backend develops the implementation and tests. "
            if self._build_backend is not None
            else "No build backend is configured: provide tool_code and test_code inline. "
        )
        return (
            "Author and install a reusable agent tool during the current investigation. "
            "Provide a manifest with name, description, input_schema, required_permission, "
            "and declared_reach. "
            + authoring
            + "If a commissioned build returns status=pending, stop and wait for its A2A "
            "callback; on the callback turn, resume it with continuation_task_id only. "
            "If it returns status=input_required, either answer from "
            "available evidence or ask the operator. Resume the same durable A2A task with "
            "continuation_task_id and continuation_answer; include continuation_metadata "
            "when the result requests a gate decision. "
            + "The entry point defaults to run(input). Good instruments "
            "ACQUIRE live evidence: tool_code may run CLI commands (subprocess), call HTTP "
            "APIs, and read files — declare that reach honestly in declared_reach, it is "
            "what review and invocation policy gate on. Do not hardcode environment values "
            "or thresholds; take them as input_schema parameters. The tool is canaried "
            "(one sandboxed run on canary_input — return a clear error object instead of "
            "raising when access is missing), persisted, and registered so it can be "
            "called by name on the next iteration."
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
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "input_schema": {"type": "object"},
                        "required_permission": {"type": "string"},
                        "declared_reach": {
                            "type": "array",
                            "description": (
                                "External reach grants. Use [] for pure local computation."
                            ),
                            "items": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "properties": {
                                            "kind": {"type": "string"},
                                            "target": {"type": "string"},
                                            "access": {"type": "string"},
                                            "metadata": {"type": "object"},
                                        },
                                        "required": ["kind"],
                                    },
                                    {"type": "string", "minLength": 1},
                                ]
                            },
                        },
                        "output_schema": {"type": "object"},
                        "entry_point": {"type": "string"},
                    },
                    "required": [
                        "name",
                        "description",
                        "input_schema",
                        "required_permission",
                    ],
                },
                "tool_code": {
                    "type": "string",
                    "description": (
                        "Python implementation (inline). Define the manifest entry_point "
                        "and return a JSON object. Runs in a sandboxed subprocess: "
                        "subprocess/CLI calls, HTTP, and file reads are available within "
                        "the declared_reach. Prefer fetching live state over re-analysing "
                        "the input payload; return a clear error object when access is "
                        "unavailable. Omit to commission a build_request."
                    ),
                },
                "build_request": {
                    "type": "string",
                    "description": (
                        "Natural-language spec of the tool to build. When a build backend "
                        "is configured, the tool is developed in a Forge session / Ting "
                        "workflow instead of written inline."
                    ),
                },
                "test_code": {
                    "type": "string",
                    "description": (
                        "Optional self-contained test module (pytest/asserts) that loads "
                        "the tool and exercises the entry point. Commissioned builds "
                        "populate this from the produced artifact."
                    ),
                },
                "requirements": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional pip package requirement strings the tool needs at "
                        "runtime ([] for stdlib-only). Commissioned builds populate this "
                        "from the produced artifact."
                    ),
                },
                "signal_context": {
                    "type": "string",
                    "description": "Optional investigation context passed to the build backend.",
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
                "continuation_task_id": {
                    "type": "string",
                    "description": (
                        "A durable commissioned-build task id returned by an earlier "
                        "status=pending or status=input_required result. Use it to resume "
                        "that exact task after its callback."
                    ),
                },
                "continuation_answer": {
                    "type": "string",
                    "description": (
                        "The model's or operator's answer to the pending remote question or gate."
                    ),
                },
                "continuation_metadata": {
                    "type": "object",
                    "description": (
                        "Optional A2A reply metadata requested by the input_required "
                        "result, such as gateDecision=approve or request_changes."
                    ),
                },
            },
            "anyOf": [
                {"required": ["manifest"]},
                {"required": ["continuation_task_id"]},
            ],
        }

    @property
    def required_permission(self) -> str:
        return "tool:build"

    @property
    def parallelisable(self) -> bool:
        return False

    async def recover_pending(self) -> list[ToolResult]:
        """Continue commissioned builds interrupted before their result was durable."""
        results: list[ToolResult] = []
        for record in self._commission_records():
            if str(record.get("environment_id") or "") != self._environment_id:
                continue
            if str(record.get("valkyrie_id") or "") != self._valkyrie_id:
                continue
            original = record.get("input")
            if not isinstance(original, dict):
                continue
            operation_id = str(record.get("operation_id") or "")
            telemetry = get_observability()
            with telemetry.span(
                "ravn.tool_build.commission.recover",
                attributes={"ravn.tool_build.operation.id": operation_id},
            ):
                installed = self._installed_artifact_for_commission(record)
                if installed is not None:
                    self._delete_commission(operation_id)
                    telemetry.event(
                        "ravn.tool_build.commission.reconciled",
                        attributes={
                            "ravn.tool_build.operation.id": operation_id,
                            "ravn.tool_build.name": installed.manifest.name,
                            "ravn.tool_build.artifact.id": installed.artifact_id,
                        },
                    )
                    results.append(
                        ToolResult(
                            tool_call_id="",
                            content=(
                                "Skipped duplicate commissioned build recovery: "
                                f"verified tool {installed.manifest.name!r} is already installed "
                                f"as {installed.artifact_id}."
                            ),
                        )
                    )
                    continue
                telemetry.event(
                    "ravn.tool_build.commission.recovery_started",
                    attributes={
                        "ravn.tool_build.operation.id": operation_id,
                        "ravn.tool_build.name": str(record.get("tool_name") or ""),
                        "ravn.tool_build.commission.state": str(record.get("state") or ""),
                    },
                )
                result = await self._execute_pipeline(dict(original))
                results.append(result)
                telemetry.event(
                    "ravn.tool_build.commission.recovery_finished",
                    attributes={
                        "ravn.tool_build.operation.id": operation_id,
                        "ravn.tool_build.recovery.outcome": (
                            "error" if result.is_error else "completed"
                        ),
                    },
                    content=result.content,
                )
        return results

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        telemetry = get_observability()
        commissioned = bool(str(input.get("continuation_task_id") or "").strip()) or (
            bool(str(input.get("build_request") or "").strip())
            and not bool(str(input.get("tool_code") or "").strip())
        )
        attributes = {
            "ravn.tool_build.mode": "commissioned" if commissioned else "inline",
            "ravn.tool_build.backend": (
                str(getattr(self._build_backend, "name", "unconfigured"))
                if commissioned
                else "inline"
            ),
            "ravn.tool_build.backend.configured": self._build_backend is not None,
            "ravn.tool_build.request.has_build_request": bool(
                str(input.get("build_request") or "").strip()
            ),
            "ravn.tool_build.request.has_inline_code": bool(
                str(input.get("tool_code") or "").strip()
            ),
            "ravn.autonomy.mode": self._autonomy_mode,
        }
        manifest = input.get("manifest")
        if isinstance(manifest, dict):
            attributes["ravn.tool_build.name"] = str(manifest.get("name") or "")
        with telemetry.span("ravn.tool_build.lifecycle", attributes=attributes) as span:
            telemetry.event("ravn.tool_build.requested", attributes=attributes, content=input)
            try:
                result = await self._execute_pipeline(input)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__, str(exc))
                telemetry.count(
                    "ravn.tool_build.lifecycle.operations",
                    attributes={
                        "ravn.tool_build.mode": attributes["ravn.tool_build.mode"],
                        "ravn.tool_build.backend": attributes["ravn.tool_build.backend"],
                        "ravn.tool_build.outcome": "exception",
                        "error.type": type(exc).__name__,
                    },
                )
                raise
            outcome = _build_result_outcome(result)
            span.set_attribute("ravn.tool_build.outcome", outcome)
            if result.is_error:
                telemetry.mark_error(span, "tool_build_failed", result.content)
            telemetry.event(
                "ravn.tool_build.finished",
                attributes={**attributes, "ravn.tool_build.outcome": outcome},
                content={"is_error": result.is_error, "result": result.content},
            )
            telemetry.count(
                "ravn.tool_build.lifecycle.operations",
                attributes={
                    "ravn.tool_build.mode": attributes["ravn.tool_build.mode"],
                    "ravn.tool_build.backend": attributes["ravn.tool_build.backend"],
                    "ravn.tool_build.outcome": outcome,
                },
            )
            return result

    async def _execute_pipeline(self, input: dict) -> ToolResult:  # noqa: A002
        telemetry = get_observability()
        try:
            input = await self._maybe_commission(input)
            artifact = _artifact_from_input(input)
            telemetry.set_attributes(
                {
                    "ravn.tool_build.artifact.id": artifact.artifact_id,
                    "ravn.tool_build.name": artifact.manifest.name,
                }
            )
            telemetry.event(
                "ravn.tool_build.artifact.materialized",
                attributes={
                    "ravn.tool_build.artifact.id": artifact.artifact_id,
                    "ravn.tool_build.name": artifact.manifest.name,
                    "ravn.tool_build.requirements.count": len(artifact.requirements),
                    "ravn.tool_build.has.tests": bool(artifact.test_code),
                },
                content={
                    "manifest": artifact.manifest.to_dict(),
                    "requirements": artifact.requirements,
                    "provenance": artifact.provenance,
                },
            )

            # Never trust the builder's own "it works": independently verify the
            # returned code in a throwaway venv, repairing on failure, BEFORE the
            # review/install path ever sees it. A hard-failed verification is
            # never installed.
            artifact, verify_error = await self._verify_and_repair(input, artifact)

            # Persist the artifact (with the recorded verification outcome) even
            # when verification hard-failed, so the failure is auditable, then
            # fail loudly without installing.
            artifact_path = write_learned_tool_artifact(
                artifacts_dir=self._artifacts_dir,
                artifact=artifact,
            )
            telemetry.event(
                "ravn.tool_build.artifact.persisted",
                attributes={
                    "ravn.tool_build.artifact.id": artifact.artifact_id,
                    "ravn.tool_build.artifact.envelope.path": str(artifact_path),
                },
            )
            if verify_error is not None:
                self._complete_commission(input)
                return verify_error

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
                confidence=self._flock_confidence,
            )
            review = await self._review(resident_artifact)
            if review.blocking_findings:
                telemetry.event(
                    "ravn.tool_build.review.blocked",
                    attributes={"ravn.review.outcome": review.outcome},
                    content={
                        "rationale": review.rationale,
                        "findings": review.findings,
                    },
                )
                self._complete_commission(input)
                return ToolResult(
                    tool_call_id="",
                    content="build_tool rejected by review: " + "; ".join(review.blocking_findings),
                    is_error=True,
                )
            if not review_allows_install(review, self._autonomy_mode):
                review_filed = await self._file_install_review(resident_artifact, artifact, review)
                telemetry.event(
                    "ravn.tool_build.review.held",
                    attributes={
                        "ravn.review.outcome": review.outcome,
                        "ravn.review.filed": review_filed,
                    },
                )
                self._complete_commission(input)
                return ToolResult(
                    tool_call_id="",
                    content=_review_summary(artifact, artifact_path, review_filed=review_filed),
                    is_error=not review_filed,
                )

            tool_path = write_learned_tool(tools_dir=self._tools_dir, artifact=artifact)
            self._complete_commission(input, artifact=artifact)
            telemetry.event(
                "ravn.tool_build.tool.persisted",
                attributes={
                    "ravn.tool_build.artifact.id": artifact.artifact_id,
                    "ravn.tool_build.tool.code.path": str(tool_path),
                },
            )
            learned_tool = load_learned_tool(
                artifact=artifact,
                tool_path=tool_path,
                timeout_seconds=self._timeout_seconds,
                runner=self._runner_for_backend(),
                # Canonical venv home beside the learned-tools dir: a freshly
                # built tool with requirements must be runnable on the local
                # backend, not refuse for want of a venvs_dir.
                venvs_dir=learned_tool_venvs_dir(self._tools_dir.parent),
            )

            if isinstance(canary_input, dict):
                with telemetry.span(
                    "ravn.tool_build.canary",
                    attributes={"ravn.tool_build.name": artifact.manifest.name},
                ) as canary_span:
                    canary = await learned_tool.execute(canary_input)
                    canary_outcome = "error" if canary.is_error else "passed"
                    canary_span.set_attribute("ravn.tool_build.canary.outcome", canary_outcome)
                    telemetry.event(
                        "ravn.tool_build.canary.finished",
                        attributes={"ravn.tool_build.canary.outcome": canary_outcome},
                        content={"input": canary_input, "result": canary.content},
                    )
                    telemetry.count(
                        "ravn.tool_build.canary.operations",
                        attributes={"ravn.tool_build.canary.outcome": canary_outcome},
                    )
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
            lifecycle_warning = ""
            if self._installed_artifact_recorder is not None:
                lifecycle_attributes = {
                    "ravn.learned_tool.name": artifact.manifest.name,
                    "ravn.learned_tool.artifact_id": artifact.artifact_id,
                    "ravn.skill.lifecycle.action": "register",
                }
                with telemetry.span(
                    "ravn.learned_tool.lifecycle.register",
                    attributes=lifecycle_attributes,
                ) as lifecycle_span:
                    try:
                        await self._installed_artifact_recorder(resident_artifact)
                        telemetry.event(
                            "ravn.learned_tool.lifecycle.registered",
                            attributes=lifecycle_attributes,
                        )
                    except Exception as exc:  # noqa: BLE001 — installation already succeeded
                        lifecycle_warning = (
                            "The tool is installed, but its lifecycle record could not be "
                            f"updated: {type(exc).__name__}: {exc}"
                        )
                        telemetry.mark_error(
                            lifecycle_span,
                            type(exc).__name__,
                            str(exc),
                        )
                        logger.warning(
                            "Learned tool %s installed, but lifecycle registration failed",
                            artifact.manifest.name,
                            exc_info=True,
                        )
            publish_learned_tool_inventory([artifact])
            telemetry.event(
                "ravn.tool_build.registered",
                attributes={
                    "ravn.tool_build.name": artifact.manifest.name,
                    "ravn.tool_build.replaced": replace,
                },
            )
            flock_warning = ""
            try:
                await self._publish_flock_proposal(artifact)
            except Exception as exc:  # noqa: BLE001 — publication follows successful install
                flock_warning = (
                    "The tool is installed and registered, but its Flock "
                    f"proposal could not be published: {type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "Learned tool %s installed, but Flock publication failed",
                    artifact.manifest.name,
                    exc_info=True,
                )
                telemetry.event(
                    "ravn.tool_build.flock.failed",
                    attributes={
                        "ravn.tool_build.name": artifact.manifest.name,
                        "error.type": type(exc).__name__,
                    },
                    content=str(exc),
                )
        except ToolBuildInputRequiredError as exc:
            return self._input_required_result(exc)
        except ToolBuildPendingError as exc:
            return self._pending_result(exc)
        except (LearnedToolError, ToolBuildError, TypeError, ValueError) as exc:
            return ToolResult(tool_call_id="", content=f"build_tool failed: {exc}", is_error=True)

        return ToolResult(
            tool_call_id="",
            content=_summary(
                artifact,
                tool_path,
                artifact_path,
                registered=True,
                flock_warning=flock_warning,
                lifecycle_warning=lifecycle_warning,
            ),
        )

    async def _maybe_commission(self, input: dict) -> dict:  # noqa: A002
        """Commission a build backend when the agent asked for one.

        When the agent supplies a build_request (and no inline tool_code) and a
        backend is configured, the tool is developed in a Forge session / Ting
        workflow; the produced manifest + code merge back into the input so the
        rest of execute() reviews and installs it identically to an inline tool.
        """
        continuation_task_id = str(input.get("continuation_task_id") or "").strip()
        if continuation_task_id:
            return await self._resume_commission(input, task_id=continuation_task_id)

        build_request = str(input.get("build_request") or "").strip()
        tool_code = str(input.get("tool_code") or "").strip()
        telemetry = get_observability()
        if tool_code:
            telemetry.event(
                "ravn.tool_build.route.selected",
                attributes={
                    "ravn.tool_build.route": "inline",
                    "ravn.tool_build.route.reason": "inline_code_supplied",
                    "ravn.tool_build.backend.configured": self._build_backend is not None,
                },
            )
            return input
        if not build_request:
            telemetry.event(
                "ravn.tool_build.route.selected",
                attributes={
                    "ravn.tool_build.route": "inline",
                    "ravn.tool_build.route.reason": "no_build_request",
                    "ravn.tool_build.backend.configured": self._build_backend is not None,
                },
            )
            return input
        if self._build_backend is None:
            telemetry.event(
                "ravn.tool_build.route.selected",
                attributes={
                    "ravn.tool_build.route": "rejected",
                    "ravn.tool_build.route.reason": "backend_unconfigured",
                    "ravn.tool_build.backend.configured": False,
                },
            )
            raise LearnedToolError(
                "build_request was given but no tool build backend is configured"
            )
        telemetry.event(
            "ravn.tool_build.route.selected",
            attributes={
                "ravn.tool_build.route": "commissioned",
                "ravn.tool_build.route.reason": "build_request_supplied",
                "ravn.tool_build.backend": str(getattr(self._build_backend, "name", "unknown")),
                "ravn.tool_build.backend.configured": True,
                "ravn.tool_build.inline_fallback.enabled": False,
            },
        )
        return await self._commission_and_merge(input, signal_context_suffix="")

    def _build_backend_request(self, input: dict, *, signal_context_suffix: str) -> Any:  # noqa: A002
        """Build the ToolBuildRequest for an (initial or repair) commission."""
        from ravn.ports.tool_build_backend import ToolBuildRequest  # noqa: PLC0415

        manifest_in = input.get("manifest") if isinstance(input.get("manifest"), dict) else {}
        declared_reach = LearnedToolManifest.from_dict(manifest_in).declared_reach
        signal_context = str(input.get("signal_context") or "")
        if signal_context_suffix:
            signal_context = (
                f"{signal_context}\n\n{signal_context_suffix}"
                if signal_context
                else signal_context_suffix
            )
        return ToolBuildRequest(
            name=str(manifest_in.get("name") or ""),
            description=str(manifest_in.get("description") or ""),
            build_request=str(input.get("build_request") or "").strip(),
            input_schema=dict(manifest_in.get("input_schema") or {"type": "object"}),
            required_permission=str(manifest_in.get("required_permission") or "tool:run"),
            declared_reach=[grant.to_dict() for grant in declared_reach],
            entry_point=str(manifest_in.get("entry_point") or "run"),
            environment_id=self._environment_id,
            valkyrie_id=self._valkyrie_id,
            domain=self._domain,
            signal_context=signal_context,
            operation_id=str(input.get("_build_operation_id") or ""),
            continuation=dict(input.get("_build_continuation") or {}),
        )

    async def _commission_and_merge(self, input: dict, *, signal_context_suffix: str) -> dict:  # noqa: A002
        """Commission the backend and merge its result back into the input."""
        prepared = dict(input)
        operation_id = ""
        if bool(getattr(self._build_backend, "supports_restart_recovery", False)):
            prepared = self._prepare_commission(
                input,
                replace_current=bool(signal_context_suffix),
            )
            operation_id = str(prepared["_build_operation_id"])
        request = self._build_backend_request(
            prepared,
            signal_context_suffix=signal_context_suffix,
        )
        try:
            result = await self._build_backend.build(request)
        except (ToolBuildInputRequiredError, ToolBuildPendingError) as exc:
            self._persist_pending_build(prepared, exc)
            if operation_id:
                self._delete_commission(operation_id)
            raise
        merged = dict(prepared)
        merged.pop("_build_continuation", None)
        merged.pop("continuation_task_id", None)
        merged.pop("continuation_answer", None)
        merged.pop("continuation_metadata", None)
        merged["manifest"] = result.manifest
        merged["tool_code"] = result.tool_code
        merged["test_code"] = result.test_code
        merged["requirements"] = list(result.requirements)
        provenance = dict(input.get("provenance") or {})
        provenance.update(result.provenance)
        if operation_id:
            provenance["build_operation_id"] = operation_id
        if result.build_evidence:
            provenance["build_evidence"] = dict(result.build_evidence)
        merged["provenance"] = provenance
        return merged

    async def _resume_commission(self, input: dict, *, task_id: str) -> dict:  # noqa: A002
        """Resume the exact commissioned A2A task suspended on remote input."""
        if self._build_backend is None:
            raise LearnedToolError(
                "continuation_task_id was given but no tool build backend is configured"
            )
        pending = self._load_pending_build(task_id)
        original = pending.get("input")
        continuation = pending.get("continuation")
        if not isinstance(original, dict) or not isinstance(continuation, dict):
            raise LearnedToolError(f"pending build {task_id!r} has invalid durable state")
        if str(continuation.get("task_id") or "") != task_id:
            raise LearnedToolError(f"pending build state does not match task {task_id!r}")

        continuation = dict(continuation)
        input_kind = str(continuation.get("input_kind") or "")
        answer = str(input.get("continuation_answer") or "").strip()
        if input_kind != "pending" and not answer:
            raise LearnedToolError("continuation_answer must be non-empty")
        if answer:
            continuation["answer"] = answer
        supplied_metadata = input.get("continuation_metadata")
        if supplied_metadata is not None and not isinstance(supplied_metadata, dict):
            raise LearnedToolError("continuation_metadata must be an object")
        reply_metadata = continuation.get("reply_metadata")
        reply_metadata = dict(reply_metadata) if isinstance(reply_metadata, dict) else {}
        reply_metadata.update(dict(supplied_metadata or {}))
        continuation["reply_metadata"] = reply_metadata

        resumed = dict(original)
        resumed["_build_continuation"] = continuation
        telemetry = get_observability()
        attributes = {
            "a2a.task.id": task_id,
            "a2a.input.kind": str(continuation.get("input_kind") or ""),
            "ravn.tool_build.backend": str(getattr(self._build_backend, "name", "unknown")),
        }
        telemetry.event(
            "ravn.tool_build.continuation.resumed",
            attributes=attributes,
            content={
                "answer": answer,
                "reply_metadata": reply_metadata,
            },
        )
        try:
            merged = await self._commission_and_merge(
                resumed,
                signal_context_suffix="",
            )
        except (ToolBuildInputRequiredError, ToolBuildPendingError):
            raise
        self._delete_pending_build(task_id)
        telemetry.event(
            "ravn.tool_build.continuation.completed",
            attributes=attributes,
        )
        return merged

    def _persist_pending_build(
        self,
        input: dict,  # noqa: A002
        required: ToolBuildInputRequiredError | ToolBuildPendingError,
    ) -> None:
        """Atomically persist the minimum state needed to resume one build."""
        original = {
            key: value
            for key, value in input.items()
            if key
            not in {
                "_build_continuation",
                "_build_operation_id",
                "continuation_task_id",
                "continuation_answer",
                "continuation_metadata",
            }
        }
        payload = {
            "version": 1,
            "task_id": required.task_id,
            "input": original,
            "continuation": required.continuation,
        }
        path = self._pending_build_path(required.task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        get_observability().event(
            "ravn.tool_build.continuation.suspended",
            attributes={
                "a2a.task.id": required.task_id,
                "a2a.input.kind": str(required.continuation.get("input_kind") or ""),
                "ravn.tool_build.pending.path": str(path),
            },
            content={
                "prompt": getattr(required, "prompt", ""),
                "input_payload": required.continuation.get("input_payload", {}),
                "reply_metadata": required.continuation.get("reply_metadata", {}),
            },
        )

    def _load_pending_build(self, task_id: str) -> dict[str, Any]:
        path = self._pending_build_path(task_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LearnedToolError(
                f"no pending commissioned build exists for task {task_id!r}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise LearnedToolError(
                f"could not read pending commissioned build {task_id!r}: {exc}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise LearnedToolError(f"pending build {task_id!r} has invalid durable state")
        if str(payload.get("task_id") or "") != task_id:
            raise LearnedToolError(f"pending build state does not match task {task_id!r}")
        return payload

    def _delete_pending_build(self, task_id: str) -> None:
        path = self._pending_build_path(task_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _pending_build_path(self, task_id: str) -> Path:
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        return self._artifacts_dir / "pending-builds" / f"{digest}.json"

    def _prepare_commission(
        self,
        input: dict,  # noqa: A002
        *,
        replace_current: bool,
    ) -> dict:
        prepared = dict(input)
        current_id = str(prepared.get("_build_operation_id") or "").strip()
        if current_id and not replace_current:
            return prepared
        if current_id:
            self._delete_commission(current_id)

        manifest = prepared.get("manifest")
        manifest = manifest if isinstance(manifest, dict) else {}
        tool_name = str(manifest.get("name") or "").strip()
        existing = None if replace_current else self._pending_commission(tool_name)
        if existing is not None:
            operation_id = str(existing["operation_id"])
        else:
            operation_id = str(uuid4())
            original = {
                key: value
                for key, value in prepared.items()
                if key
                not in {
                    "_build_continuation",
                    "_build_operation_id",
                    "continuation_task_id",
                    "continuation_answer",
                    "continuation_metadata",
                }
            }
            self._write_commission(
                operation_id,
                {
                    "version": 1,
                    "operation_id": operation_id,
                    "tool_name": tool_name,
                    "environment_id": self._environment_id,
                    "valkyrie_id": self._valkyrie_id,
                    "state": "submitting",
                    "input": original,
                },
            )
            get_observability().event(
                "ravn.tool_build.commission.persisted",
                attributes={
                    "ravn.tool_build.operation.id": operation_id,
                    "ravn.tool_build.name": tool_name,
                    "ravn.tool_build.commission.state": "submitting",
                },
            )

        prepared["_build_operation_id"] = operation_id
        return prepared

    def _pending_commission(self, tool_name: str) -> dict[str, Any] | None:
        if not tool_name:
            return None
        return next(
            (
                payload
                for payload in self._commission_records()
                if str(payload.get("tool_name") or "") == tool_name
                and str(payload.get("environment_id") or "") == self._environment_id
                and str(payload.get("valkyrie_id") or "") == self._valkyrie_id
            ),
            None,
        )

    def _commission_records(self) -> list[dict[str, Any]]:
        directory = self._artifacts_dir / "pending-commissions"
        try:
            paths = sorted(
                directory.glob("*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return []
        records: list[dict[str, Any]] = []
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict) or payload.get("version") != 1:
                continue
            records.append(payload)
        return records

    def _write_commission(self, operation_id: str, payload: dict[str, Any]) -> None:
        path = self._commission_path(operation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    def _complete_commission(
        self,
        input: dict,  # noqa: A002
        *,
        artifact: LearnedToolArtifact | None = None,
    ) -> None:
        operation_id = str(input.get("_build_operation_id") or "").strip()
        completed_ids: set[str] = set()
        if operation_id:
            self._delete_commission(operation_id)
            completed_ids.add(operation_id)
        if artifact is not None:
            for record in self._commission_records():
                stale_id = str(record.get("operation_id") or "")
                if not stale_id or not self._artifact_satisfies_commission(artifact, record):
                    continue
                self._delete_commission(stale_id)
                completed_ids.add(stale_id)
        for completed_id in completed_ids:
            get_observability().event(
                "ravn.tool_build.commission.completed",
                attributes={"ravn.tool_build.operation.id": completed_id},
            )

    def _installed_artifact_for_commission(
        self,
        record: dict[str, Any],
    ) -> LearnedToolArtifact | None:
        tool_name = str(record.get("tool_name") or "").strip()
        if not tool_name:
            return None
        artifact_path = learned_tool_artifact_path(self._artifacts_dir, tool_name)
        code_path = learned_tool_path(self._tools_dir, tool_name)
        if not artifact_path.is_file() or not code_path.is_file():
            return None
        try:
            artifact = read_learned_tool_artifact(artifact_path)
            installed_code = code_path.read_text(encoding="utf-8")
        except (LearnedToolError, OSError, ValueError):
            return None
        if installed_code != artifact.tool_code:
            return None
        if not self._artifact_satisfies_commission(artifact, record):
            return None
        return artifact

    @staticmethod
    def _artifact_satisfies_commission(
        artifact: LearnedToolArtifact,
        record: dict[str, Any],
    ) -> bool:
        if artifact.manifest.name != str(record.get("tool_name") or ""):
            return False
        verification = artifact.provenance.get("verification")
        if not isinstance(verification, dict) or verification.get("ok") is not True:
            return False
        operation_id = str(record.get("operation_id") or "")
        if operation_id and artifact.provenance.get("build_operation_id") == operation_id:
            return True
        original = record.get("input")
        if not isinstance(original, dict):
            return False
        if original.get("replace") or str(original.get("build_request") or "").strip():
            return False
        return bool(str(original.get("tool_code") or "").strip())

    def _delete_commission(self, operation_id: str) -> None:
        try:
            self._commission_path(operation_id).unlink()
        except FileNotFoundError:
            return

    def _commission_path(self, operation_id: str) -> Path:
        digest = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        return self._artifacts_dir / "pending-commissions" / f"{digest}.json"

    def _input_required_result(self, required: ToolBuildInputRequiredError) -> ToolResult:
        continuation = required.continuation
        payload = {
            "status": "input_required",
            "backend": str(getattr(self._build_backend, "name", "unknown")),
            "task_id": required.task_id,
            "input_kind": required.input_kind,
            "question": required.prompt,
            "input_payload": continuation.get("input_payload", {}),
            "reply_metadata": continuation.get("reply_metadata", {}),
            "resume_with": {
                "continuation_task_id": required.task_id,
                "continuation_answer": "<answer>",
            },
            "next_step": (
                "Answer from reliable available evidence, or ask the operator this exact "
                "question. Then call build_tool with continuation_task_id and "
                "continuation_answer to resume the same A2A task."
            ),
        }
        if required.input_kind == "gate":
            payload["resume_with"]["continuation_metadata"] = {
                "gateDecision": "approve | request_changes"
            }
        return ToolResult(
            tool_call_id="",
            content=json.dumps(payload, indent=2, sort_keys=True),
            is_error=False,
        )

    def _pending_result(self, pending: ToolBuildPendingError) -> ToolResult:
        return ToolResult(
            tool_call_id="",
            content=json.dumps(
                {
                    "status": "pending",
                    "backend": str(getattr(self._build_backend, "name", "unknown")),
                    "task_id": pending.task_id,
                    "push_registered": pending.push_registered,
                    "resume_with": {"continuation_task_id": pending.task_id},
                    "next_step": (
                        "Do not poll. Sleep with reason=external_event. The A2A callback "
                        "will wake this resident; then call build_tool once with the returned "
                        "continuation_task_id to process the new task state."
                    ),
                },
                indent=2,
                sort_keys=True,
            ),
            is_error=False,
        )

    async def _verify_and_repair(
        self,
        input: dict,  # noqa: A002
        artifact: LearnedToolArtifact,
    ) -> tuple[LearnedToolArtifact, ToolResult | None]:
        """Independently verify the built tool, repairing up to the bounded limit.

        Control flow, in order:
          1. Verify the current artifact in a throwaway venv.
          2. On pass (or skip for empty test_code): record the outcome into
             ``provenance["verification"]`` and return the artifact.
          3. On failure, up to ``max_repair_attempts`` times:
             (a) missing module not already declared -> append it to
                 requirements, rebuild the artifact, re-verify (deterministic
                 dependency heal, no LLM);
             (b) else a build backend is configured -> re-commission with the
                 failing logs appended to signal_context, rebuild, re-verify;
             (c) else (inline tool, no backend) -> stop and return a clear error.
          4. If still failing after the budget: return a clear error. Never
             install a tool whose verification hard-failed.
        """
        attempts = 0
        result = self._verify(artifact)
        self._record_verification_telemetry(artifact, result, attempt=0, repair="none")
        while not result.ok and attempts < self._max_repair_attempts:
            attempts += 1
            repaired, input = self._repair_dependency(input, artifact, result)
            if repaired is not None:
                artifact = repaired
                result = self._verify(artifact)
                self._record_verification_telemetry(
                    artifact,
                    result,
                    attempt=attempts,
                    repair="dependency",
                )
                continue
            if self._build_backend is not None:
                input = await self._commission_and_merge(
                    input,
                    signal_context_suffix=_repair_brief(result),
                )
                artifact = _artifact_from_input(input)
                result = self._verify(artifact)
                self._record_verification_telemetry(
                    artifact,
                    result,
                    attempt=attempts,
                    repair="recommission",
                )
                continue
            # Inline tool, no backend, no deterministic dependency heal: stop.
            break

        artifact = _record_verification(artifact, result, attempts)
        if not result.ok:
            return artifact, ToolResult(
                tool_call_id="",
                content=(
                    "build_tool aborted: independent verification failed after "
                    f"{attempts} repair attempt(s); tool was NOT installed.\n"
                    f"{result.logs}"
                ),
                is_error=True,
            )
        return artifact, None

    def _verify(self, artifact: LearnedToolArtifact) -> VerificationResult:
        telemetry = get_observability()
        attributes = {
            "ravn.tool_build.name": artifact.manifest.name,
            "ravn.tool_build.requirements.count": len(artifact.requirements),
            "ravn.tool_build.has.tests": bool(artifact.test_code),
        }
        with telemetry.span("ravn.tool_build.verify", attributes=attributes) as span:
            result = verify_learned_tool_in_ephemeral_venv(
                tool_name=artifact.manifest.name,
                tool_code=artifact.tool_code,
                test_code=artifact.test_code,
                requirements=list(artifact.requirements),
                entry_point=artifact.manifest.entry_point,
            )
            span.set_attribute(
                "ravn.tool_build.verify.outcome",
                "passed" if result.ok else "failed",
            )
            return result

    def _record_verification_telemetry(
        self,
        artifact: LearnedToolArtifact,
        result: VerificationResult,
        *,
        attempt: int,
        repair: str,
    ) -> None:
        telemetry = get_observability()
        outcome = "passed" if result.ok else "failed"
        attributes = {
            "ravn.tool_build.name": artifact.manifest.name,
            "ravn.tool_build.verify.outcome": outcome,
            "ravn.tool_build.verify.attempt": attempt,
            "ravn.tool_build.repair.kind": repair,
            "ravn.tool_build.missing_module": result.missing_module or "",
        }
        telemetry.event(
            "ravn.tool_build.verification",
            attributes=attributes,
            content={"logs": result.logs},
        )
        telemetry.count(
            "ravn.tool_build.verifications",
            attributes={
                "ravn.tool_build.verify.outcome": outcome,
                "ravn.tool_build.repair.kind": repair,
            },
        )

    def _repair_dependency(
        self,
        input: dict,  # noqa: A002
        artifact: LearnedToolArtifact,
        result: VerificationResult,
    ) -> tuple[LearnedToolArtifact | None, dict]:
        """Deterministically heal a missing dependency, if that's the failure.

        Returns ``(rebuilt_artifact, updated_input)`` on a heal, or
        ``(None, input)`` when the failure is not a fresh missing module.
        """
        missing = result.missing_module
        if not missing or missing in artifact.requirements:
            return None, input
        requirements = [*artifact.requirements, missing]
        updated_input = dict(input)
        updated_input["requirements"] = list(requirements)
        rebuilt = _artifact_with_requirements(artifact, requirements)
        return rebuilt, updated_input

    async def _review(self, resident_artifact: ResidentLearningArtifact) -> ReviewResult:
        identity = ResidentLearningIdentity(
            environment_id=self._environment_id,
            valkyrie_id=self._valkyrie_id,
            domain=self._domain,
            flock_ids=[self._flock_id] if self._flock_id else [],
            autonomy_mode=self._autonomy_mode,
        )
        request, build = review_inputs(resident_artifact, identity)
        telemetry = get_observability()
        attributes = {
            "ravn.tool_build.name": resident_artifact.title,
            "ravn.autonomy.mode": self._autonomy_mode,
        }
        with telemetry.span("ravn.tool_build.review", attributes=attributes) as span:
            result = await self._reviewer.review(
                request=request,
                build=build,
                autonomy_mode=self._autonomy_mode,
            )
            span.set_attribute("ravn.review.outcome", result.outcome)
            telemetry.event(
                "ravn.tool_build.reviewed",
                attributes={
                    **attributes,
                    "ravn.review.outcome": result.outcome,
                    "ravn.review.findings.count": len(result.findings),
                },
                content={
                    "rationale": result.rationale,
                    "findings": result.findings,
                },
            )
            telemetry.count(
                "ravn.tool_build.reviews",
                attributes={"ravn.review.outcome": result.outcome},
            )
            return result

    async def _file_install_review(
        self,
        resident_artifact: ResidentLearningArtifact,
        artifact: LearnedToolArtifact,
        review: ReviewResult,
    ) -> bool:
        if self._review_requester is None:
            return False
        safety_class = manifest_safety_class(artifact.manifest)
        item = ReviewItem.new(
            kind=ReviewKind.EVOLUTION_BUILD.value,
            requested_action="install",
            environment_id=self._environment_id,
            valkyrie_id=self._valkyrie_id,
            title=artifact.manifest.name,
            summary=artifact.manifest.description,
            flock_id=self._flock_id,
            domain=self._domain,
            risk_class=risk_class_for_safety(safety_class),
            safety_class=safety_class,
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
                "investigation_prompt": self._investigation_prompt(),
            },
            requested_by=self._valkyrie_id,
            correlation_id=artifact.artifact_id,
        )
        return await self._review_requester.request(item) is not None

    async def _publish_flock_proposal(self, artifact: LearnedToolArtifact) -> None:
        if self._publisher is None or not self._flock_id:
            get_observability().event(
                "ravn.tool_build.flock.skipped",
                attributes={
                    "ravn.tool_build.name": artifact.manifest.name,
                    "ravn.tool_build.flock.reason": (
                        "publisher_unavailable" if self._publisher is None else "flock_unconfigured"
                    ),
                },
            )
            return
        telemetry = get_observability()
        with telemetry.span(
            "ravn.tool_build.flock.publish",
            attributes={"ravn.tool_build.name": artifact.manifest.name},
        ):
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
                    confidence=self._flock_confidence,
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
            telemetry.event(
                "ravn.tool_build.flock.proposed",
                attributes={"ravn.tool_build.name": artifact.manifest.name},
            )
            telemetry.count("ravn.tool_build.flock.proposals")

    def _runner_for_backend(self) -> Any | None:
        workspace_root = self._workspace_root or self._tools_dir.parent
        return learned_tool_runner_for_backend(
            self._execution_backend,
            workspace_root=workspace_root,
            venvs_dir=learned_tool_venvs_dir(self._tools_dir.parent),
            sandbox_shell=self._sandbox_shell,
            backend_kwargs=self._execution_backend_kwargs,
        )


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
    execution_backend_kwargs: Mapping[str, Any] | None = None,
    workspace_root: str | Path | None = None,
    sandbox_shell: Any | None = None,
    build_backend: Any | None = None,
    investigation_context: Callable[[], str] | None = None,
    max_repair_attempts: int = DEFAULT_MAX_REPAIR_ATTEMPTS,
    flock_confidence: float = SELF_REGISTERED_TOOL_CONFIDENCE,
    installed_artifact_recorder: InstalledArtifactRecorder | None = None,
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
        execution_backend_kwargs=execution_backend_kwargs,
        workspace_root=workspace_root,
        sandbox_shell=sandbox_shell,
        build_backend=build_backend,
        investigation_context=investigation_context,
        max_repair_attempts=max_repair_attempts,
        flock_confidence=flock_confidence,
        installed_artifact_recorder=installed_artifact_recorder,
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
    confidence: float = SELF_REGISTERED_TOOL_CONFIDENCE,
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
        confidence=confidence,
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


def _artifact_with_requirements(
    artifact: LearnedToolArtifact,
    requirements: list[str],
) -> LearnedToolArtifact:
    """Rebuild an artifact with a healed requirements list."""
    return dataclass_replace(artifact, requirements=list(requirements))


def _record_verification(
    artifact: LearnedToolArtifact,
    result: VerificationResult,
    attempts: int,
) -> LearnedToolArtifact:
    """Persist the verification outcome into provenance so review can see it."""
    provenance = dict(artifact.provenance)
    provenance["verification"] = {
        "ok": result.ok,
        "attempts": attempts,
        "logs": result.logs,
        "missing_module": result.missing_module,
    }
    return dataclass_replace(artifact, provenance=provenance)


def _repair_brief(result: VerificationResult) -> str:
    """The failing-verification brief appended to a re-commission's context."""
    return (
        "The previous build FAILED independent verification. Fix the tool and "
        "its tests so the verification passes. Verification logs:\n"
        f"{result.logs}"
    )


def _artifact_from_input(input: dict) -> LearnedToolArtifact:  # noqa: A002
    manifest_raw = input.get("manifest")
    if not isinstance(manifest_raw, dict):
        raise ValueError("manifest must be an object")
    manifest = LearnedToolManifest.from_dict(manifest_raw)
    tool_code = str(input.get("tool_code") or "")
    test_code = str(input.get("test_code") or "")
    requirements_raw = input.get("requirements")
    if requirements_raw is not None and not isinstance(requirements_raw, list):
        raise ValueError("requirements must be a list when provided")
    requirements = [str(item) for item in (requirements_raw or []) if str(item)]
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
        test_code=test_code,
        requirements=requirements,
        provenance=dict(provenance or {}),
    )


def _summary(
    artifact: LearnedToolArtifact,
    tool_path: Path,
    artifact_path: Path,
    *,
    registered: bool,
    flock_warning: str = "",
    lifecycle_warning: str = "",
) -> str:
    payload = {
        "artifact_id": artifact.artifact_id,
        "tool_name": artifact.manifest.name,
        "registered": registered,
        "required_permission": artifact.manifest.required_permission,
        "declared_reach": [grant.to_dict() for grant in artifact.manifest.declared_reach],
        "installed_code_path": str(tool_path),
        "artifact_envelope_path": str(artifact_path),
        "tests_embedded_in_envelope": bool(artifact.test_code),
        "verification": _verification_payload(artifact),
    }
    if flock_warning:
        payload["flock_publication_warning"] = flock_warning
    if lifecycle_warning:
        payload["lifecycle_warning"] = lifecycle_warning
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
        "artifact_envelope_path": str(artifact_path),
        "tests_embedded_in_envelope": bool(artifact.test_code),
        "verification": _verification_payload(artifact),
    }
    if not review_filed:
        payload["reason"] = "operator review is required but no review requester is configured"
    return json.dumps(payload, indent=2, sort_keys=True)


def _build_result_outcome(result: ToolResult) -> str:
    """Classify the lifecycle without treating a filed review as installation."""
    if result.is_error:
        return "error"
    try:
        payload = json.loads(result.content)
    except (TypeError, ValueError):
        return "completed"
    if not isinstance(payload, dict):
        return "completed"
    if payload.get("status") == "input_required":
        return "input_required"
    if payload.get("registered") is True:
        return "registered"
    if payload.get("review_required") is True and payload.get("review_filed") is True:
        return "review_pending"
    return "completed"


def _verification_payload(artifact: LearnedToolArtifact) -> dict[str, Any]:
    verification = artifact.provenance.get("verification")
    return dict(verification) if isinstance(verification, dict) else {}
