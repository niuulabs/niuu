"""Expose resident-native sessions as Ravn mesh task peers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.models import OutputMode
from volundr.domain.models import ResidentObservedState
from volundr.domain.ports import (
    ResidentChatConnection,
    ResidentRuntimeRepository,
    ResidentSessionController,
    SessionPersona,
    SessionPersonaProvider,
)

logger = logging.getLogger(__name__)


@dataclass
class _ResidentTask:
    task_id: str
    runtime_id: UUID
    status: str = "queued"
    output: str = ""
    error: str = ""
    connection: ResidentChatConnection | None = None
    runner: asyncio.Task[None] | None = None


@dataclass
class _ResidentPeer:
    mesh: SleipnirMeshAdapter
    discovery: EventBusDiscoveryAdapter
    persona: SessionPersona | None


class ResidentFlockAdapter:
    """Attach active resident session adapters to the configured Sleipnir bus."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        session_controllers: list[ResidentSessionController],
        bus: Any,
        *,
        persona_provider: SessionPersonaProvider | None = None,
    ) -> None:
        self._repository = repository
        self._controllers = {controller.engine: controller for controller in session_controllers}
        self._bus = bus
        self._persona_provider = persona_provider
        self._peers: dict[UUID, _ResidentPeer] = {}
        self._tasks: dict[str, _ResidentTask] = {}

    async def sync(self) -> None:
        """Converge mesh RPC listeners with active flock-enabled residents."""
        runtimes = await self._repository.list_for_reconciliation()
        desired = {
            runtime.id: runtime
            for runtime in runtimes
            if runtime.observed_state is ResidentObservedState.ACTIVE
            and runtime.flock_id is not None
            and runtime.flock_peer_id
            and runtime.engine in self._controllers
        }
        for runtime_id in set(self._peers) - set(desired):
            peer = self._peers.pop(runtime_id)
            await peer.mesh.stop()
            await peer.discovery.stop()
        for runtime_id, runtime in desired.items():
            persona = await self._resolve_persona(runtime.owner_id, runtime.persona_name)
            current = self._peers.get(runtime_id)
            if current is not None and current.persona == persona:
                continue
            if current is not None:
                self._peers.pop(runtime_id, None)
                await current.mesh.stop()
                await current.discovery.stop()
            peer = SleipnirMeshAdapter(
                publisher=self._bus,
                subscriber=self._bus,
                own_peer_id=runtime.flock_peer_id,
                environment_id=str(runtime.flock_id),
                manage_transport_lifecycle=False,
            )
            peer.set_rpc_handler(
                lambda message, resident_id=runtime_id: self._handle_rpc(resident_id, message)
            )
            discovery = EventBusDiscoveryAdapter(
                MeshIdentity(
                    peer_id=runtime.flock_peer_id,
                    realm_id=str(runtime.flock_id),
                    persona=runtime.persona_name or runtime.name,
                    capabilities=[capability.value for capability in runtime.capabilities],
                    permission_mode="permissive",
                    version="volundr",
                    consumes_event_types=(
                        list(persona.consumes_event_types) if persona is not None else []
                    ),
                    emits_event_types=(
                        [persona.produces_event_type]
                        if persona is not None and persona.produces_event_type
                        else []
                    ),
                ),
                self._bus,
                self._bus,
                manage_transport_lifecycle=False,
            )
            resident_peer = _ResidentPeer(mesh=peer, discovery=discovery, persona=persona)
            self._peers[runtime_id] = resident_peer
            try:
                await discovery.start()
                await peer.start()
                if persona is not None:
                    for event_type in persona.consumes_event_types:
                        await peer.subscribe(
                            event_type,
                            lambda event, resident_id=runtime_id: self._handle_persona_event(
                                resident_id, event
                            ),
                        )
            except Exception:
                self._peers.pop(runtime_id, None)
                await asyncio.gather(peer.stop(), discovery.stop(), return_exceptions=True)
                raise
            logger.info(
                "Resident flock peer started runtime=%s peer=%s flock=%s",
                runtime.id,
                runtime.flock_peer_id,
                runtime.flock_id,
            )

    async def _resolve_persona(self, owner_id: str, name: str) -> SessionPersona | None:
        if self._persona_provider is None or not name.strip():
            return None
        persona = await self._persona_provider.get(owner_id, name)
        if persona is None:
            logger.warning("Resident flock persona not found owner=%s name=%s", owner_id, name)
        return persona

    async def _handle_persona_event(self, runtime_id: UUID, event: RavnEvent) -> None:
        runtime = await self._repository.get(runtime_id)
        if runtime is None or event.source == runtime.flock_peer_id:
            return
        event_type = str(event.payload.get("event_type") or "event")
        identity = ":".join(
            (
                str(runtime_id),
                event_type,
                event.task_id or "",
                event.correlation_id,
                event.root_correlation_id,
            )
        )
        task_id = f"event_{hashlib.sha256(identity.encode()).hexdigest()[:20]}"
        payload = json.dumps(event.payload, sort_keys=True, default=str)
        result = await self._dispatch(
            runtime_id,
            {
                "task_id": task_id,
                "title": f"React to {event_type} ({task_id[-8:]})",
                "initiative_context": (
                    f"A subscribed flock event was received.\n"
                    f"Event type: {event_type}\n"
                    f"Source: {event.source}\n"
                    f"Payload: {payload}"
                ),
                "triggered_by": f"mesh:event:{event_type}",
                "output_mode": OutputMode.SURFACE,
                "session_id": event.session_id,
                "root_correlation_id": event.root_correlation_id or event.correlation_id,
            },
        )
        if result.get("status") != "accepted":
            logger.warning(
                "Resident persona event rejected runtime=%s event_type=%s error=%s",
                runtime_id,
                event_type,
                result.get("error", "unknown"),
            )

    async def stop(self) -> None:
        """Stop RPC listeners and active resident task connections."""
        for peer in list(self._peers.values()):
            await peer.mesh.stop()
            await peer.discovery.stop()
        self._peers.clear()
        for task in self._tasks.values():
            if task.runner and not task.runner.done():
                task.runner.cancel()
        await asyncio.gather(
            *(task.runner for task in self._tasks.values() if task.runner),
            return_exceptions=True,
        )

    async def _handle_rpc(self, runtime_id: UUID, message: dict[str, Any]) -> dict[str, Any]:
        message_type = str(message.get("type") or "")
        if message_type == "work_request":
            request_id = str(message.get("request_id") or "").strip()
            if not request_id:
                return {"status": "error", "error": "request_id is required"}
            accepted = await self._dispatch(
                runtime_id,
                {
                    "task_id": request_id,
                    "title": "Directed flock message",
                    "prompt": str(message.get("prompt") or ""),
                    "session_id": str(message.get("session_id") or ""),
                    "root_correlation_id": str(message.get("root_correlation_id") or ""),
                },
            )
            if accepted.get("status") != "accepted":
                return accepted
            task = self._tasks[request_id]
            if task.runner is not None:
                await task.runner
            return {
                "status": task.status,
                "request_id": request_id,
                "output": task.output,
                **({"error": task.error} if task.error else {}),
            }
        if message_type == "task_dispatch":
            return await self._dispatch(runtime_id, message.get("task"))
        if message_type == "task_list":
            runtime_tasks = [item for item in self._tasks.values() if item.runtime_id == runtime_id]
            return {
                "active": [item.task_id for item in runtime_tasks if item.status == "running"],
                "queued": [item.task_id for item in runtime_tasks if item.status == "queued"],
            }
        task_id = str(message.get("task_id") or "")
        task = self._tasks.get(task_id)
        if task is None or task.runtime_id != runtime_id:
            return {"error": "task_not_found", "task_id": task_id}
        if message_type == "task_status":
            response = {"task_id": task_id, "status": task.status}
            if message.get("include_progress"):
                response["progress"] = task.output
            return response
        if message_type == "task_result":
            return {
                "task_id": task_id,
                "status": task.status,
                "output": task.output,
                **({"error": task.error} if task.error else {}),
            }
        if message_type == "task_cancel":
            if task.connection is not None:
                await task.connection.send({"type": "interrupt"})
            task.status = "cancelled"
            if task.runner is not None and not task.runner.done():
                task.runner.cancel()
            return {"task_id": task_id, "status": "cancelled"}
        return {"error": "unsupported_message", "type": message_type}

    async def _dispatch(self, runtime_id: UUID, raw_task: Any) -> dict[str, Any]:
        task_payload = raw_task if isinstance(raw_task, dict) else {}
        task_id = str(task_payload.get("task_id") or "").strip()
        if not task_id:
            return {"status": "rejected", "error": "task_id is required"}
        existing = self._tasks.get(task_id)
        if existing is not None:
            if existing.runtime_id == runtime_id:
                return {"status": "accepted", "task_id": task_id}
            return {"status": "rejected", "error": "task_id belongs to another resident"}
        task = _ResidentTask(task_id=task_id, runtime_id=runtime_id)
        self._tasks[task_id] = task
        task.runner = asyncio.create_task(
            self._run_task(task, task_payload),
            name=f"resident-flock-{task_id}",
        )
        return {"status": "accepted", "task_id": task_id}

    async def _run_task(self, task: _ResidentTask, payload: dict[str, Any]) -> None:
        connection: ResidentChatConnection | None = None
        try:
            runtime = await self._repository.get(task.runtime_id)
            if runtime is None:
                raise RuntimeError(f"Resident runtime {task.runtime_id} no longer exists")
            controller = self._controllers[runtime.engine]
            title = str(payload.get("title") or "Remote flock task")
            context = str(payload.get("initiative_context") or "").strip()
            prompt = str(payload.get("prompt") or "").strip()
            if not prompt:
                prompt = f"{title}\n\n{context}" if context else title
            peer = self._peers.get(task.runtime_id)
            persona = peer.persona if peer is not None else None
            if persona is not None and persona.system_prompt:
                prompt = f"{persona.system_prompt}\n\nTask:\n{prompt}"
            session = await controller.create_session(runtime, title=title, model=runtime.model)
            connection = await controller.connect_chat(runtime, session.id)
            task.connection = connection
            task.status = "running"
            await connection.send({"type": "user", "content": prompt, "request_id": task.task_id})
            while task.status == "running":
                frame = await connection.receive()
                frame_type = str(frame.get("type") or "")
                if frame_type == "content_block_delta":
                    delta = frame.get("delta")
                    if isinstance(delta, dict):
                        task.output += str(delta.get("text") or "")
                    continue
                if frame_type == "control_request":
                    await connection.send(
                        {
                            "type": "permission_response",
                            "request_id": str(frame.get("request_id") or ""),
                            "behavior": "allowOnce",
                        }
                    )
                    continue
                if frame_type == "result":
                    result = str(frame.get("result") or "")
                    if result and not task.output:
                        task.output = result
                    task.status = "complete"
                    await self._publish_task_output(runtime, task, payload, persona)
                    return
                if frame_type == "error":
                    task.error = str(frame.get("error") or "Resident task failed")
                    task.status = "failed"
                    return
        except asyncio.CancelledError:
            task.status = "cancelled"
            raise
        except Exception as exc:
            task.error = str(exc)
            task.status = "failed"
            logger.exception(
                "Resident flock task failed runtime=%s task=%s",
                task.runtime_id,
                task.task_id,
            )
        finally:
            task.connection = None
            if connection is not None:
                await connection.close()

    async def _publish_task_output(
        self,
        runtime: Any,
        task: _ResidentTask,
        payload: dict[str, Any],
        persona: SessionPersona | None,
    ) -> None:
        peer = self._peers.get(task.runtime_id)
        if peer is None:
            return
        session_id = str(payload.get("session_id") or "")
        root_correlation_id = str(payload.get("root_correlation_id") or session_id or task.task_id)
        output_mode = OutputMode(str(payload.get("output_mode") or OutputMode.SILENT))
        if output_mode == OutputMode.SURFACE and task.output:
            response = RavnEvent(
                type=RavnEventType.RESPONSE,
                source=runtime.flock_peer_id,
                payload={"text": task.output, "persona": runtime.persona_name or runtime.name},
                timestamp=datetime.now(UTC),
                urgency=0.2,
                correlation_id=session_id or task.task_id,
                session_id=session_id,
                task_id=task.task_id,
                root_correlation_id=root_correlation_id,
            )
            await peer.mesh.publish(response, topic=f"activity.{runtime.flock_peer_id}")

        if persona is None or not persona.produces_event_type:
            return
        schema = OutcomeSchema(persona.produces_schema) if persona.produces_schema else None
        parsed = parse_outcome_block(task.output, schema)
        fields = dict(parsed.fields) if parsed is not None else {}
        outcome_payload: dict[str, Any] = {
            "persona": persona.name,
            "success": True,
            "event_type": persona.produces_event_type,
            "outcome": fields,
            "fields": fields,
            "valid": bool(parsed.valid) if parsed is not None else not persona.produces_schema,
            "task_id": task.task_id,
            "bubble_up": True,
        }
        summary = fields.get("summary")
        if isinstance(summary, str) and summary.strip():
            outcome_payload["summary"] = summary.strip()
        outcome = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=runtime.flock_peer_id,
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id=task.task_id,
            session_id=session_id,
            task_id=task.task_id,
            root_correlation_id=root_correlation_id,
        )
        await peer.mesh.publish(outcome, topic=persona.produces_event_type)
