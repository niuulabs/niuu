"""Resident session controllers participating in the Ravn mesh."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.outcome import OutcomeField
from niuu.mesh import mesh_event_prefix
from niuu.mesh.identity import MeshIdentity
from ravn.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.domain.events import RavnEvent, RavnEventType
from sleipnir.adapters.in_process import InProcessBus
from volundr.adapters.outbound.resident_flock import ResidentFlockAdapter
from volundr.domain.models import (
    ResidentBackend,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
    ResidentSession,
)
from volundr.domain.ports import SessionPersona


class _Repository:
    def __init__(self, runtime: ResidentRuntime) -> None:
        self.runtime = runtime

    async def list_for_reconciliation(self) -> list[ResidentRuntime]:
        return [self.runtime]

    async def get(self, runtime_id: UUID) -> ResidentRuntime | None:
        return self.runtime if runtime_id == self.runtime.id else None


class _Connection:
    def __init__(self, output: str = "alive", *, requires_approval: bool = False) -> None:
        self.frames: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.output = output
        self.requires_approval = requires_approval

    async def receive(self) -> dict:
        return await self.frames.get()

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)
        if frame.get("type") == "user" and self.requires_approval:
            await self.frames.put(
                {
                    "type": "control_request",
                    "request_id": "approval-1",
                    "tool": "terminal",
                }
            )
            return
        await self.frames.put(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": self.output},
            }
        )
        await self.frames.put({"type": "result", "result": self.output})

    async def close(self) -> None:
        self.closed = True


class _Controller:
    engine = ResidentEngine.HERMES

    def __init__(self, output: str = "alive", *, requires_approval: bool = False) -> None:
        self.connection = _Connection(output, requires_approval=requires_approval)
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


class _PersonaProvider:
    def __init__(self, persona: SessionPersona) -> None:
        self.persona = persona
        self.requests: list[tuple[str, str]] = []

    async def get(self, owner_id: str, name: str) -> SessionPersona | None:
        self.requests.append((owner_id, name))
        return self.persona if name == self.persona.name else None


def _runtime(*, flock_id: UUID, persona_name: str = "") -> ResidentRuntime:
    return ResidentRuntime(
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Hermes worker",
        persona_name=persona_name,
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


async def test_ravn_rpc_dispatches_through_existing_resident_session_controller() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id)
    bus = InProcessBus()
    controller = _Controller(requires_approval=True)
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
        environment_id=str(flock_id),
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
    assert controller.connection.sent[1] == {
        "type": "permission_response",
        "request_id": "approval-1",
        "behavior": "allowOnce",
    }
    assert controller.connection.closed

    directed = await coordinator.send(
        "hermes-worker",
        {
            "type": "work_request",
            "request_id": "room-message-1",
            "prompt": "Reply directly",
            "session_id": "room-1",
            "root_correlation_id": "room-1",
        },
    )
    assert directed == {
        "status": "complete",
        "request_id": "room-message-1",
        "output": "alive",
    }
    assert controller.created[-1] == ("Directed flock message", "niuu/qwen")
    user_frames = [frame for frame in controller.connection.sent if frame.get("type") == "user"]
    assert user_frames[-1]["content"] == "Reply directly"
    assert resident._tasks["room-message-1"].status == "complete"

    await coordinator.stop()
    await discovery.stop()
    await resident.stop()


async def test_directed_work_request_preserves_room_correlation() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id)
    resident = ResidentFlockAdapter(_Repository(runtime), [_Controller()], InProcessBus())
    captured: dict = {}

    async def capture_dispatch(runtime_id: UUID, payload: dict) -> dict:
        captured.update(payload)
        return {"status": "rejected", "error": "captured"}

    resident._dispatch = capture_dispatch  # type: ignore[method-assign]
    result = await resident._handle_rpc(
        runtime.id,
        {
            "type": "work_request",
            "request_id": "room-message-1",
            "prompt": "Reply directly",
            "session_id": "room-1",
            "root_correlation_id": "root-1",
        },
    )

    assert result == {"status": "rejected", "error": "captured"}
    assert captured["session_id"] == "room-1"
    assert captured["root_correlation_id"] == "root-1"


async def test_resident_persona_subscribes_surfaces_and_emits_declared_outcome() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="Act as the event Hermes.",
        consumes_event_types=("proof.started",),
        produces_event_type="proof.hermes.completed",
        produces_schema={
            "summary": OutcomeField(type="string", description="result summary"),
        },
    )
    provider = _PersonaProvider(persona)
    output = "---outcome---\nsummary: HERMES_EVENT_OK\n---end---"
    controller = _Controller(output)
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [controller],
        bus,
        persona_provider=provider,
    )
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
        environment_id=str(flock_id),
        manage_transport_lifecycle=False,
    )
    await discovery.start()
    await bus.flush()
    await coordinator.start()

    peer = discovery.peers()["hermes-worker"]
    assert peer.persona == "event-hermes"
    assert peer.consumes_event_types == ["proof.started"]
    assert peer.emits_event_types == ["proof.hermes.completed"]
    assert provider.requests == [("user-a", "event-hermes")]

    surfaced: list[object] = []
    outcomes: list[object] = []
    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], surfaced.append)
    await bus.subscribe([f"{prefix}.proof.hermes.completed"], outcomes.append)

    accepted = await coordinator.send(
        "hermes-worker",
        {
            "type": "task_dispatch",
            "task": {
                "task_id": "surface-task",
                "title": "Surface proof",
                "initiative_context": "Return the proof outcome",
                "output_mode": "surface",
                "session_id": "room-1",
                "root_correlation_id": "room-1",
            },
        },
    )
    assert accepted["status"] == "accepted"
    for _ in range(30):
        result = await coordinator.send(
            "hermes-worker", {"type": "task_result", "task_id": "surface-task"}
        )
        if result.get("status") == "complete":
            break
        await asyncio.sleep(0)
    await bus.flush()

    assert controller.connection.sent[0]["content"].startswith(
        "Act as the event Hermes.\n\nTask:\n"
    )
    assert len(surfaced) == 1
    assert surfaced[0].payload["ravn_event"]["text"] == output
    assert surfaced[0].payload["ravn_session_id"] == "room-1"
    assert len(outcomes) == 1
    assert outcomes[0].payload["ravn_event"]["event_type"] == "proof.hermes.completed"
    assert outcomes[0].payload["ravn_event"]["fields"] == {"summary": "HERMES_EVENT_OK"}

    await coordinator.stop()
    await discovery.stop()
    await resident.stop()


async def test_matching_persona_event_wakes_resident_and_surfaces_response() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="React to subscribed proof events.",
        consumes_event_types=("proof.started",),
        produces_event_type="proof.hermes.completed",
    )
    controller = _Controller("EVENT_REACTION_OK")
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [controller],
        bus,
        persona_provider=_PersonaProvider(persona),
    )
    await resident.sync()
    surfaced: list[object] = []
    outcomes: list[object] = []
    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], surfaced.append)
    await bus.subscribe([f"{prefix}.proof.hermes.completed"], outcomes.append)

    peer = resident._peers[runtime.id]
    await peer.mesh.publish(
        RavnEvent(
            type=RavnEventType.OUTCOME,
            source="coordinator",
            payload={
                "event_type": "proof.started",
                "persona": "event-source",
                "fields": {"summary": "start"},
            },
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="source-task",
            session_id="room-1",
            task_id="source-task",
            root_correlation_id="room-1",
        ),
        topic="proof.started",
    )
    await bus.flush()
    for _ in range(30):
        if controller.created and any(
            task.status == "complete" for task in resident._tasks.values()
        ):
            break
        await asyncio.sleep(0)
    await bus.flush()

    assert len(controller.created) == 1
    reaction_title = controller.created[0][0]
    assert reaction_title.startswith("React to proof.started (")
    assert len(reaction_title.rsplit("(", 1)[1].removesuffix(")")) == 8
    assert controller.created[0][1] == "niuu/qwen"
    assert "Event type: proof.started" in controller.connection.sent[0]["content"]
    assert len(surfaced) == 1
    assert surfaced[0].payload["ravn_event"]["text"] == "EVENT_REACTION_OK"
    assert surfaced[0].payload["ravn_session_id"] == "room-1"
    assert len(outcomes) == 1
    assert outcomes[0].payload["ravn_event"]["event_type"] == "proof.hermes.completed"
    assert outcomes[0].payload["ravn_root_correlation_id"] == "room-1"

    await resident.stop()


async def test_surface_task_without_assistant_output_surfaces_error() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id)
    controller = _Controller("")
    bus = InProcessBus()
    resident = ResidentFlockAdapter(_Repository(runtime), [controller], bus)
    await resident.sync()
    surfaced: list[object] = []

    async def capture_surface(event: object) -> None:
        surfaced.append(event)

    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], capture_surface)

    accepted = await resident._dispatch(
        runtime.id,
        {
            "task_id": "empty-task",
            "title": "Empty response proof",
            "output_mode": "surface",
            "session_id": "room-1",
            "root_correlation_id": "room-1",
        },
    )
    assert accepted == {"status": "accepted", "task_id": "empty-task"}
    task = resident._tasks["empty-task"]
    assert task.runner is not None
    await task.runner
    await bus.flush()

    assert task.status == "failed"
    assert task.error == "Resident task completed without an assistant response"
    assert len(surfaced) == 1
    assert surfaced[0].payload["ravn_type"] == "error"
    assert surfaced[0].payload["ravn_event"]["message"] == task.error
    assert surfaced[0].payload["ravn_session_id"] == "room-1"

    await resident.stop()
