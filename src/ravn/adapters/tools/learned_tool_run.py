"""On-demand dispatcher for resident-authored learned tools.

Learned tools are no longer bulk-loaded as native callable tools on every
turn — with dozens of accumulated tools that made every LLM request's tool
schema grow without bound (NIU-1118). Instead they follow the same
retrieval-on-demand model as markdown skills: ``capability_list`` enumerates
them from the artifact catalog, and this single ``learned_tool_run`` tool
loads and executes one by name when the agent actually needs it.

Permission model
----------------
The dispatch itself requires ``tool:run``. Before executing, the resolved
tool's own manifest ``required_permission`` is checked through the injected
permission port — exactly the check the agent loop applied when learned
tools were native tools, so dispatch does not widen what a session may run.
"""

from __future__ import annotations

import logging

from ravn.domain.models import ToolResult
from ravn.ports.permission import PermissionPort
from ravn.ports.tool import ToolPort
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import LearnedToolError, LearnedToolResolver

logger = logging.getLogger(__name__)

_PERMISSION = "tool:run"


class LearnedToolRunTool(ToolPort):
    """Load and execute a persisted learned tool by manifest name."""

    def __init__(
        self,
        *,
        resolver: LearnedToolResolver,
        permission: PermissionPort,
        skill_manager: SkillManagementRegistry | None = None,
    ) -> None:
        self._resolver = resolver
        self._permission = permission
        self._skill_manager = skill_manager

    @property
    def name(self) -> str:
        return "learned_tool_run"

    @property
    def description(self) -> str:
        return (
            "Run one of this resident's learned tools by name. Learned tools "
            "(built with build_tool or adopted from the flock) are not preloaded "
            "as native tools — discover them with capability_list (kind='tool', "
            "tag 'learned'), then execute here with the tool's name and an input "
            "object matching its input_schema."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Learned tool name as shown by capability_list.",
                },
                "input": {
                    "type": "object",
                    "description": "Input payload matching the tool's input_schema.",
                },
            },
            "required": ["name"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        name = str(input.get("name") or "").strip()
        if not name:
            return ToolResult(
                tool_call_id="",
                content="Error: name must not be empty.",
                is_error=True,
            )
        payload = input.get("input")
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return ToolResult(
                tool_call_id="",
                content="Error: input must be an object matching the tool's input_schema.",
                is_error=True,
            )

        if self._skill_manager is not None and self._skill_manager.status(name) == "archived":
            return ToolResult(
                tool_call_id="",
                content=f"Learned tool {name!r} is archived and cannot be run.",
                is_error=True,
            )

        try:
            tool = self._resolver.load(name)
        except LearnedToolError as exc:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"{exc} — use capability_list (kind='tool', tag 'learned') to "
                    "discover installed learned tools."
                ),
                is_error=True,
            )

        granted = await self._permission.check(tool.required_permission)
        if not granted:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Permission {tool.required_permission!r} denied for learned tool {name!r}."
                ),
                is_error=True,
            )

        try:
            result = await tool.execute(payload)
        except Exception as exc:
            logger.warning("learned_tool_run: %r raised: %s", name, exc)
            result = ToolResult(
                tool_call_id="",
                content=f"Learned tool {name!r} error: {exc}",
                is_error=True,
            )
        if self._skill_manager is not None:
            try:
                await self._skill_manager.record_usage(name, success=not result.is_error)
            except LookupError:
                logger.warning(
                    "learned_tool_run: %r has no managed lifecycle record",
                    name,
                )
        return result
