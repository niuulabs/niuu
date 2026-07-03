"""Review adapter for Valkyrie tool evolution.

:class:`PolicyCourtReviewer` is the single gate built and adopted artifacts
pass through before install.  The install/approve decision delegates to the
shared :class:`~ravn.context.autonomy.AutonomyPolicy` — the same authority
model that gates every other resident self-improvement — and the artifact is
validated structurally (AST import/entry-point analysis for tool
implementations, declared metadata for skills) instead of by prose pattern
matching.

The structural tool-code validator lives with the learned-tool domain
(:func:`ravn.valkyrie_evolution.learned_tools.tool_implementation_findings`);
this adapter and the ``build_tool`` install path share it.
"""

from __future__ import annotations

from typing import Any

from ravn.valkyrie_evolution.learned_tools import tool_implementation_findings
from ravn.valkyrie_evolution.models import BuildResult, EvolutionRequest, ReviewResult
from ravn.valkyrie_evolution.ports import EvolutionReviewPort

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
    from ravn.valkyrie_evolution.models import LearnedToolManifest  # noqa: PLC0415

    findings: list[str] = []
    if build.artifact_type == "agent_tool":
        try:
            manifest = LearnedToolManifest.from_dict(
                dict(build.evidence.get("learned_tool_manifest") or {})
            )
        except Exception as exc:  # noqa: BLE001
            findings.append(f"invalid learned tool manifest: {exc}")
            manifest = None
        if manifest is not None:
            if not manifest.name:
                findings.append("learned tool manifest is missing name")
            if not manifest.required_permission:
                findings.append("learned tool manifest is missing required_permission")
            if not manifest.input_schema:
                findings.append("learned tool manifest is missing input_schema")
        if build.has_tool_implementation:
            # Agent tools gate capability through declared_reach (and the
            # mutating-access boundaries), not a read-only Python-import
            # allowlist: a tool that shells out to acquire live evidence is
            # exactly what we want. Validate structure only — the same
            # "declared_reach" contract the install path enforces.
            findings.extend(
                tool_implementation_findings(
                    build.tool_code,
                    entry_point=build.tool_entry_point or "run",
                )
            )
        else:
            findings.append("learned tool is missing implementation")
        return findings

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
            risk_boundaries=list(request.risk_boundaries),
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
