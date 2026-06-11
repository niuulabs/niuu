"""Builder and review adapters for Valkyrie tool evolution.

Builders implement :class:`EvolutionBuilderPort` and turn a capability gap
into a skill (operator instructions) plus a tool implementation (executable
probe).  Three builders exist:

* :class:`TemplateToolBuilder` — deterministic, offline; renders a generic
  read-only probe from the signal evidence.  Default and test baseline.
* :class:`AgentToolBuilder` — authors the skill and the probe implementation
  with the configured LLM.  Local self-improvement without review queues.
* :class:`WorkflowToolBuilder` — boundary for offloading tool builds to an
  external workflow engine (Forge/Volundr).  Explicitly unimplemented.

Builder selection is config-driven (``dream_cycle.builder_adapter`` with
kwargs), never hardcoded in call sites.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from ravn.ports.llm import LLMPort
from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest, ReviewResult
from ravn.valkyrie_evolution.ports import (
    EventLedgerPort,
    EvolutionBuilderPort,
    EvolutionReviewPort,
)
from sleipnir.domain.events import SleipnirEvent

DEFAULT_AGENT_BUILDER_MAX_TOKENS = 4096


class ToolBuildError(RuntimeError):
    """A builder failed to produce a valid skill + tool implementation."""


class JsonlEventLedger(EventLedgerPort):
    """JSONL-backed event ledger used by the local proof command."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._events: list[SleipnirEvent] = []

    async def record(self, event: SleipnirEvent) -> None:
        self._events.append(event)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")

    async def list_events(self) -> list[SleipnirEvent]:
        return list(self._events)


def _write_build_artifacts(
    artifact_dir: Path | None,
    *,
    skill_name: str,
    skill_content: str,
    tool_code: str,
) -> tuple[str, str]:
    """Persist a built skill + tool pair; return their paths ('' when not written)."""
    if artifact_dir is None:
        return "", ""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifact_dir / f"{skill_name}.md"
    artifact.write_text(skill_content, encoding="utf-8")
    tool_artifact = artifact_dir / f"{skill_name}.py"
    tool_artifact.write_text(tool_code, encoding="utf-8")
    return str(artifact), str(tool_artifact)


class TemplateToolBuilder(EvolutionBuilderPort):
    """Render a deterministic skill and read-only probe from gap evidence."""

    def __init__(self, *, artifact_dir: str | Path | None = None) -> None:
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    async def build(self, request: EvolutionRequest) -> BuildResult:
        gap = request.gap
        skill_name = _skill_name_from_capability(gap.capability_name)
        interesting_fields = _interesting_fields(gap.evidence)
        content = _render_skill(
            skill_name=skill_name,
            capability_name=gap.capability_name,
            gap_reason=gap.reason,
            fields=interesting_fields,
            safety_class=gap.safety_class,
        )
        tool_code = _render_tool_code(
            capability_name=gap.capability_name,
            fields=interesting_fields,
        )
        artifact_path, tool_path = _write_build_artifacts(
            self.artifact_dir,
            skill_name=skill_name,
            skill_content=content,
            tool_code=tool_code,
        )
        return BuildResult(
            request_id=request.request_id,
            skill_name=skill_name,
            skill_content=content,
            description=f"Inspect signals requiring {gap.capability_name}.",
            artifact_type="ravn_skill_tool",
            artifact_path=artifact_path,
            tool_code=tool_code,
            tool_path=tool_path,
            evidence={
                "capability_name": gap.capability_name,
                "derived_from_signal_fields": sorted(interesting_fields),
                "builder": self.__class__.__name__,
            },
        )


class AgentToolBuilder(EvolutionBuilderPort):
    """Author the skill and tool implementation with the configured LLM.

    The builder asks for a strict JSON document containing the operator skill
    markdown and a single-module Python probe, then validates the probe
    structurally (parses, defines the entry point, declares no side-effectful
    imports for read-only gaps) before returning it.  Validation failures
    raise :class:`ToolBuildError` — there is no silent fallback.
    """

    def __init__(
        self,
        *,
        llm: LLMPort,
        model: str = "",
        max_tokens: int = DEFAULT_AGENT_BUILDER_MAX_TOKENS,
        artifact_dir: str | Path | None = None,
    ) -> None:
        self._llm = llm
        self._model = model
        self._max_tokens = max_tokens
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None

    async def build(self, request: EvolutionRequest) -> BuildResult:
        gap = request.gap
        skill_name = _skill_name_from_capability(gap.capability_name)
        response = await self._llm.generate(
            [{"role": "user", "content": _agent_builder_prompt(request, skill_name)}],
            tools=[],
            system=_AGENT_BUILDER_SYSTEM,
            model=self._model,
            max_tokens=self._max_tokens,
        )
        document = _parse_builder_response(response.content, skill_name=skill_name)
        skill_content = document["skill_markdown"]
        tool_code = document["tool_code"]
        _validate_tool_code(tool_code, entry_point="run", safety_class=gap.safety_class)
        skill_content = _ensure_skill_metadata(
            skill_content,
            skill_name=skill_name,
            capability_name=gap.capability_name,
            safety_class=gap.safety_class,
        )

        artifact_path, tool_path = _write_build_artifacts(
            self.artifact_dir,
            skill_name=skill_name,
            skill_content=skill_content,
            tool_code=tool_code,
        )
        return BuildResult(
            request_id=request.request_id,
            skill_name=skill_name,
            skill_content=skill_content,
            description=f"Inspect signals requiring {gap.capability_name}.",
            artifact_type="ravn_skill_tool",
            artifact_path=artifact_path,
            tool_code=tool_code,
            tool_path=tool_path,
            evidence={
                "capability_name": gap.capability_name,
                "builder": self.__class__.__name__,
                "model": self._model or "default",
            },
        )


class WorkflowToolBuilder(EvolutionBuilderPort):
    """Boundary for offloading tool builds to an external workflow engine.

    Wire this to Forge/Volundr workflow orchestration before selecting it in
    ``dream_cycle.builder_adapter``.
    """

    def __init__(self, *, endpoint: str = "", workflow: str = "valkyrie-tool-build") -> None:
        self.endpoint = endpoint
        self.workflow = workflow

    async def build(self, request: EvolutionRequest) -> BuildResult:
        raise NotImplementedError(
            "WorkflowToolBuilder defines the external workflow boundary. "
            "Wire it to Forge/Volundr workflow orchestration before selecting it."
        )


class PolicyCourtReviewer(EvolutionReviewPort):
    """Court-backed review gate for built and adopted evolution artifacts.

    The install/approve decision delegates to the shared
    :class:`~ravn.context.autonomy.AutonomyPolicy` — the same authority model
    that gates every other resident self-improvement — and the artifact itself
    is validated structurally (AST import/entry-point analysis for tool
    implementations, declared metadata for skills) instead of by prose
    pattern matching.  Every decision can be recorded through the court audit
    machinery via an optional :class:`~ravn.odin.court.CourtAuditSink`.
    """

    def __init__(
        self,
        *,
        reviewer: str = "odin:policy-court",
        audit_sink: Any | None = None,
    ) -> None:
        from ravn.context.autonomy import AutonomyPolicy  # noqa: PLC0415

        self.reviewer = reviewer
        self._audit_sink = audit_sink
        self._policy = AutonomyPolicy()

    async def review(
        self,
        *,
        request: EvolutionRequest,
        build: BuildResult,
        autonomy_mode: str,
    ) -> ReviewResult:
        from ravn.context.autonomy import SelfImprovementProposal  # noqa: PLC0415

        blocking = _structural_findings(request, build)
        proposal = SelfImprovementProposal(
            proposal_id=request.request_id,
            title=build.skill_name,
            artifact_type="skill",
            action="create",
            content=build.skill_content,
            scope=_local_install_scope(request.target_scope),
            environment_id=request.gap.environment_id,
            domain=request.gap.domain,
            mode=_policy_mode(autonomy_mode),
            risk_class=_risk_class(request.gap.safety_class),
        )
        decision = self._policy.decide(proposal)

        findings = list(blocking)
        if decision.decision != "allow":
            findings.append(f"policy: {decision.reason}")

        if blocking:
            approved = False
            outcome = "rejected"
            rationale = "Artifact failed structural court review: " + "; ".join(blocking)
        elif decision.decision == "allow":
            approved = True
            outcome = "yolo_approved" if autonomy_mode.lower() == "yolo" else "approved"
            rationale = decision.reason
        else:
            approved = False
            outcome = "needs_approval"
            rationale = decision.reason

        review = ReviewResult(
            request_id=request.request_id,
            artifact_name=build.skill_name,
            approved=approved,
            outcome=outcome,
            rationale=rationale,
            reviewer=self.reviewer,
            required_for_activation=autonomy_mode.lower() != "yolo",
            findings=findings,
            blocking_findings=list(blocking),
        )
        await self._record_audit(request, review)
        return review

    async def _record_audit(self, request: EvolutionRequest, review: ReviewResult) -> None:
        if self._audit_sink is None:
            return
        from datetime import UTC, datetime  # noqa: PLC0415

        from ravn.odin.court import CourtDecisionRecord  # noqa: PLC0415

        await self._audit_sink.record_decision(
            CourtDecisionRecord(
                audit_ref=f"evolution-review:{request.request_id}",
                environment_id=request.gap.environment_id,
                root_correlation_id=request.request_id,
                decision=review.outcome,
                tier="silent",
                action_authorization="autonomous" if review.approved else "human_review_required",
                escalation_path="review_queue" if not review.approved else "action_executor",
                huddle_id="",
                judgment_refs=[],
                action_refs=[],
                rejected_refs=[],
                dissent=[],
                evidence=[
                    {
                        "artifact_name": review.artifact_name,
                        "findings": list(review.findings),
                        "blocking_findings": list(review.blocking_findings),
                    }
                ],
                rationale=review.rationale,
                created_at=datetime.now(UTC),
                raw_event_ids=[],
            )
        )


_AGENT_BUILDER_SYSTEM = (
    "You are a resident Valkyrie improving its own tooling. You write small, "
    "safe, dependency-free Python probes that inspect operational signals. "
    "Respond with a single JSON object and nothing else."
)

# Imports a read-only probe may use. Everything else is rejected up front so
# the build fails fast instead of failing review later.
_READ_ONLY_ALLOWED_IMPORTS = frozenset(
    {"json", "re", "math", "datetime", "collections", "itertools", "statistics"}
)


def _agent_builder_prompt(request: EvolutionRequest, skill_name: str) -> str:
    gap = request.gap
    return f"""A resident Valkyrie in environment `{gap.environment_id}` (domain `{gap.domain}`)
observed a signal it has no capability for.

Capability gap: {gap.capability_name}
Reason: {gap.reason}
Safety class: {gap.safety_class}
Signal evidence (JSON):
{json.dumps(gap.evidence, indent=2, sort_keys=True, default=str)}

Author both artifacts for this capability:

1. `skill_markdown` — operator instructions for handling future signals of
   this kind. Start with `# skill: {skill_name}` and include a `metadata:`
   block with `capability: {gap.capability_name}` and
   `safety_class: {gap.safety_class}`. The resident container has no shell
   binaries: for Kubernetes inspection reference the `kubernetes_inspect`
   tool rather than `kubectl`. Describe only inspection and
   evidence-gathering steps; never instruct state-changing operations.
2. `tool_code` — one self-contained Python module implementing
   `def run(signal: dict) -> dict`. It receives the raw signal payload and
   returns a JSON-serializable judgment dict with at least the keys
   `capability`, `matches` (bool), `observed` (dict of relevant fields),
   `severity`, and `summary`. The probe must be read-only: standard library
   only (allowed imports: {sorted(_READ_ONLY_ALLOWED_IMPORTS)}), no file,
   network, subprocess, or environment access.

Respond with exactly one JSON object: {{"skill_markdown": "...", "tool_code": "..."}}"""


def _parse_builder_response(content: str, *, skill_name: str) -> dict[str, str]:
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ToolBuildError(
            f"builder response for {skill_name} contained no JSON object: {content[:200]!r}"
        )
    try:
        document = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ToolBuildError(f"builder response for {skill_name} is not valid JSON: {exc}") from exc
    skill_markdown = str(document.get("skill_markdown") or "")
    tool_code = str(document.get("tool_code") or "")
    if not skill_markdown.strip():
        raise ToolBuildError(f"builder response for {skill_name} is missing skill_markdown")
    if not tool_code.strip():
        raise ToolBuildError(f"builder response for {skill_name} is missing tool_code")
    return {"skill_markdown": skill_markdown, "tool_code": tool_code}


def _validate_tool_code(tool_code: str, *, entry_point: str, safety_class: str) -> None:
    findings = tool_implementation_findings(
        tool_code,
        entry_point=entry_point,
        safety_class=safety_class,
    )
    if findings:
        raise ToolBuildError("; ".join(findings))


def tool_implementation_findings(
    tool_code: str,
    *,
    entry_point: str = "run",
    safety_class: str = "read_only",
) -> list[str]:
    """Structurally validate a tool implementation; return blocking findings."""
    try:
        tree = ast.parse(tool_code)
    except SyntaxError as exc:
        return [f"tool implementation has a syntax error: {exc}"]

    findings: list[str] = []
    entry_points = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == entry_point
    ]
    if not entry_points:
        findings.append(f"tool implementation does not define {entry_point}()")

    if safety_class == "read_only":
        blocked = sorted(_blocked_imports(tree))
        if blocked:
            findings.append(f"read-only tool imports forbidden modules: {', '.join(blocked)}")
    return findings


def _blocked_imports(tree: ast.AST) -> set[str]:
    blocked: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [(node.module or "").split(".")[0]]
        else:
            continue
        blocked.update(name for name in names if name and name not in _READ_ONLY_ALLOWED_IMPORTS)
    return blocked


def _ensure_skill_metadata(
    skill_content: str,
    *,
    skill_name: str,
    capability_name: str,
    safety_class: str,
) -> str:
    content = skill_content
    if f"capability: {capability_name}" in content and "safety_class:" in content:
        return content
    header = f"# skill: {skill_name}"
    metadata = (
        f"\nmetadata:\n"
        f"  capability: {capability_name}\n"
        f"  source: valkyrie-dream-cycle\n"
        f"  safety_class: {safety_class}\n"
    )
    if content.startswith(header):
        return content.replace(header, header + "\n" + metadata, 1)
    return f"{header}\n{metadata}\n{content}"


def _skill_name_from_capability(capability_name: str) -> str:
    safe = re.sub(r"[^a-z0-9]+", "-", capability_name.lower()).strip("-")
    return f"valkyrie-{safe}"


# Operations a self-built or peer-taught artifact may never instruct, in any
# autonomy mode. Content lint only — authority decisions live in AutonomyPolicy.
_BLOCKED_INSTRUCTIONS = (
    "rm -rf",
    "kubectl delete",
    "send email",
    "delete secret",
)


def _structural_findings(request: EvolutionRequest, build: BuildResult) -> list[str]:
    """Blocking structural findings for a built or adopted artifact."""
    from ravn.context.autonomy import unnegated_prose_mentions  # noqa: PLC0415

    findings: list[str] = []
    if build.artifact_type != "ravn_skill_tool":
        findings.append(f"unexpected artifact type: {build.artifact_type}")
    capability_marker = f"capability: {request.gap.capability_name}"
    if capability_marker not in build.skill_content:
        findings.append("missing capability marker")
    lower = build.skill_content.lower()
    # Negation-aware: "never run kubectl delete" is a disclaimer, not an
    # instruction, and must not block an otherwise safe artifact.
    for blocked in sorted(unnegated_prose_mentions(lower, _BLOCKED_INSTRUCTIONS)):
        findings.append(f"blocked operation mentioned: {blocked}")
    if unnegated_prose_mentions(lower, ("kubectl",)) and "kubernetes_inspect" not in lower:
        findings.append("unavailable runtime dependency: kubectl; use kubernetes_inspect")
    if build.has_tool_implementation:
        findings.extend(
            tool_implementation_findings(
                build.tool_code,
                entry_point=build.tool_entry_point or "run",
                safety_class=request.gap.safety_class,
            )
        )
    return findings


def _policy_mode(autonomy_mode: str) -> str:
    """Map runtime mode strings onto the shared AutonomyPolicy modes.

    Unknown modes degrade to guarded — the conservative default.
    """
    mode = autonomy_mode.lower()
    if mode in {"guarded", "autonomous", "yolo"}:
        return mode
    return "guarded"


def _risk_class(safety_class: str) -> str:
    mapping = {
        "read_only": "low",
        "low": "low",
        "mutating": "medium",
        "write": "medium",
        "medium": "medium",
        "high": "high",
        "destructive": "critical",
    }
    return mapping.get(safety_class.lower(), "medium")


def _local_install_scope(target_scope: str) -> str:
    """Scope of the local mutation a review gates.

    Installing a flock- or shared-promoted learning only changes this
    resident's own registry, so the authority decision is evaluated at
    environment scope; the lifecycle scope label is preserved elsewhere.
    """
    scope = target_scope.lower()
    if scope in {"flock", "shared"}:
        return "environment"
    if scope in {"private", "environment", "domain"}:
        return scope
    return "environment"


def _interesting_fields(evidence: dict[str, Any]) -> dict[str, Any]:
    payload = evidence.get("payload")
    if not isinstance(payload, dict):
        return {}
    return {
        key: value
        for key, value in payload.items()
        if key
        in {
            "kind",
            "reason",
            "object",
            "namespace",
            "host",
            "service",
            "metric",
            "printer",
            "material",
            "threshold",
            "observed",
            "message",
        }
        and value not in ("", None)
    }


def _render_skill(
    *,
    skill_name: str,
    capability_name: str,
    gap_reason: str,
    fields: dict[str, Any],
    safety_class: str,
) -> str:
    field_lines = "\n".join(f"- {key}: `{value}`" for key, value in sorted(fields.items()))
    if not field_lines:
        field_lines = "- No stable fields were present; inspect the raw signal payload."
    return f"""# skill: {skill_name}

Reusable resident Valkyrie capability for `{capability_name}`.

metadata:
  capability: {capability_name}
  source: valkyrie-dream-cycle
  safety_class: {safety_class}
  tool_entry_point: run

## When To Use

Use this skill when an incoming Environment signal matches capability `{capability_name}`.

## What To Inspect

{field_lines}

## Procedure

1. Run the installed probe implementation for this capability against the signal payload.
2. Gather read-only context that explains `{gap_reason}` before proposing action.
   For Kubernetes signals, use `kubernetes_inspect`; do not assume `kubectl` exists.
3. Produce a structured judgment with confidence, evidence, and escalation need.
4. If the signal is still ambiguous, declare the missing context as a new capability gap.
"""


def _render_tool_code(*, capability_name: str, fields: dict[str, Any]) -> str:
    field_names = sorted(fields) or [
        "kind",
        "reason",
        "message",
        "namespace",
        "object",
    ]
    return f'''"""Read-only probe for capability `{capability_name}` (auto-built)."""

CAPABILITY = {capability_name!r}
INTERESTING_FIELDS = {field_names!r}


def run(signal: dict) -> dict:
    payload = signal.get("payload") if isinstance(signal.get("payload"), dict) else signal
    observed = {{
        key: payload.get(key)
        for key in INTERESTING_FIELDS
        if payload.get(key) not in ("", None)
    }}
    severity = str(payload.get("severity") or signal.get("severity") or "unknown")
    matches = bool(observed)
    summary = (
        f"{{CAPABILITY}}: observed {{sorted(observed)}}"
        if matches
        else f"{{CAPABILITY}}: no interesting fields present"
    )
    return {{
        "capability": CAPABILITY,
        "matches": matches,
        "observed": observed,
        "severity": severity,
        "summary": summary,
    }}
'''
