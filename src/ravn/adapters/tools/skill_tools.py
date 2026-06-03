"""Skill tools — let the agent discover and execute user-defined skills.

Two tools are provided:

* ``skill_list`` — list all available skills with name and description.
* ``skill_run``  — load a skill's instruction content and return it, causing
  the agent to follow those instructions for the current task.

Permission model
----------------
Both tools use ``skill:read`` as their required permission.  This string
contains no write/delete/execute markers so it is granted in every standard
mode (read_only, workspace_write, full_access).  Skills themselves may
instruct the agent to perform actions that require additional permissions, but
the *loading* of a skill is always a read-only operation.
"""

from __future__ import annotations

import logging

from ravn.domain.models import ToolResult
from ravn.ports.skill import SkillPort
from ravn.ports.tool import ToolPort
from ravn.skills.management import SkillManagementRegistry

logger = logging.getLogger(__name__)

_SKILL_PERMISSION = "skill:read"
_SKILL_MANAGE_PERMISSION = "skill:manage"


# ---------------------------------------------------------------------------
# skill_list
# ---------------------------------------------------------------------------


class SkillListTool(ToolPort):
    """List all available skills with their name and description.

    Skills are discovered from:
    1. ``.ravn/skills/`` in the current project directory (project-local).
    2. ``~/.ravn/skills/`` (user-global).
    3. Built-in skills shipped with Ravn.

    Use this tool to discover what skills are available before running one
    with ``skill_run``.
    """

    def __init__(self, skill_port: SkillPort) -> None:
        self._skill_port = skill_port

    @property
    def name(self) -> str:
        return "skill_list"

    @property
    def description(self) -> str:
        return (
            "List all available skills with their name and first-line description. "
            "Skills are reusable instruction sequences stored as Markdown files in "
            ".ravn/skills/ (project), ~/.ravn/skills/ (user), or built in to Ravn. "
            "Use this to discover skills before running one with skill_run."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Optional filter string. Only skills whose name, description, "
                        "or content contains this text (case-insensitive) are returned."
                    ),
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _SKILL_PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        query: str | None = input.get("query") or None

        try:
            skills = await self._skill_port.list_skills(query=query)
        except Exception as exc:
            logger.warning("skill_list: list_skills failed: %s", exc)
            return ToolResult(
                tool_call_id="",
                content=f"Failed to list skills: {exc}",
                is_error=True,
            )

        if not skills:
            if query:
                return ToolResult(
                    tool_call_id="",
                    content=f"No skills found matching {query!r}.",
                )
            return ToolResult(
                tool_call_id="",
                content=(
                    "No skills available. Create a skill by adding a .md file to .ravn/skills/."
                ),
            )

        lines = [f"Available skills ({len(skills)}):", ""]
        for skill in skills:
            lines.append(f"  {skill.name:<24}  {skill.description}")

        if query:
            lines.insert(0, f"Filtered by: {query!r}\n")

        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# skill_run
# ---------------------------------------------------------------------------


_ARGS_SEPARATOR = "\n\n---\n\n"


class SkillRunTool(ToolPort):
    """Load a skill and return its instruction content for execution.

    The returned content is the full Markdown body of the skill.  The agent
    reads this and uses it as the instruction set for the current task.
    Optionally pass *args* to provide additional context or parameters that
    are appended to the skill instructions.
    """

    def __init__(self, skill_port: SkillPort) -> None:
        self._skill_port = skill_port

    @property
    def name(self) -> str:
        return "skill_run"

    @property
    def description(self) -> str:
        return (
            "Load a named skill and return its instruction content. "
            "The skill content becomes your instruction set for the current task — "
            "read it and follow the steps exactly. "
            "Use skill_list first to discover available skill names."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to load (as shown by skill_list).",
                },
                "args": {
                    "type": "string",
                    "description": (
                        "Optional additional context or parameters to append to the "
                        "skill instructions (e.g. a specific file path or test name)."
                    ),
                },
            },
            "required": ["skill_name"],
        }

    @property
    def required_permission(self) -> str:
        return _SKILL_PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        skill_name = (input.get("skill_name") or "").strip()
        if not skill_name:
            return ToolResult(
                tool_call_id="",
                content="Error: skill_name must not be empty.",
                is_error=True,
            )

        try:
            skill = await self._skill_port.get_skill(skill_name)
        except Exception as exc:
            logger.warning("skill_run: get_skill(%r) failed: %s", skill_name, exc)
            return ToolResult(
                tool_call_id="",
                content=f"Failed to load skill {skill_name!r}: {exc}",
                is_error=True,
            )

        if skill is None:
            return ToolResult(
                tool_call_id="",
                content=(
                    f"Skill {skill_name!r} not found. Use skill_list to see available skills."
                ),
                is_error=True,
            )

        content = skill.content.strip()
        args = (input.get("args") or "").strip()
        if args:
            content = content + _ARGS_SEPARATOR + args

        header = f"## Skill: {skill.name}\n\n"
        return ToolResult(tool_call_id="", content=header + content)


class SkillManageTool(ToolPort):
    """Create, update, archive, promote, pin, validate, and inspect managed skills."""

    def __init__(
        self,
        skill_port: SkillPort,
        *,
        manager: SkillManagementRegistry | None = None,
    ) -> None:
        self._manager = manager or SkillManagementRegistry(skill_port)

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "Write-capable skill management for resident Valkyries. Supports "
            "create, update, archive, restore, pin, unpin, promote, show, list, "
            "validate, and telemetry actions with Environment/Flock scope metadata."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "create",
                        "update",
                        "patch",
                        "archive",
                        "delete",
                        "restore",
                        "pin",
                        "unpin",
                        "promote",
                        "show",
                        "list",
                        "validate",
                        "telemetry",
                    ],
                },
                "name": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "scope": {"type": "string"},
                "environment_id": {"type": "string"},
                "domain": {"type": "string"},
                "source": {"type": "string"},
                "action_safety_class": {"type": "string"},
                "success": {"type": "boolean"},
                "include_archived": {"type": "boolean"},
                "query": {"type": "string"},
            },
            "required": ["action"],
        }

    @property
    def required_permission(self) -> str:
        return _SKILL_MANAGE_PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        action = (input.get("action") or "").strip()
        try:
            match action:
                case "create":
                    skill = await self._manager.create(
                        name=(input.get("name") or "").strip(),
                        content=input.get("content") or "",
                        description=input.get("description") or "",
                        scope=input.get("scope") or "private",
                        environment_id=input.get("environment_id") or "",
                        domain=input.get("domain") or "",
                        source=input.get("source") or "manual",
                        action_safety_class=input.get("action_safety_class") or "read_only",
                    )
                    return _json_result({"status": "created", "skill": skill.name})
                case "update" | "patch":
                    skill = await self._manager.update(
                        name=(input.get("name") or "").strip(),
                        content=input.get("content"),
                        description=input.get("description"),
                    )
                    return _json_result({"status": "updated", "skill": skill.name})
                case "archive" | "delete":
                    meta = await self._manager.archive((input.get("name") or "").strip())
                    return _json_result({"status": "archived", "metadata": meta})
                case "restore":
                    meta = await self._manager.restore((input.get("name") or "").strip())
                    return _json_result({"status": "restored", "metadata": meta})
                case "pin" | "unpin":
                    meta = await self._manager.pin(
                        (input.get("name") or "").strip(),
                        pinned=action == "pin",
                    )
                    return _json_result({"status": action, "metadata": meta})
                case "promote":
                    meta = await self._manager.promote(
                        (input.get("name") or "").strip(),
                        scope=input.get("scope") or "environment",
                        environment_id=input.get("environment_id") or "",
                        domain=input.get("domain") or "",
                    )
                    return _json_result({"status": "promoted", "metadata": meta})
                case "show":
                    data = await self._manager.show(
                        (input.get("name") or "").strip(),
                        include_archived=bool(input.get("include_archived")),
                    )
                    return _json_result(data)
                case "list":
                    rows = await self._manager.list_skills(
                        input.get("query") or None,
                        include_archived=bool(input.get("include_archived")),
                    )
                    return _json_result({"skills": rows})
                case "validate":
                    SkillManagementRegistry._validate_name_content(
                        input.get("name") or "",
                        input.get("content") or "",
                    )
                    return _json_result({"status": "valid"})
                case "telemetry":
                    meta = await self._manager.record_usage(
                        (input.get("name") or "").strip(),
                        success=bool(input.get("success", True)),
                        environment_id=input.get("environment_id") or "",
                        domain=input.get("domain") or "",
                        action_safety_class=input.get("action_safety_class") or "",
                    )
                    return _json_result({"status": "recorded", "metadata": meta})
                case _:
                    return ToolResult(
                        tool_call_id="",
                        content=f"Error: unsupported skill_manage action {action!r}.",
                        is_error=True,
                    )
        except Exception as exc:
            logger.warning("skill_manage action %r failed: %s", action, exc)
            return ToolResult(tool_call_id="", content=f"Error: {exc}", is_error=True)


def _json_result(payload: dict) -> ToolResult:
    import dataclasses
    import json

    def _default(value):
        if dataclasses.is_dataclass(value):
            return dataclasses.asdict(value)
        return str(value)

    return ToolResult(
        tool_call_id="",
        content=json.dumps(payload, default=_default, indent=2, sort_keys=True),
    )
