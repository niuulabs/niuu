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
from datetime import UTC, datetime
from pathlib import Path

from niuu.observability import get_observability
from ravn.domain.models import ToolResult
from ravn.ports.permission import PermissionPort
from ravn.ports.tool import ToolPort
from ravn.skills.management import SkillLifecycle, SkillManagementRegistry
from ravn.valkyrie_evolution.learned_tools import (
    LearnedToolError,
    LearnedToolInfrastructureError,
    LearnedToolResolver,
    learned_tool_venvs_dir,
)
from ravn.valkyrie_evolution.resident_learning import (
    DEFAULT_ROLLBACK_CONSECUTIVE_FAILURES,
    EVOLUTION_ROLLED_BACK_EVENT,
)
from ravn.valkyrie_evolution.tool_runtime import remove_tool_venv
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher

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
        rollback_consecutive_failures: int = DEFAULT_ROLLBACK_CONSECUTIVE_FAILURES,
        publisher: SleipnirPublisher | None = None,
        environment_id: str = "",
        valkyrie_id: str = "",
        tools_dir: str | Path | None = None,
        source: str = "",
    ) -> None:
        self._resolver = resolver
        self._permission = permission
        self._skill_manager = skill_manager
        self._host_tools_provider = host_tools_provider
        #: Same regression threshold ResidentEvolutionConfig.rollback_
        #: consecutive_failures expresses for the autonomous install loop
        #: (resident_learning._rollback_regressed_skill) — applied here too,
        #: because this dispatcher, not that loop, is the path every learned
        #: tool actually runs through today. A tool that keeps failing here
        #: must stop being runnable, the same as one the autonomous loop
        #: executes on a signal's behalf.
        self._rollback_consecutive_failures = rollback_consecutive_failures
        #: When wired, rollback publishes the SAME valkyrie.evolution.
        #: rolled_back event the autonomous install loop publishes (so the
        #: realm capability sync and peers learn the capability is gone) and
        #: prunes the tool's dedicated dependency venv. Without a publisher/
        #: tools_dir this dispatcher still archives the tool for real — it
        #: just cannot announce it or reclaim the venv, which callers should
        #: wire when resident evolution is enabled.
        self._publisher = publisher
        self._environment_id = environment_id
        self._valkyrie_id = valkyrie_id
        self._tools_dir = Path(tools_dir) if tools_dir else None
        self._source = source or valkyrie_id or "learned_tool_run"

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
            except LearnedToolInfrastructureError as exc:
                # The BACKEND could not run the tool at all (docker
                # unavailable, dependency provisioning failed, the runner
                # refused the call) — never the tool's own logic. This must
                # never reach record_usage/rollback below: a backend outage
                # is not evidence the tool is broken, and counting it toward
                # the regression threshold archives a healthy tool.
                span.set_attribute("ravn.learned_tool.outcome", "infrastructure_error")
                telemetry.event(
                    "ravn.learned_tool.lifecycle.infrastructure_error",
                    attributes=attributes,
                    content={"error": str(exc)},
                )
                logger.warning("learned_tool_run: %r backend error: %s", name, exc)
                return ToolResult(
                    tool_call_id="",
                    content=(
                        f"Learned tool {name!r} could not run (backend error, not a tool "
                        f"failure): {exc}"
                    ),
                    is_error=True,
                )
            except Exception as exc:
                logger.warning("learned_tool_run: %r raised: %s", name, exc)
                result = ToolResult(
                    tool_call_id="",
                    content=f"Learned tool {name!r} error: {exc}",
                    is_error=True,
                )
            outcome = "error" if result.is_error else "success"
            span.set_attribute("ravn.learned_tool.outcome", outcome)
            if self._skill_manager is None:
                return result

            lifecycle: SkillLifecycle | None = None
            try:
                lifecycle = await self._skill_manager.record_usage(
                    name,
                    success=not result.is_error,
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

            usage_attributes = {
                **attributes,
                "ravn.learned_tool.outcome": outcome,
                "ravn.skill.lifecycle.run_count": lifecycle.run_count,
                "ravn.skill.lifecycle.failure_count": lifecycle.failure_count,
                "ravn.skill.lifecycle.consecutive_failures": lifecycle.consecutive_failures,
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
            # Deliberately OUTSIDE the record_usage try/except above: a
            # LookupError raised from rollback itself (e.g. skill_manager.
            # archive() racing a concurrent removal) must never be mistaken
            # for "this tool has no managed lifecycle record".
            if (
                result.is_error
                and lifecycle.consecutive_failures >= self._rollback_consecutive_failures
            ):
                result = await self._rollback_regressed_tool(
                    name,
                    lifecycle,
                    result,
                    skill_manager=self._skill_manager,
                )
            return result

    async def _rollback_regressed_tool(
        self,
        name: str,
        lifecycle: SkillLifecycle,
        result: ToolResult,
        *,
        skill_manager: SkillManagementRegistry,
    ) -> ToolResult:
        """Archive a learned tool that just crossed the regression threshold.

        The YOLO invariant applies here too: rollback on regression is
        automatic, not a suggestion an operator has to notice and act on.
        Archiving makes the tool immediately unrunnable (the lifecycle-status
        check at the top of ``execute`` refuses an archived tool), so the
        next dispatch fails loudly and clearly instead of quietly repeating
        the same failure forever. When wired with a publisher, this emits
        the SAME ``valkyrie.evolution.rolled_back`` event the autonomous
        install loop's rollback emits, so the realm capability sync and
        peers learn the capability is gone; when wired with tools_dir, the
        tool's dedicated dependency venv is reclaimed.
        """
        await skill_manager.archive(name)
        if self._tools_dir is not None:
            remove_tool_venv(
                venvs_dir=learned_tool_venvs_dir(self._tools_dir.parent),
                tool_name=name,
            )
        if self._publisher is not None:
            await self._publisher.publish(
                SleipnirEvent(
                    event_type=EVOLUTION_ROLLED_BACK_EVENT,
                    source=self._source,
                    payload={
                        "environment_id": self._environment_id,
                        "valkyrie_id": self._valkyrie_id,
                        "skill_name": name,
                        "artifact_type": "agent_tool",
                        "command_action": "auto_rollback_regression",
                        "rationale": (
                            f"Auto-rolled-back {name!r} after "
                            f"{lifecycle.consecutive_failures} consecutive failures via "
                            "learned_tool_run"
                        ),
                    },
                    summary=f"{self._source} archived learned tool {name!r}",
                    urgency=0.45,
                    domain="infrastructure",
                    timestamp=datetime.now(UTC),
                )
            )
        get_observability().event(
            "ravn.learned_tool.lifecycle.rolled_back",
            attributes={
                "ravn.learned_tool.name": name,
                "ravn.skill.lifecycle.consecutive_failures": lifecycle.consecutive_failures,
                "ravn.skill.lifecycle.rollback_threshold": self._rollback_consecutive_failures,
            },
        )
        logger.warning(
            "learned_tool_run: %r archived after %d consecutive failures",
            name,
            lifecycle.consecutive_failures,
        )
        return ToolResult(
            tool_call_id="",
            content=(
                f"{result.content}\n\nLearned tool {name!r} was archived after "
                f"{lifecycle.consecutive_failures} consecutive failures and can no "
                "longer be run."
            ),
            is_error=True,
        )
