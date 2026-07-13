"""Cascade tools — coordinator agent API for parallel task execution (NIU-435).

These tools are registered when ``cascade.enabled: true`` in ravn.yaml.
They give the coordinator agent a clean interface over three cascade modes:

  Mode 1 — Local parallel:   enqueue subtasks into the local DriveLoop
  Mode 2 — Networked:        delegate to a flock peer via MeshPort.send()
  Mode 3 — Ephemeral spawn:  SpawnPort.spawn() then delegate via mesh

All routing logic (local vs mesh vs spawn) lives in ``task_create``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ravn.domain.models import AgentTask, OutputMode, ToolResult
from ravn.ports.tool import ToolPort

if TYPE_CHECKING:
    from ravn.drive_loop import DriveLoop
    from ravn.ports.discovery import DiscoveryPort
    from ravn.ports.mesh import MeshPort
    from ravn.ports.spawn import SpawnPort

logger = logging.getLogger(__name__)

_PERMISSION = "cascade:manage"

# Defaults — overridden by CascadeConfig when wired through build_cascade_tools()
_DEFAULT_MESH_DELEGATION_TIMEOUT_S = 30.0
_DEFAULT_COLLECT_POLL_INTERVAL_S = 2.0
_DEFAULT_COLLECT_TIMEOUT_S = 300.0


def _new_task_id() -> str:
    ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"task_{ts}_{uuid.uuid4().hex[:6]}"


# ---------------------------------------------------------------------------
# task_create
# ---------------------------------------------------------------------------


class TaskCreateTool(ToolPort):
    """Create and dispatch a subtask.

    Routing priority:
    1. If an idle capable peer is available and mesh is enabled → delegate via mesh
    2. If no idle peer but spawn=True → spawn a fresh instance then delegate
    3. Otherwise → enqueue locally in the DriveLoop

    Returns task_id and where the task is running (local/peer_id).
    """

    def __init__(
        self,
        drive_loop: DriveLoop,
        mesh: MeshPort | None = None,
        discovery: DiscoveryPort | None = None,
        spawn_adapter: SpawnPort | None = None,
        mesh_delegation_timeout_s: float = _DEFAULT_MESH_DELEGATION_TIMEOUT_S,
        allowed_target_personas: set[str] | None = None,
        allowed_target_resolver: Callable[[], set[str] | None] | None = None,
    ) -> None:
        self._drive_loop = drive_loop
        self._mesh = mesh
        self._discovery = discovery
        self._spawn = spawn_adapter
        self._mesh_delegation_timeout_s = mesh_delegation_timeout_s
        self._allowed_target_personas = allowed_target_personas
        self._allowed_target_resolver = allowed_target_resolver
        # task_id → peer_id for remote tasks
        self._remote_tasks: dict[str, str] = {}
        # parent_task_id → personas already dispatched from that workflow node execution
        self._dispatched_personas_by_parent: dict[str, set[str]] = {}

    @property
    def name(self) -> str:
        return "task_create"

    @property
    def description(self) -> str:
        return (
            "Create and dispatch a subtask to run in parallel. "
            "The coordinator automatically routes the task to an idle flock peer (if available), "
            "spawns a new Ravn instance (if spawn=true and no idle peers exist), "
            "or runs it locally in the drive loop. "
            "Returns task_id and execution location."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task prompt / instructions for the subtask agent.",
                },
                "title": {
                    "type": "string",
                    "description": "Short human-readable title for the task.",
                },
                "persona": {
                    "type": "string",
                    "description": "Optional persona name for the subtask agent.",
                },
                "peer_id": {
                    "type": "string",
                    "description": (
                        "Optional exact peer_id from flock_status. Use this when addressing a "
                        "specific resident member."
                    ),
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["silent", "ambient", "surface"],
                    "description": "Output mode (default: silent).",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priority (lower = higher priority, default 5).",
                },
                "spawn": {
                    "type": "boolean",
                    "description": (
                        "If true and no idle peers are available, spawn a fresh Ravn instance. "
                        "Default: false."
                    ),
                },
                "required_caps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability names the peer must have (optional).",
                },
            },
            "required": ["prompt", "title"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        prompt = input.get("prompt", "").strip()
        title = input.get("title", "untitled").strip()
        persona = input.get("persona") or None
        requested_peer_id = str(input.get("peer_id") or "").strip()
        output_mode = OutputMode(input.get("output_mode", "silent"))
        priority = int(input.get("priority", 5))
        allow_spawn = bool(input.get("spawn", False))
        required_caps: list[str] = input.get("required_caps") or []
        current_task = (
            self._drive_loop.current_task() if hasattr(self._drive_loop, "current_task") else None
        )
        resolved_allowed_target_personas = (
            self._allowed_target_resolver() if self._allowed_target_resolver is not None else None
        )
        if resolved_allowed_target_personas is None:
            resolved_allowed_target_personas = self._allowed_target_personas

        if (
            current_task is not None
            and current_task.workflow_node_id
            and resolved_allowed_target_personas is not None
            and not persona
        ):
            logger.warning(
                "task_create: explicit persona required for workflow node %s",
                current_task.workflow_node_id,
            )
            return ToolResult(
                tool_call_id="",
                content=json.dumps(
                    {
                        "error": "persona_required",
                        "workflow_node_id": current_task.workflow_node_id,
                        "allowed_personas": sorted(resolved_allowed_target_personas),
                    }
                ),
                is_error=True,
            )

        if (
            persona
            and resolved_allowed_target_personas is not None
            and persona not in resolved_allowed_target_personas
        ):
            logger.warning(
                "task_create: persona %s is not allowed by the active workflow graph (allowed=%s)",
                persona,
                sorted(resolved_allowed_target_personas),
            )
            return ToolResult(
                tool_call_id="",
                content=json.dumps(
                    {
                        "error": "persona_not_allowed",
                        "persona": persona,
                        "allowed_personas": sorted(resolved_allowed_target_personas),
                    }
                ),
                is_error=True,
            )

        if current_task is not None and persona:
            dispatched = self._dispatched_personas_by_parent.setdefault(current_task.task_id, set())
            if persona in dispatched:
                logger.warning(
                    "task_create: persona %s already dispatched from task %s workflow_node=%s",
                    persona,
                    current_task.task_id,
                    current_task.workflow_node_id or "-",
                )
                return ToolResult(
                    tool_call_id="",
                    content=json.dumps(
                        {
                            "error": "duplicate_stage_dispatch",
                            "persona": persona,
                            "parent_task_id": current_task.task_id,
                            "workflow_node_id": current_task.workflow_node_id or "",
                        }
                    ),
                    is_error=True,
                )

        task_id = _new_task_id()
        parent_root_correlation_id = (
            current_task.root_correlation_id or current_task.task_id
            if current_task is not None
            else ""
        )
        parent_session_id = current_task.session_id if current_task is not None else ""
        if current_task is not None and persona:
            self._dispatched_personas_by_parent.setdefault(current_task.task_id, set()).add(persona)

        # Try to delegate to an idle peer first
        if self._mesh is not None and self._discovery is not None:
            if requested_peer_id:
                peer = self._discovery.peers().get(requested_peer_id)
                if peer is None:
                    return ToolResult(
                        tool_call_id="",
                        content=json.dumps(
                            {"error": "peer_not_found", "peer_id": requested_peer_id}
                        ),
                        is_error=True,
                    )
                peer_caps = set(getattr(peer, "capabilities", []))
                if required_caps and not all(
                    capability in peer_caps for capability in required_caps
                ):
                    return ToolResult(
                        tool_call_id="",
                        content=json.dumps(
                            {
                                "error": "peer_missing_capabilities",
                                "peer_id": requested_peer_id,
                                "required_caps": required_caps,
                            }
                        ),
                        is_error=True,
                    )
                return await self._delegate_to_peer(
                    requested_peer_id,
                    task_id,
                    title,
                    prompt,
                    persona,
                    output_mode,
                    priority,
                    session_id=parent_session_id,
                    root_correlation_id=parent_root_correlation_id,
                )
            peer_id = self._pick_idle_peer(required_caps, preferred_persona=persona)
            if peer_id is not None:
                return await self._delegate_to_peer(
                    peer_id,
                    task_id,
                    title,
                    prompt,
                    persona,
                    output_mode,
                    priority,
                    session_id=parent_session_id,
                    root_correlation_id=parent_root_correlation_id,
                )

        # No idle peer — maybe spawn
        if allow_spawn and self._spawn is not None:
            try:
                from ravn.ports.spawn import SpawnConfig  # noqa: PLC0415

                spawn_cfg = SpawnConfig(
                    persona=persona or "",
                    caps=required_caps,
                    permission_mode="workspace_write",
                    max_concurrent_tasks=1,
                )
                peer_ids = await self._spawn.spawn(1, spawn_cfg)
                if peer_ids and self._mesh is not None:
                    peer_id = peer_ids[0]
                    return await self._delegate_to_peer(
                        peer_id, task_id, title, prompt, persona, output_mode, priority
                    )
            except Exception as exc:
                logger.warning("task_create: spawn failed, falling back to local: %s", exc)

        # Fall back to local enqueue
        agent_task = AgentTask(
            task_id=task_id,
            title=title,
            initiative_context=prompt,
            triggered_by="cascade:coordinator",
            output_mode=output_mode,
            persona=persona,
            priority=priority,
        )
        if parent_session_id:
            agent_task.session_id = parent_session_id
        if parent_root_correlation_id:
            agent_task.root_correlation_id = parent_root_correlation_id
        await self._drive_loop.enqueue(agent_task)
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"task_id": task_id, "location": "local"}),
        )

    def _pick_idle_peer(
        self,
        required_caps: list[str],
        *,
        preferred_persona: str | None = None,
    ) -> str | None:
        """Return an idle capable peer_id, preferring persona-specific matches."""
        if self._discovery is None:
            return None
        peers: dict = self._discovery.peers()
        preferred = (preferred_persona or "").strip()
        fallback: str | None = None
        for peer_id, peer in peers.items():
            if getattr(peer, "status", "") != "idle":
                continue
            if required_caps:
                peer_caps = set(getattr(peer, "capabilities", []))
                if not all(c in peer_caps for c in required_caps):
                    continue
            if preferred and str(getattr(peer, "persona", "")).strip() == preferred:
                return peer_id
            if fallback is None:
                fallback = peer_id
        return fallback

    async def _delegate_to_peer(
        self,
        peer_id: str,
        task_id: str,
        title: str,
        prompt: str,
        persona: str | None,
        output_mode: OutputMode,
        priority: int,
        *,
        session_id: str = "",
        root_correlation_id: str = "",
    ) -> ToolResult:
        assert self._mesh is not None  # noqa: S101
        task_dict = {
            "task_id": task_id,
            "title": title,
            "initiative_context": prompt,
            "triggered_by": "cascade:coordinator",
            "output_mode": str(output_mode),
            "persona": persona,
            "priority": priority,
        }
        if session_id:
            task_dict["session_id"] = session_id
        if root_correlation_id:
            task_dict["root_correlation_id"] = root_correlation_id
        try:
            reply = await self._mesh.send(
                target_peer_id=peer_id,
                message={"type": "task_dispatch", "task": task_dict},
                timeout_s=self._mesh_delegation_timeout_s,
            )
            if reply.get("status") == "accepted":
                self._remote_tasks[task_id] = peer_id
                return ToolResult(
                    tool_call_id="",
                    content=json.dumps(
                        {"task_id": task_id, "location": peer_id, "status": "accepted"}
                    ),
                )
            return ToolResult(
                tool_call_id="",
                content=json.dumps({"task_id": task_id, "error": reply.get("error", "rejected")}),
                is_error=True,
            )
        except Exception as exc:
            logger.warning("task_create: mesh delegation failed: %s — falling back to local", exc)

        # Delegation failed → local fallback
        agent_task = AgentTask(
            task_id=task_id,
            title=title,
            initiative_context=prompt,
            triggered_by="cascade:coordinator",
            output_mode=output_mode,
            persona=persona,
            priority=priority,
        )
        if session_id:
            agent_task.session_id = session_id
        if root_correlation_id:
            agent_task.root_correlation_id = root_correlation_id
        await self._drive_loop.enqueue(agent_task)
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"task_id": task_id, "location": "local"}),
        )


# ---------------------------------------------------------------------------
# task_status
# ---------------------------------------------------------------------------


class TaskStatusTool(ToolPort):
    """Query the status of a task (local or remote)."""

    def __init__(
        self,
        drive_loop: DriveLoop,
        mesh: MeshPort | None = None,
        remote_tasks: dict[str, str] | None = None,
        mesh_delegation_timeout_s: float = _DEFAULT_MESH_DELEGATION_TIMEOUT_S,
    ) -> None:
        self._drive_loop = drive_loop
        self._mesh = mesh
        # Shared dict: task_id → peer_id (populated by TaskCreateTool)
        self._remote_tasks: dict[str, str] = remote_tasks if remote_tasks is not None else {}
        self._mesh_delegation_timeout_s = mesh_delegation_timeout_s

    @property
    def name(self) -> str:
        return "task_status"

    @property
    def description(self) -> str:
        return (
            "Query the status of a task by task_id. "
            "For local tasks this checks the DriveLoop queue/active map. "
            "For remote tasks it sends a status query via mesh."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID returned by task_create."},
                "include_progress": {
                    "type": "boolean",
                    "description": (
                        "If true, include accumulated events so far. "
                        "Useful for polling a running task's progress. Default: false."
                    ),
                },
            },
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        task_id = input.get("task_id", "").strip()
        include_progress = bool(input.get("include_progress", False))
        if not task_id:
            return ToolResult(tool_call_id="", content="Error: task_id is required.", is_error=True)

        peer_id = self._remote_tasks.get(task_id)
        if peer_id is not None and self._mesh is not None:
            try:
                reply = await self._mesh.send(
                    target_peer_id=peer_id,
                    message={
                        "type": "task_status",
                        "task_id": task_id,
                        "include_progress": include_progress,
                    },
                    timeout_s=self._mesh_delegation_timeout_s,
                )
                return ToolResult(tool_call_id="", content=json.dumps(reply))
            except Exception as exc:
                logger.warning("task_status: mesh query failed: %s", exc)

        status_result = self._drive_loop.task_status(task_id, include_progress=include_progress)
        if include_progress and isinstance(status_result, dict):
            return ToolResult(
                tool_call_id="",
                content=json.dumps({"task_id": task_id, **status_result}),
            )
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"task_id": task_id, "status": status_result}),
        )


# ---------------------------------------------------------------------------
# task_list
# ---------------------------------------------------------------------------


class TaskListTool(ToolPort):
    """List all active and queued tasks (local + remote)."""

    def __init__(
        self,
        drive_loop: DriveLoop,
        mesh: MeshPort | None = None,
        discovery: DiscoveryPort | None = None,
        mesh_delegation_timeout_s: float = _DEFAULT_MESH_DELEGATION_TIMEOUT_S,
    ) -> None:
        self._drive_loop = drive_loop
        self._mesh = mesh
        self._discovery = discovery
        self._mesh_delegation_timeout_s = mesh_delegation_timeout_s

    @property
    def name(self) -> str:
        return "task_list"

    @property
    def description(self) -> str:
        return (
            "List all active and queued tasks. "
            "Shows local DriveLoop tasks plus aggregated status from all known flock peers."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        result: dict = {"local": self._local_task_list(), "remote": []}

        if self._mesh is not None and self._discovery is not None:
            peers = self._discovery.peers()

            async def _query_peer(peer_id: str) -> dict:
                try:
                    reply = await asyncio.wait_for(
                        self._mesh.send(
                            target_peer_id=peer_id,
                            message={"type": "task_list"},
                        ),
                        timeout=self._mesh_delegation_timeout_s,
                    )
                    return {"peer_id": peer_id, "reply": reply}
                except Exception as exc:
                    return {"peer_id": peer_id, "error": str(exc)}

            remote_results = await asyncio.gather(*[_query_peer(pid) for pid in peers])
            result["remote"] = list(remote_results)

        return ToolResult(tool_call_id="", content=json.dumps(result, indent=2))

    def _local_task_list(self) -> dict:
        return {
            "active": self._drive_loop.active_task_ids(),
            "queued": self._drive_loop.queued_task_ids(),
        }


# ---------------------------------------------------------------------------
# task_stop
# ---------------------------------------------------------------------------


class TaskStopTool(ToolPort):
    """Cancel a running or queued task."""

    def __init__(
        self,
        drive_loop: DriveLoop,
        mesh: MeshPort | None = None,
        remote_tasks: dict[str, str] | None = None,
    ) -> None:
        self._drive_loop = drive_loop
        self._mesh = mesh
        self._remote_tasks: dict[str, str] = remote_tasks if remote_tasks is not None else {}

    @property
    def name(self) -> str:
        return "task_stop"

    @property
    def description(self) -> str:
        return "Cancel a task by task_id. Works for both local and remote (mesh) tasks."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "Task ID to cancel."}},
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        task_id = input.get("task_id", "").strip()
        if not task_id:
            return ToolResult(tool_call_id="", content="Error: task_id is required.", is_error=True)

        peer_id = self._remote_tasks.get(task_id)
        if peer_id is not None and self._mesh is not None:
            try:
                reply = await self._mesh.send(
                    target_peer_id=peer_id,
                    message={"type": "task_cancel", "task_id": task_id},
                    timeout_s=10.0,
                )
                return ToolResult(tool_call_id="", content=json.dumps(reply))
            except Exception as exc:
                return ToolResult(
                    tool_call_id="",
                    content=f"Failed to cancel remote task: {exc}",
                    is_error=True,
                )

        await self._drive_loop.cancel(task_id)
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"task_id": task_id, "status": "cancel_requested"}),
        )


# ---------------------------------------------------------------------------
# task_collect
# ---------------------------------------------------------------------------


class TaskCollectTool(ToolPort):
    """Wait for a task to complete and return its output.

    Polls task_status until the task is no longer running/queued,
    then returns the collected output from the channel.
    """

    def __init__(
        self,
        drive_loop: DriveLoop,
        mesh: MeshPort | None = None,
        remote_tasks: dict[str, str] | None = None,
        poll_interval_s: float = _DEFAULT_COLLECT_POLL_INTERVAL_S,
        default_timeout_s: float = _DEFAULT_COLLECT_TIMEOUT_S,
    ) -> None:
        self._drive_loop = drive_loop
        self._mesh = mesh
        self._remote_tasks: dict[str, str] = remote_tasks if remote_tasks is not None else {}
        self._poll_interval_s = poll_interval_s
        self._default_timeout_s = default_timeout_s

    @property
    def name(self) -> str:
        return "task_collect"

    @property
    def description(self) -> str:
        return (
            "Wait for a task to complete and return its output. "
            "Polls task_status until the task finishes, then returns the result. "
            "Use timeout_s to bound the wait time."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to collect."},
                "timeout_s": {
                    "type": "number",
                    "description": f"Timeout in seconds (default {_DEFAULT_COLLECT_TIMEOUT_S}).",
                },
            },
            "required": ["task_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        task_id = input.get("task_id", "").strip()
        timeout_s = float(input.get("timeout_s", self._default_timeout_s))

        if not task_id:
            return ToolResult(tool_call_id="", content="Error: task_id is required.", is_error=True)

        peer_id = self._remote_tasks.get(task_id)

        try:
            await asyncio.wait_for(
                self._poll_until_done(task_id, peer_id),
                timeout=timeout_s,
            )
        except TimeoutError:
            return ToolResult(
                tool_call_id="",
                content=f"Task {task_id!r} did not complete within {timeout_s}s.",
                is_error=True,
            )

        if peer_id is not None and self._mesh is not None:
            try:
                reply = await self._mesh.send(
                    target_peer_id=peer_id,
                    message={"type": "task_result", "task_id": task_id},
                    timeout_s=10.0,
                )
                return ToolResult(tool_call_id="", content=json.dumps(reply))
            except Exception as exc:
                logger.warning("task_collect: failed to fetch remote result: %s", exc)

        # Local task — retrieve output from the TaskResultStore
        local_result = self._drive_loop.get_result(task_id)
        if local_result is not None:
            return ToolResult(
                tool_call_id="",
                content=json.dumps(
                    {
                        "task_id": task_id,
                        "status": local_result.status,
                        "output": local_result.output,
                        "event_count": len(local_result.events),
                    }
                ),
            )

        return ToolResult(
            tool_call_id="",
            content=json.dumps({"task_id": task_id, "status": "complete", "output": ""}),
        )

    async def _poll_until_done(self, task_id: str, peer_id: str | None) -> None:
        """Poll until task status is neither running nor queued."""
        while True:
            await asyncio.sleep(self._poll_interval_s)
            if peer_id is not None and self._mesh is not None:
                with suppress(Exception):
                    reply = await self._mesh.send(
                        target_peer_id=peer_id,
                        message={"type": "task_status", "task_id": task_id},
                        timeout_s=5.0,
                    )
                    status = reply.get("status", "unknown")
                    if status not in ("running", "queued"):
                        return
                    continue

            status = self._drive_loop.task_status(task_id)
            if status not in ("running", "queued"):
                return


# ---------------------------------------------------------------------------
# flock_spawn
# ---------------------------------------------------------------------------


class FlockSpawnTool(ToolPort):
    """Spawn fresh Ravn instances for this task."""

    def __init__(self, spawn_adapter: SpawnPort) -> None:
        self._spawn = spawn_adapter

    @property
    def name(self) -> str:
        return "flock_spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn N fresh Ravn instances (subprocess or Kubernetes Job). "
            "Returns peer_ids once instances are registered with the discovery service. "
            "Call flock_terminate when done to clean up."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "count": {
                    "type": "integer",
                    "description": "Number of Ravn instances to spawn (default 1).",
                    "minimum": 1,
                    "maximum": 10,
                },
                "persona": {
                    "type": "string",
                    "description": "Persona name for the spawned instances.",
                },
                "caps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Capability names (tools) the spawned instances should have.",
                },
                "permission_mode": {
                    "type": "string",
                    "enum": ["read_only", "workspace_write", "full_access"],
                    "description": "Permission mode (default: workspace_write).",
                },
            },
            "required": [],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        from ravn.ports.spawn import SpawnConfig  # noqa: PLC0415

        count = int(input.get("count", 1))
        persona = input.get("persona", "")
        caps: list[str] = input.get("caps") or []
        permission_mode = input.get("permission_mode", "workspace_write")

        config = SpawnConfig(
            persona=persona,
            caps=caps,
            permission_mode=permission_mode,  # type: ignore[arg-type]
            max_concurrent_tasks=1,
        )

        try:
            peer_ids = await self._spawn.spawn(count, config)
        except TimeoutError as exc:
            return ToolResult(tool_call_id="", content=str(exc), is_error=True)
        except Exception as exc:
            return ToolResult(tool_call_id="", content=f"Spawn failed: {exc}", is_error=True)

        return ToolResult(
            tool_call_id="",
            content=json.dumps({"spawned": peer_ids}),
        )


# ---------------------------------------------------------------------------
# flock_status
# ---------------------------------------------------------------------------


class FlockStatusTool(ToolPort):
    """Show the current DiscoveryPort peer table."""

    def __init__(self, discovery: DiscoveryPort) -> None:
        self._discovery = discovery

    @property
    def name(self) -> str:
        return "flock_status"

    @property
    def description(self) -> str:
        return (
            "Show the current flock: all verified peers with their peer_id, host, "
            "persona, capabilities, status (idle/busy), and task_count."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        peers = self._discovery.peers()
        output: list[dict] = []
        for peer_id, peer in peers.items():
            output.append(
                {
                    "peer_id": peer_id,
                    "host": getattr(peer, "host", "unknown"),
                    "persona": getattr(peer, "persona", ""),
                    "capabilities": getattr(peer, "capabilities", []),
                    "status": getattr(peer, "status", "unknown"),
                    "task_count": getattr(peer, "task_count", 0),
                }
            )

        if not output:
            return ToolResult(tool_call_id="", content="No verified peers in flock.")

        return ToolResult(tool_call_id="", content=json.dumps(output, indent=2))


# ---------------------------------------------------------------------------
# flock_terminate
# ---------------------------------------------------------------------------


class FlockTerminateTool(ToolPort):
    """Terminate spawned Ravn instances."""

    def __init__(self, spawn_adapter: SpawnPort) -> None:
        self._spawn = spawn_adapter

    @property
    def name(self) -> str:
        return "flock_terminate"

    @property
    def description(self) -> str:
        return (
            "Terminate spawned Ravn instances. "
            "Pass peer_ids to terminate specific instances, "
            "or omit to terminate all instances this daemon spawned."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "peer_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Peer IDs to terminate. Omit to terminate all.",
                }
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:  # noqa: A002
        peer_ids: list[str] = input.get("peer_ids") or []

        if not peer_ids:
            await self._spawn.terminate_all()
            return ToolResult(tool_call_id="", content="All spawned instances terminated.")

        errors: list[str] = []
        for pid in peer_ids:
            try:
                await self._spawn.terminate(pid)
            except Exception as exc:
                errors.append(f"{pid}: {exc}")

        if errors:
            return ToolResult(
                tool_call_id="",
                content=f"Terminated with errors: {'; '.join(errors)}",
                is_error=True,
            )
        return ToolResult(
            tool_call_id="",
            content=json.dumps({"terminated": peer_ids}),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_cascade_tools(
    drive_loop: DriveLoop,
    mesh: MeshPort | None = None,
    discovery: DiscoveryPort | None = None,
    spawn_adapter: SpawnPort | None = None,
    cascade_config: object | None = None,
    allowed_target_personas: set[str] | None = None,
    allowed_target_resolver: Callable[[], set[str] | None] | None = None,
) -> list[ToolPort]:
    """Build and return all cascade tools.

    Shared ``remote_tasks`` dict wires task_create → task_status/stop/collect
    so they can look up which peer owns a remote task.

    Pass a ``CascadeConfig`` instance as ``cascade_config`` to thread timing
    values from settings instead of using the built-in defaults.
    """
    if cascade_config is None:
        from ravn.config import CascadeConfig as _CascadeConfig  # noqa: PLC0415

        cascade_config = _CascadeConfig()

    delegation_timeout = getattr(
        cascade_config, "mesh_delegation_timeout_s", _DEFAULT_MESH_DELEGATION_TIMEOUT_S
    )
    collect_poll = getattr(
        cascade_config, "collect_poll_interval_s", _DEFAULT_COLLECT_POLL_INTERVAL_S
    )
    collect_timeout = getattr(cascade_config, "collect_timeout_s", _DEFAULT_COLLECT_TIMEOUT_S)

    remote_tasks: dict[str, str] = {}

    task_create = TaskCreateTool(
        drive_loop=drive_loop,
        mesh=mesh,
        discovery=discovery,
        spawn_adapter=spawn_adapter,
        mesh_delegation_timeout_s=delegation_timeout,
        allowed_target_personas=allowed_target_personas,
        allowed_target_resolver=allowed_target_resolver,
    )
    # Share the remote_tasks mapping so other tools can track remote tasks
    task_create._remote_tasks = remote_tasks

    tools: list[ToolPort] = [
        task_create,
        TaskStatusTool(
            drive_loop=drive_loop,
            mesh=mesh,
            remote_tasks=remote_tasks,
            mesh_delegation_timeout_s=delegation_timeout,
        ),
        TaskListTool(
            drive_loop=drive_loop,
            mesh=mesh,
            discovery=discovery,
            mesh_delegation_timeout_s=delegation_timeout,
        ),
        TaskStopTool(drive_loop=drive_loop, mesh=mesh, remote_tasks=remote_tasks),
        TaskCollectTool(
            drive_loop=drive_loop,
            mesh=mesh,
            remote_tasks=remote_tasks,
            poll_interval_s=collect_poll,
            default_timeout_s=collect_timeout,
        ),
    ]

    if discovery is not None:
        tools.append(FlockStatusTool(discovery=discovery))

    if spawn_adapter is not None:
        tools.append(FlockSpawnTool(spawn_adapter=spawn_adapter))
        tools.append(FlockTerminateTool(spawn_adapter=spawn_adapter))

    return tools
