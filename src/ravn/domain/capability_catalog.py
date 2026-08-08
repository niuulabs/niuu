"""Portable capability catalog models.

Capabilities are descriptions of things an agent can use: native tools,
resident skills, and launchable workflows. This module is intentionally a
catalog/projection layer only. It does not decide which workflow to run for a
signal, and it does not replace the existing tool, skill, or workflow
registries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from niuu.domain.agent_directory import AgentDirectoryEntry, AgentSkill


class CapabilityKind(StrEnum):
    """Provider-neutral capability categories."""

    TOOL = "tool"
    SKILL = "skill"
    WORKFLOW = "workflow"
    AGENT_SKILL = "agent_skill"


@dataclass(frozen=True)
class Capability:
    """Canonical, runtime-neutral description of an available capability."""

    capability_id: str
    kind: CapabilityKind
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    required_permission: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""
    version: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_catalog_dict(self) -> dict[str, Any]:
        """Return the stable catalog representation for Guild/UI/API use."""
        return {
            "id": self.capability_id,
            "kind": self.kind.value,
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "required_permission": self.required_permission,
            "tags": list(self.tags),
            "source": self.source,
            "version": self.version,
            "metadata": dict(self.metadata),
        }

    def to_index_dict(self, *, description_chars: int = 200) -> dict[str, Any]:
        """Return the compact form used when *listing* many capabilities.

        A list answers "what do I already have?"; it does not need each entry's
        input schema, and carrying them is what made the answer unreadable. On
        one resident the full catalog reached 151,007 chars against a 100,000
        char tool-result cap — a third cut off mid-JSON, so the model received
        malformed output, could not find the tool it owned, and built it again.
        Schemas were 63KB of that and descriptions another 39KB.

        Fetch the full entry for a chosen capability with ``to_catalog_dict``;
        that shape is the stable Guild/UI/API contract and is unchanged.
        """
        description = self.description or ""
        clipped = description[:description_chars].rstrip()
        if len(description) > len(clipped):
            clipped += "…"
        return {
            "id": self.capability_id,
            "kind": self.kind.value,
            "name": self.name,
            "description": clipped,
            "tags": list(self.tags),
            "source": self.source,
        }

    def matches_query(self, query: str) -> bool:
        """Whether *query* occurs in this capability's name or description.

        The catalog had kind and tag filters but no way to ask "do I have
        anything that lists pods?" — so a resident that could not see its whole
        catalog had no way to narrow, and rebuilding was the only move left.
        """
        needle = query.strip().casefold()
        if not needle:
            return True
        return needle in self.name.casefold() or needle in (self.description or "").casefold()

    def to_claude_tool(self) -> dict[str, Any] | None:
        """Project native tool capabilities to Claude/Anthropic tool schema."""
        if self.kind is not CapabilityKind.TOOL:
            return None
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }

    def to_codex_action(self) -> dict[str, Any]:
        """Project the capability to a Codex action/capability descriptor."""
        return {
            "id": self.capability_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
            "parameters": dict(self.input_schema),
            "required_permission": self.required_permission,
            "tags": list(self.tags),
            "source": self.source,
            "version": self.version,
            "metadata": dict(self.metadata),
        }


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


def capability_from_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    required_permission: str,
    source: str = "ravn",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Capability:
    """Create a portable capability entry from an existing ToolPort shape."""
    return Capability(
        capability_id=f"tool:{name}",
        kind=CapabilityKind.TOOL,
        name=name,
        description=description,
        input_schema=dict(input_schema),
        required_permission=required_permission,
        tags=list(tags or []),
        source=source,
        metadata=dict(metadata or {}),
    )


def capability_from_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    requires_tools: list[str],
    source: str = "ravn",
    metadata: dict[str, Any] | None = None,
) -> Capability:
    """Create a portable capability entry from an existing Skill model."""
    return Capability(
        capability_id=f"skill:{skill_id or name}",
        kind=CapabilityKind.SKILL,
        name=name,
        description=description,
        required_permission="skill:run",
        tags=["skill", *requires_tools],
        source=source,
        metadata={"requires_tools": list(requires_tools), **dict(metadata or {})},
    )


def capability_from_workflow(
    workflow: WorkflowCapability,
    *,
    source: str = "workflow",
    source_index: int | None = None,
) -> Capability:
    """Create a portable capability entry from a discovered workflow."""
    metadata = dict(workflow.metadata)
    if source_index is not None:
        metadata["source_index"] = source_index
    return Capability(
        capability_id=f"workflow:{workflow.workflow_id}",
        kind=CapabilityKind.WORKFLOW,
        name=workflow.name,
        description=workflow.description,
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "session_name": {"type": "string"},
                "connection_id": {"type": "string"},
                "repo": {"type": "string"},
                "branch": {"type": "string"},
            },
            "required": ["prompt"],
        },
        required_permission="workflow:launch",
        tags=list(workflow.tags),
        source=source,
        version=workflow.version,
        metadata=metadata,
    )


def capability_from_agent_skill(
    agent: AgentDirectoryEntry,
    skill: AgentSkill,
) -> Capability:
    """Project one Guild-visible Agent Card skill into the shared catalog."""
    interfaces = [item.model_dump(by_alias=True) for item in agent.supported_interfaces]
    provenance = [item.model_dump(by_alias=True) for item in agent.provenance]
    input_modes = skill.input_modes or agent.default_input_modes
    output_modes = skill.output_modes or agent.default_output_modes
    security_requirements = skill.security_requirements or agent.security_requirements
    return Capability(
        capability_id=f"agent:{agent.id}:{skill.id}",
        kind=CapabilityKind.AGENT_SKILL,
        name=skill.name or skill.id,
        description=skill.description or agent.description,
        input_schema={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["prompt"],
        },
        required_permission="a2a:task",
        tags=list(dict.fromkeys(["agent-skill", agent.kind, *agent.tags, *skill.tags])),
        source="agent-card",
        version=agent.card_version,
        metadata={
            "invoke_via": "a2a_task",
            "agent_id": agent.id,
            "source_agent_id": agent.source_agent_id,
            "skill_id": skill.id,
            "card_url": agent.card_url,
            "card_hash": agent.card_hash,
            "signature_verified": agent.signature_verified,
            "interfaces": interfaces,
            "input_modes": list(input_modes),
            "output_modes": list(output_modes),
            "security_schemes": dict(agent.security_schemes),
            "security_requirements": list(security_requirements),
            "observed_status": agent.observed_status,
            "last_seen": agent.last_seen,
            "examples": list(skill.examples),
            "provenance": provenance,
        },
    )


def select_workflow(
    selector: WorkflowSelector,
    workflows: list[WorkflowCapability],
) -> WorkflowCapability | None:
    """Return the first workflow matching a configured selector."""
    if not selector.configured:
        return None
    for workflow in workflows:
        if selector.matches(workflow):
            return workflow
    return None


def filter_capabilities(
    capabilities: list[Capability],
    *,
    kind: CapabilityKind | None = None,
    tags: list[str] | None = None,
    require_all_tags: bool = False,
) -> list[Capability]:
    """Filter portable capability entries without changing routing behavior."""
    wanted_tags = {_norm(item) for item in tags or [] if _norm(item)}
    filtered: list[Capability] = []
    for capability in capabilities:
        if kind is not None and capability.kind is not kind:
            continue
        capability_tags = {_norm(item) for item in capability.tags if _norm(item)}
        if wanted_tags:
            if require_all_tags and not wanted_tags.issubset(capability_tags):
                continue
            if not require_all_tags and not (wanted_tags & capability_tags):
                continue
        filtered.append(capability)
    return filtered


def _norm(value: str) -> str:
    return str(value or "").strip().casefold()
