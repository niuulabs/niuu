"""Resident session controllers participating in the Ravn mesh."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from niuu.domain.outcome import OutcomeField
from niuu.mesh import mesh_event_prefix
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
    def __init__(
        self,
        output: str = "alive",
        *,
        requires_approval: bool = False,
        emit_tool_frames: bool = False,
        result_only: bool = False,
        error: str = "",
        hang: bool = False,
    ) -> None:
        self.frames: asyncio.Queue[dict] = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False
        self.output = output
        self.requires_approval = requires_approval
        self.emit_tool_frames = emit_tool_frames
        self.result_only = result_only
        self.error = error
        self.hang = hang

    async def receive(self) -> dict:
        return await self.frames.get()

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)
        if self.hang:
            return
        if self.error:
            await self.frames.put({"type": "error", "error": self.error})
            return
        if frame.get("type") == "user" and self.requires_approval:
            await self.frames.put(
                {
                    "type": "control_request",
                    "request_id": "approval-1",
                    "tool": "terminal",
                }
            )
            return
        if self.emit_tool_frames:
            await self.frames.put(
                {
                    "type": "tool_start",
                    "data": "terminal",
                    "metadata": {
                        "tool_name": "terminal",
                        "input": {"command": "printf proof"},
                    },
                }
            )
            await self.frames.put(
                {
                    "type": "tool_result",
                    "data": "proof",
                    "metadata": {"tool_name": "terminal", "is_error": False},
                }
            )
        if not self.result_only:
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

    def __init__(
        self,
        output: str = "alive",
        *,
        requires_approval: bool = False,
        emit_tool_frames: bool = False,
        result_only: bool = False,
        error: str = "",
        hang: bool = False,
    ) -> None:
        self.connection = _Connection(
            output,
            requires_approval=requires_approval,
            emit_tool_frames=emit_tool_frames,
            result_only=result_only,
            error=error,
            hang=hang,
        )
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


class _FailingController(_Controller):
    async def connect_chat(self, runtime: ResidentRuntime, session_id: UUID) -> _Connection:
        raise RuntimeError("native resident unavailable")


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


async def test_persona_event_runs_native_reaction_and_publishes_response_and_outcome() -> None:
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
    controller = _Controller(output, requires_approval=True, emit_tool_frames=True)
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [controller],
        bus,
        persona_provider=provider,
    )
    await resident.sync()

    peer = resident._peers[runtime.id]
    assert peer.persona == persona
    assert provider.requests == [("user-a", "event-hermes")]

    surfaced: list[object] = []
    outcomes: list[object] = []
    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], surfaced.append)
    await bus.subscribe([f"{prefix}.proof.hermes.completed"], outcomes.append)

    event = RavnEvent(
        type=RavnEventType.OUTCOME,
        source="skuld:room-1",
        payload={
            "event_type": "proof.started",
            "prompt": "Return the proof outcome",
        },
        timestamp=datetime.now(UTC),
        urgency=0.3,
        correlation_id="source-event",
        session_id="room-1",
        task_id="source-event",
        root_correlation_id="room-1",
    )
    await peer.mesh.publish(event, topic="proof.started")
    await bus.flush()
    for _ in range(30):
        if resident._reactions and all(
            reaction.status == "complete" for reaction in resident._reactions.values()
        ):
            break
        await asyncio.sleep(0)
    await bus.flush()

    assert controller.connection.sent[0]["content"].startswith(
        "Act as the event Hermes.\n\nSubscribed event:\n"
    )
    assert "Message:\nReturn the proof outcome" in controller.connection.sent[0]["content"]
    assert controller.connection.sent[1] == {
        "type": "permission_response",
        "request_id": "approval-1",
        "behavior": "allowOnce",
    }
    assert [item.payload["ravn_type"] for item in surfaced] == [
        "task_started",
        "tool_start",
        "tool_result",
        "response",
    ]
    assert surfaced[0].payload["ravn_event"]["title"].startswith("React to proof.started")
    assert surfaced[1].payload["ravn_event"]["input"] == {"command": "printf proof"}
    assert surfaced[2].payload["ravn_event"]["result"] == "proof"
    assert surfaced[3].payload["ravn_event"]["text"] == output
    assert all(item.payload["ravn_session_id"] == "room-1" for item in surfaced)
    assert len(outcomes) == 1
    assert outcomes[0].payload["ravn_event"]["event_type"] == "proof.hermes.completed"
    assert outcomes[0].payload["ravn_event"]["fields"] == {"summary": "HERMES_EVENT_OK"}
    assert outcomes[0].payload["ravn_root_correlation_id"] == "room-1"

    await peer.mesh.publish(event, topic="proof.started")
    await bus.flush()
    assert len(controller.created) == 1
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
            reaction.status == "complete" for reaction in resident._reactions.values()
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
    assert [item.payload["ravn_type"] for item in surfaced] == ["task_started", "response"]
    assert surfaced[1].payload["ravn_event"]["text"] == "EVENT_REACTION_OK"
    assert surfaced[1].payload["ravn_session_id"] == "room-1"
    assert len(outcomes) == 1
    assert outcomes[0].payload["ravn_event"]["event_type"] == "proof.hermes.completed"
    assert outcomes[0].payload["ravn_root_correlation_id"] == "room-1"

    await resident.stop()


async def test_event_reaction_without_assistant_output_surfaces_error() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="React to subscribed proof events.",
        consumes_event_types=("proof.started",),
        produces_event_type="proof.hermes.completed",
    )
    controller = _Controller("")
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [controller],
        bus,
        persona_provider=_PersonaProvider(persona),
    )
    await resident.sync()
    surfaced: list[object] = []

    async def capture_surface(event: object) -> None:
        surfaced.append(event)

    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], capture_surface)

    peer = resident._peers[runtime.id]
    await peer.mesh.publish(
        RavnEvent(
            type=RavnEventType.OUTCOME,
            source="skuld:room-1",
            payload={"event_type": "proof.started", "prompt": "Return a response"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="source-event",
            session_id="room-1",
            task_id="source-event",
            root_correlation_id="room-1",
        ),
        topic="proof.started",
    )
    await bus.flush()
    reaction = next(iter(resident._reactions.values()))
    assert reaction.runner is not None
    await reaction.runner
    await bus.flush()

    assert reaction.status == "failed"
    assert reaction.error == "Resident reaction completed without an assistant response"
    assert [item.payload["ravn_type"] for item in surfaced] == ["task_started", "error"]
    assert surfaced[1].payload["ravn_event"]["message"] == reaction.error
    assert surfaced[1].payload["ravn_session_id"] == "room-1"

    await resident.stop()


async def test_event_reaction_failure_surfaces_correlated_error() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="React to subscribed proof events.",
        consumes_event_types=("proof.started",),
        produces_event_type="proof.hermes.completed",
    )
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [_FailingController()],
        bus,
        persona_provider=_PersonaProvider(persona),
    )
    await resident.sync()
    surfaced: list[object] = []
    prefix = mesh_event_prefix(str(flock_id))
    await bus.subscribe([f"{prefix}.activity.hermes_worker"], surfaced.append)

    peer = resident._peers[runtime.id]
    await peer.mesh.publish(
        RavnEvent(
            type=RavnEventType.OUTCOME,
            source="skuld:room-1",
            payload={"event_type": "proof.started", "prompt": "Return a response"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="source-event",
            session_id="room-1",
            task_id="source-event",
            root_correlation_id="room-1",
        ),
        topic="proof.started",
    )
    await bus.flush()
    reaction = next(iter(resident._reactions.values()))
    assert reaction.runner is not None
    await reaction.runner
    await bus.flush()

    assert reaction.status == "failed"
    assert reaction.error == "native resident unavailable"
    assert [item.payload["ravn_type"] for item in surfaced] == ["task_started", "error"]
    assert surfaced[1].payload["ravn_event"]["message"] == reaction.error
    assert surfaced[1].payload["ravn_root_correlation_id"] == "room-1"

    await resident.stop()


async def test_sync_reuses_replaces_and_removes_resident_peers() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="",
        consumes_event_types=("proof.started",),
    )
    repository = _Repository(runtime)
    resident = ResidentFlockAdapter(
        repository,
        [_Controller()],
        InProcessBus(),
        persona_provider=_PersonaProvider(persona),
    )

    await resident.sync()
    first = resident._peers[runtime.id]
    await resident.sync()
    assert resident._peers[runtime.id] is first

    repository.runtime = runtime.model_copy(update={"persona_name": "missing"})
    await resident.sync()
    replacement = resident._peers[runtime.id]
    assert replacement is not first
    assert replacement.persona is None

    repository.runtime = runtime.model_copy(
        update={"observed_state": ResidentObservedState.FAILED}
    )
    await resident.sync()
    assert resident._peers == {}
    await resident.stop()


async def test_self_and_missing_runtime_events_do_not_create_reactions() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="",
        consumes_event_types=("proof.started",),
    )
    repository = _Repository(runtime)
    resident = ResidentFlockAdapter(
        repository,
        [_Controller()],
        InProcessBus(),
        persona_provider=_PersonaProvider(persona),
    )
    event = RavnEvent(
        type=RavnEventType.OUTCOME,
        source=runtime.flock_peer_id,
        payload={"event_type": "proof.started"},
        timestamp=datetime.now(UTC),
        urgency=0.3,
        correlation_id="self-event",
        session_id="room-1",
    )

    await resident._handle_persona_event(runtime.id, event)
    repository.runtime = runtime.model_copy(update={"id": uuid4()})
    await resident._handle_persona_event(runtime.id, replace(event, source="peer"))

    assert resident._reactions == {}


async def test_result_only_and_error_frames_surface_native_engine_results() -> None:
    async def run(controller: _Controller):
        flock_id = uuid4()
        runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
        persona = SessionPersona(
            name="event-hermes",
            system_prompt="",
            consumes_event_types=("proof.started",),
        )
        bus = InProcessBus()
        resident = ResidentFlockAdapter(
            _Repository(runtime),
            [controller],
            bus,
            persona_provider=_PersonaProvider(persona),
        )
        await resident.sync()
        surfaced: list[object] = []
        prefix = mesh_event_prefix(str(flock_id))
        await bus.subscribe([f"{prefix}.activity.hermes_worker"], surfaced.append)
        await resident._peers[runtime.id].mesh.publish(
            RavnEvent(
                type=RavnEventType.OUTCOME,
                source="coordinator",
                payload={"event_type": "proof.started"},
                timestamp=datetime.now(UTC),
                urgency=0.3,
                correlation_id="source-event",
                session_id="room-1",
                root_correlation_id="room-1",
            ),
            topic="proof.started",
        )
        await bus.flush()
        reaction = next(iter(resident._reactions.values()))
        assert reaction.runner is not None
        await reaction.runner
        await bus.flush()
        await resident.stop()
        return reaction, surfaced

    completed, completed_events = await run(_Controller("RESULT_ONLY_OK", result_only=True))
    failed, failed_events = await run(_Controller(error="native engine failed"))

    assert completed.status == "complete"
    assert completed.output == "RESULT_ONLY_OK"
    assert [event.payload["ravn_type"] for event in completed_events] == [
        "task_started",
        "response",
    ]
    assert failed.status == "failed"
    assert failed.error == "native engine failed"
    assert [event.payload["ravn_type"] for event in failed_events] == [
        "task_started",
        "error",
    ]


async def test_stop_cancels_running_native_reaction() -> None:
    flock_id = uuid4()
    runtime = _runtime(flock_id=flock_id, persona_name="event-hermes")
    persona = SessionPersona(
        name="event-hermes",
        system_prompt="",
        consumes_event_types=("proof.started",),
    )
    bus = InProcessBus()
    resident = ResidentFlockAdapter(
        _Repository(runtime),
        [_Controller(hang=True)],
        bus,
        persona_provider=_PersonaProvider(persona),
    )
    await resident.sync()
    await resident._peers[runtime.id].mesh.publish(
        RavnEvent(
            type=RavnEventType.OUTCOME,
            source="coordinator",
            payload={"event_type": "proof.started"},
            timestamp=datetime.now(UTC),
            urgency=0.3,
            correlation_id="source-event",
            session_id="room-1",
        ),
        topic="proof.started",
    )
    await bus.flush()
    reaction = next(iter(resident._reactions.values()))
    for _ in range(20):
        if reaction.status == "running":
            break
        await asyncio.sleep(0)

    await resident.stop()

    assert reaction.status == "cancelled"
    assert reaction.connection is None
