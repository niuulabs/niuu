"""Resident session controllers participating in the Ravn mesh."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from sleipnir.adapters.in_process import InProcessBus
from volundr.adapters.outbound.resident_flock import ResidentFlockAdapter
from volundr.domain.models import (
    ResidentBackend,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
    ResidentSession,
)


class _Repository:
    def __init__(self, runtime: ResidentRuntime) -> None:
        self.runtime = runtime

    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        return [self.runtime]

    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        return self.runtime if runtime_id == self.runtime.id else None


class _Connection:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def receive(self) -> dict:
        return await self.frames.get()

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)
        await self.frames.put(
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "alive"}}
        )
        await self.frames.put({"type": "result", "result": "alive"})

    async def close(self) -> None:
        self.closed = True


class _Controller:
    engine = ResidentEngine.HERMES

    def __init__(self) -> None:
        self.connection = _Connection()
        self.created: list[tuple[str, str]] = []

    async def create_session(
        self,
        runtime: ResidentRuntime,
        *,
        title: str,
        model: str,
    ) -> ResidentSession:
        self.created.append((title, model))
        now = datetime.now(UTC)
        return ResidentSession(
            id=uuid4(),
            resident_id=runtime.id,
            title=title,
            model=model,
            created_at=now,
            updated_at=now,
        )

    async def connect_chat(self, runtime: ResidentRuntime, session_id: UUID) -> _Connection:
        return self.connection


async def test_ravn_rpc_dispatches_through_existing_resident_session_controller() -> None:
    flock_id = uuid4()
    runtime = ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Hermes worker",
        model="niuu/qwen",
        backend=ResidentBackend.LOCAL,
        engine=ResidentEngine.HERMES,
        profile_id="hermes-local",
        observed_state=ResidentObservedState.ACTIVE,
        flock_id=flock_id,
        flock_member_id=uuid4(),
        flock_role="specialist",
        flock_peer_id="hermes-worker",
    )
    bus = InProcessBus()
    controller = _Controller()
    resident = ResidentFlockAdapter(_Repository(runtime), [controller], bus)
    await resident.sync()
    discovery = EventBusDiscoveryAdapter(
        MeshIdentity(
            peer_id="coordinator",
            realm_id=str(flock_id),
            persona="Coordinator",
            capabilities=[],
            permission_mode="permissive",
            version="test",
        ),
        bus,
        bus,
        manage_transport_lifecycle=False,
    )
    coordinator = SleipnirMeshAdapter(
        bus,
        bus,
        "coordinator",
        discovery=discovery,
        manage_transport_lifecycle=False,
    )
    await discovery.start()
    await bus.flush()
    await coordinator.start()
    assert "hermes-worker" in discovery.peers()

    accepted = await coordinator.send(
        "hermes-worker",
        {
            "type": "task_dispatch",
            "task": {
                "task_id": "task-1",
                "title": "Prove life",
                "initiative_context": "Return alive",
            },
        },
    )
    assert accepted == {"status": "accepted", "task_id": "task-1"}

    for _ in range(20):
        status = await coordinator.send(
            "hermes-worker", {"type": "task_status", "task_id": "task-1"}
        )
        if status["status"] == "complete":
            break
        await asyncio.sleep(0)

    result = await coordinator.send("hermes-worker", {"type": "task_result", "task_id": "task-1"})
    assert result == {"task_id": "task-1", "status": "complete", "output": "alive"}
    assert controller.created == [("Prove life", "niuu/qwen")]
    assert controller.connection.sent[0]["content"] == "Prove life\n\nReturn alive"
    assert controller.connection.closed

    directed = await coordinator.send(
        "hermes-worker",
        {
            "type": "work_request",
            "request_id": "room-message-1",
            "prompt": "Reply directly",
        },
    )
    assert directed == {
        "status": "complete",
        "request_id": "room-message-1",
        "output": "alive",
    }
    assert controller.created[-1] == ("Directed flock message", "niuu/qwen")
    assert controller.connection.sent[-1]["content"] == "Reply directly"

    await coordinator.stop()
    await discovery.stop()
    await resident.stop()
