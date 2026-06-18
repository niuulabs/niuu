"""Ravn tools for config-backed workflow capability catalogs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ravn.domain.capability_catalog import (
    WorkflowCapability,
    WorkflowSelector,
    capability_from_workflow,
)
from ravn.domain.models import ToolResult
from ravn.ports.capability import (
    WorkflowCapabilityPort,
    WorkflowLaunchRequest,
    WorkflowLaunchResult,
)
from ravn.ports.tool import ToolPort

_PERMISSION_READ = "workflow:read"
_PERMISSION_LAUNCH = "workflow:launch"


class _WorkflowToolBase(ToolPort):
    def __init__(self, sources: Sequence[WorkflowCapabilityPort]) -> None:
        self._sources = list(sources)

    async def _list_all(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        workflows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for index, source in enumerate(self._sources):
            try:
                for workflow in await source.list_workflows():
                    workflows.append(_workflow_payload(workflow, source_index=index))
            except Exception as exc:
                errors.append({"source_index": index, "error": str(exc)})
        return workflows, errors

    def _no_sources(self) -> ToolResult | None:
        if self._sources:
            return None
        return ToolResult(
            tool_call_id="",
            content="No workflow capability sources are configured.",
            is_error=True,
        )


class WorkflowListTool(_WorkflowToolBase):
    """List workflows discovered through configured capability sources."""

    @property
    def name(self) -> str:
        return "workflow_list"

    @property
    def description(self) -> str:
        return (
            "List launchable workflows from configured capability sources. "
            "Use this to discover build, research, and other Volundr-backed workflows."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional case-insensitive text filter over id, name, and description."
                    ),
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional workflow tags to match.",
                },
                "require_all_tags": {
                    "type": "boolean",
                    "description": "Require every tag instead of any tag.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum workflows to return.",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        if no_sources := self._no_sources():
            return no_sources

        workflows, errors = await self._list_all()
        filtered = _filter_workflows(
            workflows,
            query=str(input.get("query") or ""),
            tags=_string_list(input.get("tags")),
            require_all_tags=bool(input.get("require_all_tags") or False),
        )
        limit = _int_in_range(input.get("limit"), default=50, minimum=1, maximum=200)
        payload = {
            "workflows": filtered[:limit],
            "count": min(len(filtered), limit),
            "total": len(filtered),
            "errors": errors,
        }
        return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2))


class WorkflowDescribeTool(_WorkflowToolBase):
    """Describe one workflow from configured capability sources."""

    @property
    def name(self) -> str:
        return "workflow_describe"

    @property
    def description(self) -> str:
        return "Describe one launchable workflow by workflow_id or exact workflow name."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id to describe."},
                "name": {"type": "string", "description": "Exact workflow name to describe."},
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        if no_sources := self._no_sources():
            return no_sources

        workflow_id = str(input.get("workflow_id") or "").strip()
        name = str(input.get("name") or "").strip()
        if not workflow_id and not name:
            return ToolResult(
                tool_call_id="",
                content="workflow_id or name is required",
                is_error=True,
            )

        workflows, errors = await self._list_all()
        match = _find_workflow_payload(workflows, workflow_id=workflow_id, name=name)
        if match is None:
            payload = {"workflow": None, "errors": errors}
            return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2), is_error=True)
        payload = {"workflow": match, "errors": errors}
        return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2))


class WorkflowLaunchTool(_WorkflowToolBase):
    """Launch a workflow through a configured capability source."""

    @property
    def name(self) -> str:
        return "workflow_launch"

    @property
    def description(self) -> str:
        return (
            "Launch a configured workflow with a prompt. "
            "Call workflow_list first when you need to discover available workflows."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow id to launch."},
                "prompt": {"type": "string", "description": "Task prompt for the workflow run."},
                "session_name": {
                    "type": "string",
                    "description": "Optional human-readable session title.",
                },
                "connection_id": {
                    "type": "string",
                    "description": "Optional target Volundr connection id.",
                },
                "repo": {"type": "string", "description": "Optional repository URL/path."},
                "branch": {"type": "string", "description": "Optional branch name."},
                "source_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": (
                        "Capability source index from workflow_list; defaults to discovered match."
                    ),
                },
                "provenance": {
                    "type": "object",
                    "description": (
                        "Optional non-secret provenance metadata for the launched workflow."
                    ),
                },
            },
            "required": ["workflow_id", "prompt"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_LAUNCH

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        if no_sources := self._no_sources():
            return no_sources

        workflow_id = str(input.get("workflow_id") or "").strip()
        prompt = str(input.get("prompt") or "").strip()
        if not workflow_id:
            return ToolResult(tool_call_id="", content="workflow_id is required", is_error=True)
        if not prompt:
            return ToolResult(tool_call_id="", content="prompt is required", is_error=True)

        source_indexes = await self._candidate_source_indexes(input, workflow_id)
        if not source_indexes:
            return ToolResult(
                tool_call_id="",
                content=f"No configured workflow source has workflow_id {workflow_id!r}",
                is_error=True,
            )

        provenance = input.get("provenance")
        if not isinstance(provenance, dict):
            provenance = {}
        request = WorkflowLaunchRequest(
            workflow_id=workflow_id,
            prompt=prompt,
            session_name=str(input.get("session_name") or "").strip(),
            repo=str(input.get("repo") or "").strip(),
            branch=str(input.get("branch") or "").strip(),
            connection_id=str(input.get("connection_id") or "").strip(),
            provenance={**provenance, "launched_by": "workflow_launch"},
        )

        errors: list[dict[str, Any]] = []
        for index in source_indexes:
            try:
                result = await self._sources[index].launch_workflow(request)
                payload = {"launch": _launch_payload(result), "source_index": index}
                return ToolResult(tool_call_id="", content=json.dumps(payload, indent=2))
            except Exception as exc:
                errors.append({"source_index": index, "error": str(exc)})

        return ToolResult(
            tool_call_id="",
            content=json.dumps({"launch": None, "errors": errors}, indent=2),
            is_error=True,
        )

    async def _candidate_source_indexes(self, input: dict, workflow_id: str) -> list[int]:
        source_index = input.get("source_index")
        if isinstance(source_index, int) and 0 <= source_index < len(self._sources):
            return [source_index]

        workflows, _errors = await self._list_all()
        matching = [
            int(item["source_index"])
            for item in workflows
            if str(item.get("id") or "") == workflow_id
        ]
        return matching or list(range(len(self._sources)))


def _workflow_payload(workflow: WorkflowCapability, *, source_index: int) -> dict[str, Any]:
    capability = capability_from_workflow(workflow, source="workflow", source_index=source_index)
    return {
        "id": workflow.workflow_id,
        "name": workflow.name,
        "description": workflow.description,
        "version": workflow.version,
        "tags": list(workflow.tags),
        "metadata": dict(workflow.metadata),
        "source_index": source_index,
        "capability": capability.to_catalog_dict(),
    }


def _launch_payload(result: WorkflowLaunchResult) -> dict[str, Any]:
    return {
        "workflow_id": result.workflow_id,
        "workflow_name": result.workflow_name,
        "session_id": result.session_id,
        "session_name": result.session_name,
        "status": result.status,
        "slug": result.slug,
        "cluster_name": result.cluster_name,
        "owner_id": result.owner_id,
        "tenant_id": result.tenant_id,
        "workload_subject": result.workload_subject,
        "workload_name": result.workload_name,
        "raw": dict(result.raw),
    }


def _filter_workflows(
    workflows: list[dict[str, Any]],
    *,
    query: str,
    tags: list[str],
    require_all_tags: bool,
) -> list[dict[str, Any]]:
    selector = WorkflowSelector(tags=tags, require_all_tags=require_all_tags)
    normalized_query = query.strip().casefold()
    filtered: list[dict[str, Any]] = []
    for item in workflows:
        workflow = WorkflowCapability(
            workflow_id=str(item.get("id") or ""),
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            version=str(item.get("version") or ""),
            tags=_string_list(item.get("tags")),
            metadata=dict(item.get("metadata") or {}),
        )
        if tags and not selector.matches(workflow):
            continue
        if normalized_query:
            haystack = " ".join(
                [
                    workflow.workflow_id,
                    workflow.name,
                    workflow.description,
                    " ".join(workflow.tags),
                ]
            ).casefold()
            if normalized_query not in haystack:
                continue
        filtered.append(item)
    return filtered


def _find_workflow_payload(
    workflows: list[dict[str, Any]],
    *,
    workflow_id: str,
    name: str,
) -> dict[str, Any] | None:
    normalized_name = name.casefold()
    for item in workflows:
        if workflow_id and str(item.get("id") or "") == workflow_id:
            return item
        if normalized_name and str(item.get("name") or "").casefold() == normalized_name:
            return item
    return None


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
