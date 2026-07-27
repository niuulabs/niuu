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
from uuid import uuid4

from niuu.observability import get_observability
from ravn.context.autonomy import (
    AutonomyMode,
    AutonomyPolicy,
    JsonProposalStore,
    ProposalScope,
    SelfImprovementProposal,
)
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

    def __init__(
        self,
        skill_port: SkillPort,
        *,
        manager: SkillManagementRegistry | None = None,
    ) -> None:
        self._skill_port = skill_port
        self._manager = manager

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
            if self._manager is None:
                skills = await self._skill_port.list_skills(query=query)
            else:
                skills = await self._manager.list_runnable_skills(query=query)
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

    def __init__(
        self,
        skill_port: SkillPort,
        *,
        manager: SkillManagementRegistry | None = None,
    ) -> None:
        self._skill_port = skill_port
        self._manager = manager

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
            skill = (
                await self._manager.get_runnable_skill(skill_name)
                if self._manager is not None
                else await self._skill_port.get_skill(skill_name)
            )
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
        proposal_store: JsonProposalStore | None = None,
        autonomy_policy: AutonomyPolicy | None = None,
    ) -> None:
        self._manager = manager or SkillManagementRegistry(skill_port)
        self._proposal_store = proposal_store
        self._autonomy_policy = autonomy_policy or AutonomyPolicy()

    @property
    def name(self) -> str:
        return "skill_manage"

    @property
    def description(self) -> str:
        return (
            "Write-capable skill management for resident Valkyries. Supports "
            "create, update, archive, restore, pin, unpin, promote, show, list, "
            "validate, and telemetry actions with Environment/Flock scope metadata. "
            "Use lifecycle and usage evidence to maintain the capability portfolio; "
            "after verifying a replacement, archive an obsolete or inferior capability."
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
                        "proposals",
                        "rollback",
                    ],
                },
                "name": {"type": "string"},
                "proposal_id": {"type": "string"},
                "content": {"type": "string"},
                "description": {"type": "string"},
                "scope": {"type": "string"},
                "environment_id": {"type": "string"},
                "domain": {"type": "string"},
                "autonomy_mode": {"type": "string"},
                "risk_class": {"type": "string"},
                "risk_boundaries": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "delegated_capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "proposal_store_path": {"type": "string"},
                "source": {"type": "string"},
                "source_episode_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "status": {"type": "string"},
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

    async def execute(self, payload: dict) -> ToolResult:
        action = (payload.get("action") or "").strip()
        name = (payload.get("name") or "").strip()
        telemetry = get_observability()
        attributes = {
            "ravn.skill.lifecycle.action": action or "unknown",
            "ravn.skill.name": name,
        }
        with telemetry.span("ravn.skill.lifecycle", attributes=attributes) as span:
            result = await self._execute_action(action, payload)
            outcome = "error" if result.is_error else "success"
            span.set_attribute("ravn.skill.lifecycle.outcome", outcome)
            telemetry.event(
                "ravn.skill.lifecycle.finished",
                attributes={**attributes, "ravn.skill.lifecycle.outcome": outcome},
                content=result.content,
            )
            return result

    async def _execute_action(self, action: str, payload: dict) -> ToolResult:
        try:
            if action in {
                "create",
                "update",
                "patch",
                "archive",
                "delete",
                "restore",
                "pin",
                "unpin",
                "promote",
            } and payload.get("autonomy_mode"):
                return await self._execute_autonomous_mutation(action, payload)

            match action:
                case "create":
                    return _json_result(await self._apply_mutation(action, payload))
                case "update" | "patch":
                    return _json_result(await self._apply_mutation(action, payload))
                case "archive" | "delete":
                    return _json_result(await self._apply_mutation(action, payload))
                case "restore":
                    return _json_result(await self._apply_mutation(action, payload))
                case "pin" | "unpin":
                    return _json_result(await self._apply_mutation(action, payload))
                case "promote":
                    return _json_result(await self._apply_mutation(action, payload))
                case "show":
                    data = await self._manager.show(
                        (payload.get("name") or "").strip(),
                        include_archived=bool(payload.get("include_archived")),
                    )
                    return _json_result(data)
                case "list":
                    rows = await self._manager.list_skills(
                        payload.get("query") or None,
                        include_archived=bool(payload.get("include_archived")),
                    )
                    return _json_result({"skills": rows})
                case "validate":
                    SkillManagementRegistry._validate_name_content(
                        payload.get("name") or "",
                        payload.get("content") or "",
                    )
                    return _json_result({"status": "valid"})
                case "telemetry":
                    meta = await self._manager.record_usage(
                        (payload.get("name") or "").strip(),
                        success=bool(payload.get("success", True)),
                        environment_id=payload.get("environment_id") or "",
                        domain=payload.get("domain") or "",
                        action_safety_class=payload.get("action_safety_class") or "",
                    )
                    return _json_result({"status": "recorded", "metadata": meta})
                case "proposals":
                    store = self._store_for_input(payload)
                    proposals = store.list(
                        status=payload.get("status") or None,
                        environment_id=payload.get("environment_id") or None,
                    )
                    return _json_result({"proposals": proposals})
                case "rollback":
                    store = self._store_for_input(payload)
                    proposal_id = (payload.get("proposal_id") or "").strip()
                    proposal = await store.rollback(proposal_id, self._rollback_skill_change)
                    return _json_result({"status": "rolled_back", "proposal": proposal})
                case _:
                    return ToolResult(
                        tool_call_id="",
                        content=f"Error: unsupported skill_manage action {action!r}.",
                        is_error=True,
                    )
            raise AssertionError("unreachable skill_manage action dispatch")
        except Exception as exc:
            logger.warning("skill_manage action %r failed: %s", action, exc)
            return ToolResult(tool_call_id="", content=f"Error: {exc}", is_error=True)

    async def _execute_autonomous_mutation(self, action: str, input: dict) -> ToolResult:
        store = self._store_for_input(input)
        proposal = self._proposal_from_input(action, input)
        decision = self._autonomy_policy.decide(proposal)
        proposal = store.record_decision(proposal, decision)
        if decision.decision != "allow":
            return _json_result({"status": proposal.status, "proposal": proposal})

        async def _apply(_: SelfImprovementProposal) -> tuple[dict[str, str], dict]:
            before = await self._snapshot_skill(input)
            payload = await self._apply_mutation(action, input)
            return (
                {
                    "skill": str(payload.get("skill") or input.get("name") or ""),
                    "action": action,
                },
                {"skill_name": input.get("name") or "", "before": before},
            )

        proposal = await store.apply(proposal.proposal_id, _apply)
        return _json_result(
            {
                "status": "applied",
                "proposal": proposal,
                "policy_decision": proposal.policy_decision,
                "skill": proposal.applied_artifact_refs.get("skill", ""),
            }
        )

    async def _apply_mutation(self, action: str, payload: dict) -> dict:
        name = (payload.get("name") or "").strip()
        match action:
            case "create":
                skill = await self._manager.create(
                    name=name,
                    content=payload.get("content") or "",
                    description=payload.get("description") or "",
                    scope=payload.get("scope") or "private",
                    environment_id=payload.get("environment_id") or "",
                    domain=payload.get("domain") or "",
                    source=payload.get("source") or "manual",
                    action_safety_class=payload.get("action_safety_class") or "read_only",
                )
                return {"status": "created", "skill": skill.name}
            case "update" | "patch":
                skill = await self._manager.update(
                    name=name,
                    content=payload.get("content"),
                    description=payload.get("description"),
                )
                return {"status": "updated", "skill": skill.name}
            case "archive" | "delete":
                meta = await self._manager.archive(name)
                return {"status": "archived", "metadata": meta, "skill": name}
            case "restore":
                meta = await self._manager.restore(name)
                return {"status": "restored", "metadata": meta, "skill": name}
            case "pin" | "unpin":
                meta = await self._manager.pin(name, pinned=action == "pin")
                return {"status": action, "metadata": meta, "skill": name}
            case "promote":
                meta = await self._manager.promote(
                    name,
                    scope=payload.get("scope") or "environment",
                    environment_id=payload.get("environment_id") or "",
                    domain=payload.get("domain") or "",
                )
                return {"status": "promoted", "metadata": meta, "skill": name}
            case _:
                raise ValueError(f"unsupported mutation action: {action}")
        raise AssertionError("unreachable skill mutation dispatch")

    def _proposal_from_input(self, action: str, input: dict) -> SelfImprovementProposal:
        name = (input.get("name") or "").strip()
        mode = input.get("autonomy_mode") or AutonomyMode.GUARDED.value
        scope = input.get("scope") or ProposalScope.PRIVATE.value
        return SelfImprovementProposal(
            proposal_id=str(input.get("proposal_id") or uuid4()),
            title=f"{action} skill {name}",
            artifact_type="skill",
            action=action,
            content=input.get("content") or input.get("description") or name,
            scope=scope,
            environment_id=input.get("environment_id") or "",
            domain=input.get("domain") or "",
            mode=mode,
            risk_class=input.get("risk_class") or "low",
            risk_boundaries=_coerce_string_list(input.get("risk_boundaries")),
            delegated_capabilities=_coerce_string_list(input.get("delegated_capabilities")),
            source_episode_ids=_coerce_string_list(input.get("source_episode_ids")),
        )

    async def _snapshot_skill(self, input: dict) -> dict:
        name = (input.get("name") or "").strip()
        if not name:
            return {}
        try:
            return await self._manager.show(name, include_archived=True)
        except Exception:
            return {}

    async def _rollback_skill_change(self, proposal: SelfImprovementProposal) -> None:
        rollback = proposal.rollback_metadata or {}
        name = rollback.get("skill_name") or proposal.applied_artifact_refs.get("skill", "")
        before = rollback.get("before") or {}
        if not name:
            raise ValueError("proposal rollback metadata does not include skill name")

        if not before:
            await self._manager.archive(name)
            return

        skill = before.get("skill") or {}
        metadata = before.get("metadata") or {}
        await self._manager.update(
            name=name,
            content=skill.get("content"),
            description=skill.get("description"),
        )
        await self._manager.promote(
            name,
            scope=metadata.get("scope") or "private",
            environment_id=metadata.get("environment_id") or "",
            domain=metadata.get("domain") or "",
        )
        await self._manager.pin(name, pinned=bool(metadata.get("pinned")))
        if metadata.get("status") == "archived":
            await self._manager.archive(name)
        else:
            await self._manager.restore(name)

    def _store_for_input(self, input: dict) -> JsonProposalStore:
        if input.get("proposal_store_path"):
            return JsonProposalStore(input["proposal_store_path"])
        if self._proposal_store is None:
            self._proposal_store = JsonProposalStore()
        return self._proposal_store


def _coerce_string_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part) for part in value if str(part)]
    return []


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
