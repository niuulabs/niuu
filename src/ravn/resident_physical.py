"""Resident runtime for physical-world capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ravn.domain.models import TokenUsage
from ravn.domain.physical_device import (
    PhysicalActionKind,
    PhysicalActionRequest,
    PhysicalActionResult,
    PhysicalCapability,
)
from ravn.domain.resident_continuation import (
    ContinuationDecisionKind,
    ResidentActionCandidate,
    ResidentBudgetSnapshot,
    ResidentContinuationContext,
    ResidentMemoryPort,
    ResidentPolicyDecisionRecord,
    ResidentPolicyPort,
    ResidentTurnRecord,
    RiskBoundary,
)
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ResidentDomainExpertMemoryPort,
    ResidentDomainModel,
    WorkstreamExecutionResult,
)
from ravn.ports.physical_device import PhysicalDevicePort
from ravn.resident_continuation import ConfigurableResidentPolicy
from ravn.resident_text import merge_unique as _merge_text
from ravn.resident_text import slug as _slug_text
from ravn.resident_text import timestamp_slug as _stamp

_PHYSICAL_PREFIX = "resident/physical"
_CAPABILITY_PREFIX = f"{_PHYSICAL_PREFIX}/capabilities"
_RESULT_PREFIX = f"{_PHYSICAL_PREFIX}/results"
_AUDIT_PREFIX = f"{_PHYSICAL_PREFIX}/audits"
_REASONING_PREFIX = f"{_PHYSICAL_PREFIX}/reasoning"


@dataclass(frozen=True)
class ResidentPhysicalRuntimeConfig:
    """Bounds for one resident physical integration pass."""

    proof_turn_index: int = 1
    max_reasoning_refs: int = 8


@dataclass(frozen=True)
class ResidentPhysicalReasoning:
    """Compact resident reasoning produced from physical capability results."""

    mandate: str
    summary: str
    safe_next_action: str
    evidence_refs: tuple[str, ...]
    risk_boundaries: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentPhysicalReport:
    """One resident physical capability interaction report."""

    mandate: str
    capabilities: tuple[PhysicalCapability, ...]
    results: tuple[PhysicalActionResult, ...]
    reasoning: ResidentPhysicalReasoning
    persisted_refs: tuple[str, ...]


@dataclass(frozen=True)
class ResidentPhysicalWakeReport:
    """Aggregated resident physical-world pass for one daemon wake."""

    mandate: str
    reports: tuple[ResidentPhysicalReport, ...]
    persisted_refs: tuple[str, ...]
    operator_pending: bool = False
    final_suggested_next_action: str = "No configured physical capability needed attention."


class ResidentPhysicalMemoryPort(Protocol):
    """Persistence boundary for resident physical-world artifacts."""

    async def write_capability(self, capability: PhysicalCapability) -> str:
        """Persist a physical capability description."""

    async def write_result(self, result: PhysicalActionResult) -> str:
        """Persist telemetry, dry-run result, or policy block."""

    async def write_physical_audit(self, content: str) -> str:
        """Persist an audit record."""

    async def write_reasoning(self, reasoning: ResidentPhysicalReasoning) -> str:
        """Persist resident reasoning derived from physical results."""

    async def list_refs(self, prefix: str = _PHYSICAL_PREFIX) -> list[str]:
        """List persisted physical refs."""


class LocalResidentPhysicalMemory(ResidentPhysicalMemoryPort):
    """Filesystem-backed resident physical memory using the Mimir page shape."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def write_capability(self, capability: PhysicalCapability) -> str:
        rel = Path(_CAPABILITY_PREFIX) / f"{_slug(capability.id)}.md"
        return self._write(rel, _render_capability(capability))

    async def write_result(self, result: PhysicalActionResult) -> str:
        stamp = result.created_at.strftime("%Y%m%dT%H%M%SZ")
        filename = f"{stamp}-{_slug(result.capability_id)}-{_slug(result.kind)}.md"
        rel = Path(_RESULT_PREFIX) / filename
        return self._write(rel, _render_result(result))

    async def write_physical_audit(self, content: str) -> str:
        rel = Path(_AUDIT_PREFIX) / f"{_stamp(datetime.now(UTC))}.md"
        return self._write(rel, content)

    async def write_reasoning(self, reasoning: ResidentPhysicalReasoning) -> str:
        rel = Path(_REASONING_PREFIX) / f"{_stamp(reasoning.created_at)}.md"
        return self._write(rel, _render_reasoning(reasoning))

    async def list_refs(self, prefix: str = _PHYSICAL_PREFIX) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(path.relative_to(self._root)) for path in base.rglob("*.md"))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class ResidentPhysicalRuntime:
    """Use physical device adapters through resident policy and memory gates."""

    def __init__(
        self,
        *,
        device: PhysicalDevicePort,
        memory: ResidentPhysicalMemoryPort,
        continuation_memory: ResidentMemoryPort | None = None,
        expert_memory: ResidentDomainExpertMemoryPort | None = None,
        policy: ResidentPolicyPort | None = None,
        config: ResidentPhysicalRuntimeConfig | None = None,
    ) -> None:
        self._device = device
        self._memory = memory
        self._continuation_memory = continuation_memory
        self._expert_memory = expert_memory
        self._policy = policy or ConfigurableResidentPolicy()
        self._config = config or ResidentPhysicalRuntimeConfig()

    async def discover(self, mandate: str) -> ResidentPhysicalReport:
        capabilities = tuple(await self._device.list_capabilities())
        refs = [await self._memory.write_capability(item) for item in capabilities]
        reasoning = ResidentPhysicalReasoning(
            mandate=mandate,
            summary=f"Discovered {len(capabilities)} physical capability adapter(s).",
            safe_next_action=(
                "Read telemetry or run a dry-run before requesting physical operation."
            ),
            evidence_refs=tuple(refs[: self._config.max_reasoning_refs]),
        )
        refs.append(await self._memory.write_reasoning(reasoning))
        refs.append(
            await self._memory.write_physical_audit(
                _render_audit(
                    title="Physical Capability Discovery",
                    body=f"Discovered {len(capabilities)} configured capability adapter(s).",
                    refs=tuple(refs),
                )
            )
        )
        refs.extend(
            await self._consolidate_physical_learning(
                mandate,
                capabilities=capabilities,
                results=(),
                reasoning=reasoning,
            )
        )
        return ResidentPhysicalReport(
            mandate=mandate,
            capabilities=capabilities,
            results=(),
            reasoning=reasoning,
            persisted_refs=tuple(refs),
        )

    async def read_telemetry(self, mandate: str, capability_id: str) -> ResidentPhysicalReport:
        capabilities = tuple(await self._device.list_capabilities())
        result = await self._device.read_telemetry(capability_id)
        refs = [await self._memory.write_result(result)]
        reasoning = _reason_from_results(mandate, (result,), tuple(refs))
        refs.append(await self._memory.write_reasoning(reasoning))
        refs.append(
            await self._memory.write_physical_audit(
                _render_audit(
                    title="Physical Read-Only Telemetry",
                    body=result.summary,
                    refs=tuple(refs),
                )
            )
        )
        refs.extend(
            await self._consolidate_physical_learning(
                mandate,
                capabilities=capabilities,
                results=(result,),
                reasoning=reasoning,
            )
        )
        return ResidentPhysicalReport(
            mandate=mandate,
            capabilities=capabilities,
            results=(result,),
            reasoning=reasoning,
            persisted_refs=tuple(refs),
        )

    async def dry_run(
        self,
        mandate: str,
        request: PhysicalActionRequest,
    ) -> ResidentPhysicalReport:
        capabilities = tuple(await self._device.list_capabilities())
        result = await self._device.dry_run(
            _request_with_kind(request, PhysicalActionKind.DRY_RUN.value)
        )
        refs = [await self._memory.write_result(result)]
        reasoning = _reason_from_results(mandate, (result,), tuple(refs))
        refs.append(await self._memory.write_reasoning(reasoning))
        refs.append(
            await self._memory.write_physical_audit(
                _render_audit(title="Physical Dry Run", body=result.summary, refs=tuple(refs))
            )
        )
        refs.extend(
            await self._consolidate_physical_learning(
                mandate,
                capabilities=capabilities,
                results=(result,),
                reasoning=reasoning,
            )
        )
        return ResidentPhysicalReport(
            mandate=mandate,
            capabilities=capabilities,
            results=(result,),
            reasoning=reasoning,
            persisted_refs=tuple(refs),
        )

    async def execute(
        self,
        mandate: str,
        request: PhysicalActionRequest,
    ) -> ResidentPhysicalReport:
        capabilities = tuple(await self._device.list_capabilities())
        guarded = _request_with_kind(
            request,
            PhysicalActionKind.PHYSICAL_OPERATION.value,
            extra_risks=(RiskBoundary.PHYSICAL_OPERATION.value,),
        )
        action = ResidentActionCandidate(
            title=guarded.action,
            action=guarded.reason or guarded.action,
            reason="Resident requested a physical-world operation.",
            risk_boundaries=guarded.risk_boundaries,
            required_capabilities=(guarded.capability_id,),
        )
        turn = _policy_turn(action, turn_index=self._config.proof_turn_index)
        policy_observations = (
            tuple(await self._continuation_memory.list_policy_observations())
            if self._continuation_memory is not None
            else ()
        )
        decision = await self._policy.assess(
            action,
            context=ResidentContinuationContext(
                mandate=mandate,
                turn_record=turn,
                budget=ResidentBudgetSnapshot(turns_used=self._config.proof_turn_index),
                policy_observations=policy_observations,
            ),
        )
        if not decision.allowed or decision.needs_approval:
            result = PhysicalActionResult(
                capability_id=guarded.capability_id,
                action=guarded.action,
                kind=PhysicalActionKind.PHYSICAL_OPERATION.value,
                status="blocked",
                summary=decision.question or decision.reason,
                risk_boundaries=decision.risk_boundaries or guarded.risk_boundaries,
                approval_required=True,
                blocked_reason=decision.reason,
            )
            refs = [await self._memory.write_result(result)]
            if self._continuation_memory is not None:
                refs.append(
                    await self._continuation_memory.write_operator_needed(
                        question=decision.question or "May I operate the physical device?",
                        reason=decision.reason,
                        turn=turn,
                    )
                )
                refs.append(
                    await self._continuation_memory.write_policy_decision(
                        ResidentPolicyDecisionRecord(
                            turn_index=turn.turn_index,
                            action_title=action.title,
                            action=action.action,
                            decision_kind=ContinuationDecisionKind.ASK_OPERATOR.value,
                            allowed=decision.allowed,
                            needs_approval=decision.needs_approval,
                            reason=decision.reason,
                            risk_boundaries=decision.risk_boundaries,
                            question=decision.question,
                            calibration_notes=decision.calibration_notes,
                        )
                    )
                )
            reasoning = _reason_from_results(mandate, (result,), tuple(refs))
            refs.append(await self._memory.write_reasoning(reasoning))
            refs.append(
                await self._memory.write_physical_audit(
                    _render_audit(
                        title="Physical Operation Blocked",
                        body=decision.reason,
                        refs=tuple(refs),
                    )
                )
            )
            refs.extend(
                await self._consolidate_physical_learning(
                    mandate,
                    capabilities=capabilities,
                    results=(result,),
                    reasoning=reasoning,
                )
            )
            return ResidentPhysicalReport(
                mandate=mandate,
                capabilities=capabilities,
                results=(result,),
                reasoning=reasoning,
                persisted_refs=tuple(refs),
            )

        result = await self._device.execute(guarded)
        refs = [await self._memory.write_result(result)]
        reasoning = _reason_from_results(mandate, (result,), tuple(refs))
        refs.append(await self._memory.write_reasoning(reasoning))
        refs.append(
            await self._memory.write_physical_audit(
                _render_audit(
                    title="Physical Operation Executed",
                    body=result.summary,
                    refs=tuple(refs),
                )
            )
        )
        refs.extend(
            await self._consolidate_physical_learning(
                mandate,
                capabilities=capabilities,
                results=(result,),
                reasoning=reasoning,
            )
        )
        return ResidentPhysicalReport(
            mandate=mandate,
            capabilities=capabilities,
            results=(result,),
            reasoning=reasoning,
            persisted_refs=tuple(refs),
        )

    async def _consolidate_physical_learning(
        self,
        mandate: str,
        *,
        capabilities: tuple[PhysicalCapability, ...],
        results: tuple[PhysicalActionResult, ...],
        reasoning: ResidentPhysicalReasoning,
    ) -> tuple[str, ...]:
        if self._expert_memory is None:
            return ()
        existing = await self._expert_memory.read_domain_model(mandate)
        model = existing or ResidentDomainModel(mandate=mandate)
        result = _physical_consolidation_result(capabilities, results, reasoning)
        updated = _domain_model_with_physical_learning(
            model,
            capabilities=capabilities,
            results=results,
            reasoning=reasoning,
            result=result,
        )
        model_ref = await self._expert_memory.write_domain_model(updated)
        consolidation_ref = await self._expert_memory.write_consolidation(updated, result)
        return (model_ref, consolidation_ref)


async def run_resident_physical_wake_pass(
    mandate: str,
    runtimes: tuple[ResidentPhysicalRuntime, ...],
) -> ResidentPhysicalWakeReport:
    """Run safe physical-world resident checks for configured device runtimes."""
    reports: list[ResidentPhysicalReport] = []
    refs: list[str] = []
    operator_pending = False
    for runtime in runtimes:
        discovered = await runtime.discover(mandate)
        reports.append(discovered)
        refs.extend(discovered.persisted_refs)
        operation_gate_checked = False
        for capability in discovered.capabilities:
            if capability.telemetry_supported:
                report = await runtime.read_telemetry(mandate, capability.id)
                reports.append(report)
                refs.extend(report.persisted_refs)
            if capability.dry_run_supported:
                report = await runtime.dry_run(
                    mandate,
                    PhysicalActionRequest(
                        capability_id=capability.id,
                        action=f"Run configured dry-run for {capability.name}",
                        kind=PhysicalActionKind.DRY_RUN.value,
                        reason=(
                            "Daemon resident physical wake pass uses dry-run evidence "
                            "before any physical operation."
                        ),
                        risk_boundaries=capability.risk_boundaries,
                    ),
                )
                reports.append(report)
                refs.extend(report.persisted_refs)
            if (
                not operation_gate_checked
                and PhysicalActionKind.PHYSICAL_OPERATION.value in capability.action_kinds
            ):
                report = await runtime.execute(
                    mandate,
                    PhysicalActionRequest(
                        capability_id=capability.id,
                        action=f"Request approval boundary for {capability.name}",
                        kind=PhysicalActionKind.PHYSICAL_OPERATION.value,
                        reason=(
                            "Confirm hard policy blocks real physical operation until "
                            "operator approval is present."
                        ),
                        risk_boundaries=capability.risk_boundaries,
                    ),
                )
                reports.append(report)
                refs.extend(report.persisted_refs)
                operator_pending = operator_pending or any(
                    item.approval_required for item in report.results
                )
                operation_gate_checked = True
    return ResidentPhysicalWakeReport(
        mandate=mandate,
        reports=tuple(reports),
        persisted_refs=tuple(refs),
        operator_pending=operator_pending,
        final_suggested_next_action=_physical_wake_next_action(tuple(reports)),
    )


def _physical_consolidation_result(
    capabilities: tuple[PhysicalCapability, ...],
    results: tuple[PhysicalActionResult, ...],
    reasoning: ResidentPhysicalReasoning,
) -> WorkstreamExecutionResult:
    status = "completed"
    if any(item.status == "blocked" or item.approval_required for item in results):
        status = "blocked"
    elif any(item.status != "completed" for item in results):
        status = "failed"
    facts = tuple(_physical_fact(item) for item in capabilities)
    if results:
        facts = (*facts, *tuple(_physical_result_fact(item) for item in results))
    return WorkstreamExecutionResult(
        workstream_id="resident-physical-world",
        status=status,
        summary=reasoning.summary,
        artifact_refs=reasoning.evidence_refs,
        facts=facts,
        lessons=(reasoning.safe_next_action,),
    )


def _domain_model_with_physical_learning(
    model: ResidentDomainModel,
    *,
    capabilities: tuple[PhysicalCapability, ...],
    results: tuple[PhysicalActionResult, ...],
    reasoning: ResidentPhysicalReasoning,
    result: WorkstreamExecutionResult,
) -> ResidentDomainModel:
    blocked = tuple(
        item for item in results if item.status == "blocked" or item.approval_required
    )
    failed = tuple(
        item for item in results if item.status not in {"completed", "blocked"}
    )
    open_threads = tuple(
        f"physical approval pending for {item.capability_id}: {item.summary}"
        for item in blocked
    )
    failure_notes = tuple(
        f"physical result failed for {item.capability_id}: {item.summary}"
        for item in failed
    ) + tuple(
        f"physical operation blocked for {item.capability_id}: "
        f"{item.blocked_reason or item.summary}"
        for item in blocked
    )
    artifacts = tuple(
        ExpertArtifact(
            title=f"Physical evidence for {capability.name}",
            kind="physical_evidence",
            path=ref,
            purpose=f"Physical-world evidence for capability {capability.id}.",
            summary=reasoning.summary,
        )
        for capability, ref in zip(capabilities, reasoning.evidence_refs, strict=False)
    )
    return model.with_consolidation(
        recent_outcomes=_merge_text(model.recent_outcomes, (reasoning.summary,)),
        known_facts=_merge_text(model.known_facts, result.facts),
        resident_decisions=_merge_text(
            model.resident_decisions,
            (f"physical safe next action: {reasoning.safe_next_action}",),
        ),
        failure_notes=_merge_text(model.failure_notes, failure_notes),
        open_threads=_merge_text(model.open_threads, open_threads),
        artifacts=_merge_artifacts(model.artifacts, artifacts),
    )


def _physical_fact(capability: PhysicalCapability) -> str:
    actions = ", ".join(capability.action_kinds) or "no actions"
    return f"physical capability {capability.id} supports {actions}"


def _physical_result_fact(result: PhysicalActionResult) -> str:
    return (
        f"physical {result.kind} for {result.capability_id} "
        f"{result.status}: {result.summary}"
    )


def _request_with_kind(
    request: PhysicalActionRequest,
    kind: str,
    *,
    extra_risks: tuple[str, ...] = (),
) -> PhysicalActionRequest:
    risks = tuple(dict.fromkeys((*request.risk_boundaries, *extra_risks)))
    return PhysicalActionRequest(
        capability_id=request.capability_id,
        action=request.action,
        kind=kind,
        reason=request.reason,
        parameters=dict(request.parameters),
        risk_boundaries=risks,
    )


def _policy_turn(action: ResidentActionCandidate, *, turn_index: int) -> ResidentTurnRecord:
    return ResidentTurnRecord(
        turn_index=turn_index,
        prompt="resident physical operation policy assessment",
        response=(
            "verdict: continue\n"
            f"selected_next_action: {action.action}\n"
            f"rationale: {action.reason}\n"
        ),
        outcome_fields={
            "verdict": "continue",
            "selected_next_action": action.action,
            "rationale": action.reason,
        },
        tool_names=("physical_device",),
        usage=TokenUsage(input_tokens=0, output_tokens=0),
        selected_next_action=action,
    )


def _reason_from_results(
    mandate: str,
    results: tuple[PhysicalActionResult, ...],
    refs: tuple[str, ...],
) -> ResidentPhysicalReasoning:
    blocked = [item for item in results if item.approval_required or item.status == "blocked"]
    failed = [item for item in results if item.status not in {"completed", "blocked"}]
    risks = tuple(dict.fromkeys(risk for item in results for risk in item.risk_boundaries))
    if blocked:
        safe_next = "Wait for operator approval before attempting any physical operation."
        summary = "; ".join(item.blocked_reason or item.summary for item in blocked)
    elif failed:
        safe_next = "Investigate the failed read/dry-run result before expanding automation."
        summary = "; ".join(item.summary for item in failed)
    else:
        safe_next = (
            "Use the persisted read-only/dry-run evidence to refine the next resident work item."
        )
        summary = (
            "; ".join(item.summary for item in results)
            or "Physical capability result recorded."
        )
    return ResidentPhysicalReasoning(
        mandate=mandate,
        summary=summary,
        safe_next_action=safe_next,
        evidence_refs=refs,
        risk_boundaries=risks,
    )


def _physical_wake_next_action(reports: tuple[ResidentPhysicalReport, ...]) -> str:
    blocked = [
        result
        for report in reports
        for result in report.results
        if result.approval_required or result.status == "blocked"
    ]
    if blocked:
        return "Wait for operator approval before attempting physical operation."
    results = [result for report in reports for result in report.results]
    if results:
        return "Use persisted physical telemetry and dry-run evidence in resident planning."
    if reports:
        return "Configured physical capabilities were discovered; choose read-only evidence next."
    return "No configured physical capability needed attention."


def _render_capability(capability: PhysicalCapability) -> str:
    actions = "\n".join(f"- {item}" for item in capability.action_kinds) or "- none"
    risks = "\n".join(f"- {item}" for item in capability.risk_boundaries) or "- none"
    return (
        f"# Physical Capability: {capability.name}\n\n"
        f"- id: {capability.id}\n"
        f"- telemetry_supported: {capability.telemetry_supported}\n"
        f"- dry_run_supported: {capability.dry_run_supported}\n"
        f"- description: {capability.description}\n\n"
        f"## Action Kinds\n{actions}\n\n"
        f"## Risk Boundaries\n{risks}\n"
    )


def _render_result(result: PhysicalActionResult) -> str:
    risks = "\n".join(f"- {item}" for item in result.risk_boundaries) or "- none"
    output_refs = "\n".join(f"- {item}" for item in result.output_refs) or "- none"
    telemetry = _render_mapping(result.telemetry)
    return (
        f"# Physical Result: {result.capability_id}\n\n"
        f"- action: {result.action}\n"
        f"- kind: {result.kind}\n"
        f"- status: {result.status}\n"
        f"- approval_required: {result.approval_required}\n"
        f"- blocked_reason: {result.blocked_reason}\n"
        f"- created_at: {result.created_at.isoformat()}\n\n"
        f"## Summary\n{result.summary}\n\n"
        f"## Risk Boundaries\n{risks}\n\n"
        f"## Output Refs\n{output_refs}\n\n"
        f"## Telemetry\n{telemetry}\n"
    )


def _render_reasoning(reasoning: ResidentPhysicalReasoning) -> str:
    refs = "\n".join(f"- {item}" for item in reasoning.evidence_refs) or "- none"
    risks = "\n".join(f"- {item}" for item in reasoning.risk_boundaries) or "- none"
    return (
        "# Resident Physical Reasoning\n\n"
        f"- created_at: {reasoning.created_at.isoformat()}\n"
        f"- mandate: {_one_line(reasoning.mandate)}\n\n"
        f"## Summary\n{reasoning.summary}\n\n"
        f"## Safe Next Action\n{reasoning.safe_next_action}\n\n"
        f"## Evidence Refs\n{refs}\n\n"
        f"## Risk Boundaries\n{risks}\n"
    )


def _render_audit(*, title: str, body: str, refs: tuple[str, ...]) -> str:
    rendered_refs = "\n".join(f"- {item}" for item in refs) or "- none"
    return (
        f"# {title}\n\n"
        f"- created_at: {datetime.now(UTC).isoformat()}\n\n"
        f"{body}\n\n"
        f"## Refs\n{rendered_refs}\n"
    )


def _render_mapping(value: dict[str, Any]) -> str:
    if not value:
        return "- none"
    lines: list[str] = []
    for key, item in sorted(value.items()):
        rendered = item
        if isinstance(item, list | tuple):
            rendered = ", ".join(str(entry) for entry in item)
        lines.append(f"- {key}: {_one_line(rendered)}")
    return "\n".join(lines)


def _one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


def _merge_artifacts(
    left: tuple[ExpertArtifact, ...],
    right: tuple[ExpertArtifact, ...],
) -> tuple[ExpertArtifact, ...]:
    merged: dict[str, ExpertArtifact] = {
        item.path or item.title: item for item in left if item.path or item.title
    }
    for artifact in right:
        key = artifact.path or artifact.title
        if not key:
            continue
        merged[key] = artifact
    return tuple(merged.values())


def _slug(value: str) -> str:
    return _slug_text(value, max_length=96, fallback="item")
