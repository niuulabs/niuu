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

import json
import logging
from collections.abc import Awaitable, Callable, Sequence

from niuu.observability import get_observability
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
        host_tools_provider: Callable[[], Sequence[ToolPort]] | None = None,
    ) -> None:
        self._resolver = resolver
        self._permission = permission
        self._skill_manager = skill_manager
        self._host_tools_provider = host_tools_provider

    def _host_call(self, learned_tool_name: str) -> Callable[[str, dict], Awaitable[object]]:
        """Let a learned tool ask this resident to run one of its own tools.

        The sandbox reaches back through here and nowhere else, so this is the
        whole boundary. A learned tool may only reach tools the resident itself
        holds, never another learned tool — chaining them would make one
        sandbox escape reachable from any other — and each call is checked
        against the same permission port a model-issued call goes through.
        """

        async def call(name: str, arguments: dict) -> object:
            provider = self._host_tools_provider
            if provider is None:
                raise RuntimeError("this resident exposes no tools to learned tools")
            available = {
                tool.name: tool
                for tool in provider()
                if tool.name not in {"learned_tool_run", self.name}
            }
            tool = available.get(name)
            if tool is None:
                raise PermissionError(
                    f"{name!r} is not a tool this resident exposes to learned tools; "
                    f"available: {', '.join(sorted(available)) or 'none'}"
                )
            if not self._permission.allows(tool.required_permission):
                raise PermissionError(
                    f"{name!r} requires permission {tool.required_permission!r}, "
                    f"which this resident does not hold"
                )
            get_observability().count(
                "ravn.learned_tool.host_calls",
                attributes={
                    "ravn.learned_tool.name": learned_tool_name,
                    "gen_ai.tool.name": name,
                },
                description="Host tools invoked from inside a learned tool sandbox.",
            )
            result = await tool.execute(dict(arguments))
            if getattr(result, "is_error", False):
                raise RuntimeError(str(getattr(result, "content", "")) or f"{name} failed")
            content = getattr(result, "content", "")
            try:
                return json.loads(content)
            except (TypeError, ValueError):
                return content

        return call

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

        telemetry = get_observability()
        lifecycle_status = (
            self._skill_manager.status(name) if self._skill_manager is not None else None
        )
        attributes = {
            "ravn.learned_tool.name": name,
            "ravn.skill.lifecycle.status": lifecycle_status or "unmanaged",
        }
        with telemetry.span("ravn.learned_tool.lifecycle.run", attributes=attributes) as span:
            if lifecycle_status == "archived":
                span.set_attribute("ravn.learned_tool.outcome", "archived")
                return ToolResult(
                    tool_call_id="",
                    content=f"Learned tool {name!r} is archived and cannot be run.",
                    is_error=True,
                )

            try:
                tool = self._resolver.load(name, host_call=self._host_call(name))
            except LearnedToolError as exc:
                span.set_attribute("ravn.learned_tool.outcome", "unavailable")
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
                span.set_attribute("ravn.learned_tool.outcome", "permission_denied")
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
            outcome = "error" if result.is_error else "success"
            span.set_attribute("ravn.learned_tool.outcome", outcome)
            if self._skill_manager is not None:
                try:
                    lifecycle = await self._skill_manager.record_usage(
                        name,
                        success=not result.is_error,
                    )
                    usage_attributes = {
                        **attributes,
                        "ravn.learned_tool.outcome": outcome,
                        "ravn.skill.lifecycle.run_count": lifecycle.run_count,
                        "ravn.skill.lifecycle.failure_count": lifecycle.failure_count,
                        "ravn.skill.lifecycle.consecutive_failures": (
                            lifecycle.consecutive_failures
                        ),
                    }
                    telemetry.event(
                        "ravn.learned_tool.lifecycle.usage_recorded",
                        attributes=usage_attributes,
                    )
                    telemetry.count(
                        "ravn.learned_tool.lifecycle.runs",
                        attributes={
                            "ravn.learned_tool.name": name,
                            "ravn.learned_tool.outcome": outcome,
                        },
                    )
                except LookupError:
                    telemetry.event(
                        "ravn.learned_tool.lifecycle.unmanaged",
                        attributes=attributes,
                    )
                    logger.warning(
                        "learned_tool_run: %r has no managed lifecycle record",
                        name,
                    )
            return result
