"""Expose resident-native sessions as Ravn mesh task peers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from volundr.domain.models import ResidentObservedState
from volundr.domain.ports import (
    ResidentChatConnection,
    ResidentRuntimeRepository,
    ResidentSessionController,
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


class ResidentFlockAdapter:
    """Attach active resident session adapters to the configured Sleipnir bus."""

    def __init__(
        self,
        repository: ResidentRuntimeRepository,
        session_controllers: list[ResidentSessionController],
        bus: Any,
    ) -> None:
        self._repository = repository
        self._controllers = {controller.engine: controller for controller in session_controllers}
        self._bus = bus
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
            if runtime_id in self._peers:
                continue
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
                ),
                self._bus,
                self._bus,
                manage_transport_lifecycle=False,
            )
            try:
                await discovery.start()
                await peer.start()
            except Exception:
                await asyncio.gather(peer.stop(), discovery.stop(), return_exceptions=True)
                raise
            self._peers[runtime_id] = _ResidentPeer(mesh=peer, discovery=discovery)
            logger.info(
                "Resident flock peer started runtime=%s peer=%s flock=%s",
                runtime.id,
                runtime.flock_peer_id,
                runtime.flock_id,
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
            prompt = f"{title}\n\n{context}" if context else title
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
                if frame_type == "result":
                    result = str(frame.get("result") or "")
                    if result and not task.output:
                        task.output = result
                    task.status = "complete"
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
