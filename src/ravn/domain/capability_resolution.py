"""Policy-driven capability resolution for resident Valkyries.

The resolver is deliberately pure domain logic: it receives already-discovered
local skills and remote workflows, then decides which configured capability path
matches a signal. Discovery and invocation stay behind adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ravn.valkyrie_evolution.models import OperationalSignal

CapabilityDecision = Literal[
    "handle_locally",
    "invoke_workflow",
    "build_missing_capability",
    "decline",
]


@dataclass(frozen=True)
class WorkflowCapability:
    """Workflow entry discovered from an existing workflow catalog."""

    workflow_id: str
    name: str
    description: str = ""
    version: str = ""
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowSelector:
    """Configured selector for remote workflow capabilities."""

    names: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    require_all_tags: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.names or self.tags)

    def matches(self, workflow: WorkflowCapability) -> bool:
        names = {_norm(item) for item in self.names if _norm(item)}
        if names and _norm(workflow.name) not in names and _norm(workflow.workflow_id) not in names:
            return False

        tags = {_norm(item) for item in self.tags if _norm(item)}
        if not tags:
            return True

        workflow_tags = {_norm(item) for item in workflow.tags if _norm(item)}
        if self.require_all_tags:
            return tags.issubset(workflow_tags)
        return bool(tags & workflow_tags)


@dataclass(frozen=True)
class BuildMissingCapabilityPolicy:
    """Fallback path when no local or direct workflow capability exists."""

    enabled: bool = False
    workflow: WorkflowSelector = field(default_factory=WorkflowSelector)
    requires_approval: bool = True


@dataclass(frozen=True)
class CapabilityPolicy:
    """One configurable signal-to-capability policy."""

    name: str
    signal_types: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    local_skills: list[str] = field(default_factory=list)
    local_tools: list[str] = field(default_factory=list)
    remote_workflows: WorkflowSelector = field(default_factory=WorkflowSelector)
    build_missing_capability: BuildMissingCapabilityPolicy = field(
        default_factory=BuildMissingCapabilityPolicy
    )

    def matches(self, signal: OperationalSignal) -> bool:
        if self.signal_types and not _value_matches(signal.event_type, self.signal_types):
            return False
        if self.severities and not _value_matches(signal.severity, self.severities):
            return False

        source_id = str(signal.payload.get("source_id") or signal.payload.get("sourceId") or "")
        if self.source_ids and not _value_matches(source_id, self.source_ids):
            return False
        return True


@dataclass(frozen=True)
class CapabilityResolution:
    """Result of resolving a signal through capability policies."""

    decision: CapabilityDecision
    capability_name: str
    policy_name: str = ""
    workflow: WorkflowCapability | None = None
    local_skill: str = ""
    local_tools: list[str] = field(default_factory=list)
    requires_approval: bool = False
    reason: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)


class CapabilityResolver:
    """Resolve a signal against configured local and remote capabilities."""

    def __init__(self, policies: list[CapabilityPolicy]) -> None:
        self._policies = [policy for policy in policies if policy.name]

    def resolve(
        self,
        signal: OperationalSignal,
        *,
        local_skill_names: list[str] | None = None,
        workflows: list[WorkflowCapability] | None = None,
    ) -> CapabilityResolution:
        capability_name = derive_capability_name(signal)
        local_skills = {_norm(name) for name in (local_skill_names or []) if _norm(name)}
        available_workflows = list(workflows or [])

        for policy in self._policies:
            if not policy.matches(signal):
                continue

            skill = _first_available(policy.local_skills, local_skills)
            if skill:
                return CapabilityResolution(
                    decision="handle_locally",
                    capability_name=capability_name,
                    policy_name=policy.name,
                    local_skill=skill,
                    local_tools=list(policy.local_tools),
                    reason=f"policy {policy.name} matched local skill {skill}",
                    provenance=_provenance(signal, policy),
                )

            workflow = _select_workflow(policy.remote_workflows, available_workflows)
            if workflow is not None:
                return CapabilityResolution(
                    decision="invoke_workflow",
                    capability_name=capability_name,
                    policy_name=policy.name,
                    workflow=workflow,
                    local_tools=list(policy.local_tools),
                    reason=f"policy {policy.name} selected workflow {workflow.name}",
                    provenance=_provenance(signal, policy),
                )

            build_policy = policy.build_missing_capability
            build_workflow = _select_workflow(build_policy.workflow, available_workflows)
            if build_policy.enabled and build_workflow is not None:
                return CapabilityResolution(
                    decision="build_missing_capability",
                    capability_name=capability_name,
                    policy_name=policy.name,
                    workflow=build_workflow,
                    local_tools=list(policy.local_tools),
                    requires_approval=build_policy.requires_approval,
                    reason=f"policy {policy.name} selected builder workflow {build_workflow.name}",
                    provenance=_provenance(signal, policy),
                )

            return CapabilityResolution(
                decision="decline",
                capability_name=capability_name,
                policy_name=policy.name,
                reason=f"policy {policy.name} matched but no allowed capability is available",
                provenance=_provenance(signal, policy),
            )

        return CapabilityResolution(
            decision="decline",
            capability_name=capability_name,
            reason="no capability policy matched the signal",
            provenance=_provenance(signal, None),
        )


def derive_capability_name(signal: OperationalSignal) -> str:
    """Derive the stable capability name already used by resident learning."""
    namespace = signal.event_type.removeprefix("signal.").removesuffix(".event")
    reason = str(signal.payload.get("reason") or signal.payload.get("kind") or "unknown")
    kind = str(signal.payload.get("kind") or signal.payload.get("signal_kind") or namespace)
    return f"{namespace}.{kind}.{reason}".lower().replace(" ", "_")


def _select_workflow(
    selector: WorkflowSelector,
    workflows: list[WorkflowCapability],
) -> WorkflowCapability | None:
    if not selector.configured:
        return None
    for workflow in workflows:
        if selector.matches(workflow):
            return workflow
    return None


def _first_available(configured: list[str], available: set[str]) -> str:
    for name in configured:
        normalized = _norm(name)
        if normalized and normalized in available:
            return name
    return ""


def _value_matches(value: str, patterns: list[str]) -> bool:
    normalized = _norm(value)
    for pattern in patterns:
        candidate = _norm(pattern)
        if candidate == "*" or candidate == normalized:
            return True
    return False


def _provenance(signal: OperationalSignal, policy: CapabilityPolicy | None) -> dict[str, Any]:
    return {
        "signal_id": signal.signal_id,
        "signal_type": signal.event_type,
        "severity": signal.severity,
        "policy": policy.name if policy is not None else "",
    }


def _norm(value: str) -> str:
    return str(value or "").strip().lower()
