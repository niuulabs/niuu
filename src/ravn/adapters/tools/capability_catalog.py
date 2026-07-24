"""Ravn tool for listing the portable capability catalog."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from niuu.domain.agent_directory import AgentSkill
from niuu.observability import get_observability
from ravn.domain.capability_catalog import (
    Capability,
    CapabilityKind,
    capability_from_agent_skill,
    capability_from_skill,
    capability_from_tool,
    capability_from_workflow,
    filter_capabilities,
)
from ravn.domain.models import ToolResult
from ravn.ports.agent_directory import PeerAgentDirectoryPort
from ravn.ports.capability import WorkflowCapabilityPort
from ravn.ports.skill import SkillPort
from ravn.ports.tool import ToolPort
from ravn.tool_observability import publish_learned_tool_inventory

_PERMISSION = "introspect:read"


class CapabilityListTool(ToolPort):
    """List local tools, local skills, and remote workflows as one catalog."""

    def __init__(
        self,
        *,
        tools_provider: Callable[[], Sequence[ToolPort]],
        skill_port: SkillPort | None = None,
        workflow_sources: Sequence[WorkflowCapabilityPort] | None = None,
        learned_tools_provider: Callable[[], Sequence[Any]] | None = None,
        agent_directory: PeerAgentDirectoryPort | None = None,
    ) -> None:
        self._tools_provider = tools_provider
        self._skill_port = skill_port
        self._workflow_sources = list(workflow_sources or [])
        # Returns learned-tool artifact envelopes (manifest name/description/
        # schema) read from disk — never loaded as callables here (NIU-1118).
        self._learned_tools_provider = learned_tools_provider
        self._agent_directory = agent_directory

    @property
    def name(self) -> str:
        return "capability_list"

    @property
    def description(self) -> str:
        return (
            "List available capabilities as one portable catalog: native Ravn tools, "
            "resident learned tools, resident skills, configured remote workflows, "
            "and peer Agent Card skills. Use this before building new tools so existing "
            "capabilities are not duplicated. Entries tagged 'learned' are not native "
            "tools — execute them by name with learned_tool_run. A filtered miss includes "
            "a compact catalog_preview; use it to choose a broader query or kind before "
            "concluding that no relevant capability exists."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [kind.value for kind in CapabilityKind],
                    "description": "Optional capability kind filter.",
                },
                "query": {
                    "type": "string",
                    "description": "Optional case-insensitive text filter.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional tags to match.",
                },
                "require_all_tags": {
                    "type": "boolean",
                    "description": "Require every tag instead of any tag.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum capabilities to return.",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        capabilities, errors = await self._collect()
        kind = _parse_kind(input.get("kind"))
        if input.get("kind") and kind is None:
            return ToolResult(
                tool_call_id="",
                content=f"Unsupported capability kind: {input.get('kind')}",
                is_error=True,
            )
        filtered = filter_capabilities(
            capabilities,
            kind=kind,
            tags=_string_list(input.get("tags")),
            query=str(input.get("query") or ""),
            require_all_tags=bool(input.get("require_all_tags") or False),
        )
        limit = _int_in_range(input.get("limit"), default=100, minimum=1, maximum=500)
        catalog_counts: dict[str, int] = {}
        for capability in capabilities:
            catalog_counts[capability.kind.value] = catalog_counts.get(capability.kind.value, 0) + 1
        payload = {
            "capabilities": [item.to_catalog_dict() for item in filtered[:limit]],
            "count": min(len(filtered), limit),
            "total": len(filtered),
            "catalog_total": len(capabilities),
            "catalog_counts": catalog_counts,
            "catalog_preview": (
                [
                    {key: entry[key] for key in ("id", "kind", "name", "source")}
                    for capability in capabilities[:limit]
                    for entry in [capability.to_catalog_dict()]
                ]
                if not filtered
                else []
            ),
            "errors": errors,
        }
        telemetry = get_observability()
        telemetry.set_attributes(
            {
                "ravn.capability.catalog.total": len(capabilities),
                "ravn.capability.catalog.returned": payload["count"],
                "ravn.capability.catalog.error_count": len(errors),
            }
        )
        telemetry.event(
            "ravn.capability.catalog.result",
            attributes={
                "ravn.capability.catalog.total": len(capabilities),
                "ravn.capability.catalog.returned": payload["count"],
                "ravn.capability.catalog.error_count": len(errors),
                "ravn.capability.catalog.kinds": [
                    f"{kind}:{count}" for kind, count in sorted(catalog_counts.items())
                ],
            },
            content=payload,
        )
        for kind, count in catalog_counts.items():
            telemetry.gauge(
                "ravn.capabilities.available",
                count,
                attributes={"ravn.capability.kind": kind},
                description="Capabilities visible to the resident by kind.",
            )
        return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2))

    async def _collect(self) -> tuple[list[Capability], list[dict[str, Any]]]:
        telemetry = get_observability()
        capabilities: list[Capability] = []
        errors: list[dict[str, Any]] = []
        native_names: set[str] = set()

        for tool in self._tools_provider():
            try:
                if tool.name == self.name:
                    continue
                native_names.add(tool.name)
                capabilities.append(
                    capability_from_tool(
                        name=tool.name,
                        description=tool.description,
                        input_schema=tool.input_schema,
                        required_permission=tool.required_permission,
                        tags=_tool_tags(tool),
                        metadata={"parallelisable": tool.parallelisable},
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        "kind": "tool",
                        "name": getattr(tool, "name", ""),
                        "error": str(exc),
                    }
                )
        telemetry.event(
            "ravn.capability.source",
            attributes={
                "ravn.capability.source": "native_tools",
                "ravn.capability.count": len(capabilities),
            },
        )

        if self._learned_tools_provider is not None:
            before = len(capabilities)
            try:
                learned_artifacts = list(self._learned_tools_provider())
                publish_learned_tool_inventory(learned_artifacts)
                for artifact in learned_artifacts:
                    manifest = artifact.manifest
                    verification = artifact.provenance.get("verification")
                    verification_outcome = (
                        "passed"
                        if isinstance(verification, dict) and verification.get("ok") is True
                        else "failed"
                        if isinstance(verification, dict) and verification.get("ok") is False
                        else "unknown"
                    )
                    # Skip names already exposed natively (legacy bulk mode, or
                    # a tool build_tool registered live in this session).
                    if manifest.name in native_names:
                        continue
                    capabilities.append(
                        capability_from_tool(
                            name=manifest.name,
                            description=manifest.description,
                            input_schema=manifest.input_schema,
                            required_permission=manifest.required_permission,
                            tags=["tool", "learned"],
                            metadata={
                                "invoke_via": "learned_tool_run",
                                "artifact_id": artifact.artifact_id,
                                "artifact_type": artifact.artifact_type,
                                "verification": verification_outcome,
                                "has_tests": bool(artifact.test_code),
                                "requirements_count": len(artifact.requirements),
                            },
                        )
                    )
            except Exception as exc:
                errors.append({"kind": "learned_tool", "error": str(exc)})
            telemetry.event(
                "ravn.capability.source",
                attributes={
                    "ravn.capability.source": "learned_tools",
                    "ravn.capability.count": len(capabilities) - before,
                },
            )

        if self._skill_port is not None:
            before = len(capabilities)
            try:
                for skill in await self._skill_port.list_skills():
                    capabilities.append(
                        capability_from_skill(
                            skill_id=skill.skill_id,
                            name=skill.name,
                            description=skill.description,
                            requires_tools=list(skill.requires_tools),
                            metadata={"success_count": skill.success_count},
                        )
                    )
            except Exception as exc:
                errors.append({"kind": "skill", "error": str(exc)})
            telemetry.event(
                "ravn.capability.source",
                attributes={
                    "ravn.capability.source": "skills",
                    "ravn.capability.count": len(capabilities) - before,
                },
            )

        for source_index, source in enumerate(self._workflow_sources):
            before = len(capabilities)
            try:
                for workflow in await source.list_workflows():
                    capabilities.append(
                        capability_from_workflow(
                            workflow,
                            source="workflow",
                            source_index=source_index,
                        )
                    )
            except Exception as exc:
                errors.append({"kind": "workflow", "source_index": source_index, "error": str(exc)})
            telemetry.event(
                "ravn.capability.source",
                attributes={
                    "ravn.capability.source": f"workflow:{source_index}",
                    "ravn.capability.count": len(capabilities) - before,
                },
            )

        if self._agent_directory is not None:
            before = len(capabilities)
            agent_count = 0
            try:
                page = await self._agent_directory.list_agents()
                agent_count = len(page.items)
                for agent in page.items:
                    details = list(agent.skills)
                    known_ids = {skill.id for skill in details}
                    details.extend(
                        AgentSkill(
                            id=skill_id,
                            name=skill_id,
                            description=agent.description,
                            tags=list(agent.tags),
                        )
                        for skill_id in agent.skill_ids
                        if skill_id not in known_ids
                    )
                    capabilities.extend(
                        capability_from_agent_skill(agent, skill) for skill in details
                    )
                errors.extend(
                    {
                        "kind": "agent_directory_warning",
                        "source_instance_id": warning.source_instance_id,
                        "code": warning.code,
                        "error": warning.message,
                    }
                    for warning in page.warnings
                )
            except Exception as exc:
                errors.append({"kind": "agent_skill", "error": str(exc)})
            telemetry.event(
                "ravn.capability.source",
                attributes={
                    "ravn.capability.source": "agent_directory",
                    "ravn.capability.agent_count": agent_count,
                    "ravn.capability.count": len(capabilities) - before,
                },
            )

        return capabilities, errors


def _parse_kind(value: Any) -> CapabilityKind | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return CapabilityKind(value.strip())
    except ValueError:
        return None


def _tool_tags(tool: ToolPort) -> list[str]:
    tags = ["tool"]
    permission_prefix = tool.required_permission.partition(":")[0].strip()
    if permission_prefix:
        tags.append(permission_prefix)
    return tags


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _int_in_range(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))
