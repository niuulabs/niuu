"""Operator autonomy commands actually change resident behavior (F7/F8)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.api.valkyries import create_valkyrie_router
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent
from tests.ravn.fixtures.fakes import BusRecorder, ManualClock


def _autonomy_event(mode: str, *, valkyrie_id: str = "valkyrie:k8s-b") -> SleipnirEvent:
    return SleipnirEvent(
        event_type=registry.VALKYRIE_AUTONOMY_CHANGED,
        source="test-operator",
        payload={
            "valkyrie_id": valkyrie_id,
            "environment_id": "cluster-b",
            "mode": mode,
            "operator_id": "human:jozef",
            "reason": "test",
        },
        summary="autonomy change",
        urgency=0.4,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
    )


async def _runtime(tmp_path) -> tuple[ResidentLearningRuntime, BusRecorder, InProcessBus]:
    skill_dir = tmp_path / "skills"
    skills = SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    runtime = ResidentLearningRuntime(
        identity=ResidentLearningIdentity(
            environment_id="cluster-b",
            valkyrie_id="valkyrie:k8s-b",
            domain="k8s",
            flock_ids=["flock:k8s-valkyries"],
            autonomy_mode="guarded",
        ),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
    )
    await runtime.start()
    return runtime, recorder, bus


async def test_autonomy_command_changes_resident_mode_and_confirms(tmp_path) -> None:
    runtime, recorder, bus = await _runtime(tmp_path)
    assert runtime.identity.autonomy_mode == "guarded"

    await bus.publish(_autonomy_event("autonomous"))
    await bus.flush()

    assert runtime.identity.autonomy_mode == "autonomous"
    updates = await recorder.of_type(registry.VALKYRIE_STATE_UPDATED)
    confirmation = next(
        event for event in updates if event.payload.get("autonomy_mode") == "autonomous"
    )
    assert confirmation.payload["previous_autonomy_mode"] == "guarded"
    assert confirmation.payload["operator_id"] == "human:jozef"
    await runtime.stop()


async def test_unknown_mode_and_wrong_target_are_ignored(tmp_path) -> None:
    runtime, _recorder, bus = await _runtime(tmp_path)

    await bus.publish(_autonomy_event("delegated"))  # not canonical
    await bus.publish(_autonomy_event("yolo", valkyrie_id="valkyrie:someone-else"))
    await bus.flush()

    assert runtime.identity.autonomy_mode == "guarded"
    await runtime.stop()


async def test_wakefulness_follows_live_identity_after_autonomy_change(tmp_path) -> None:
    runtime, _recorder, bus = await _runtime(tmp_path)
    machine = ResidentWakefulness(
        identity=runtime.identity,
        skills=runtime.skills,
        publisher=bus,
        resident_learning=runtime,
        clock=ManualClock(),
    )
    assert machine.identity.autonomy_mode == "guarded"

    await bus.publish(_autonomy_event("yolo"))
    await bus.flush()

    assert machine.identity.autonomy_mode == "yolo"
    await runtime.stop()


# ---------------------------------------------------------------------------
# API: command publishing + capability gates
# ---------------------------------------------------------------------------


class _DenyingRoom:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def require_capability(self, participant_id: str, capability: str) -> None:
        self.calls.append((participant_id, capability))
        raise HTTPException(status_code=403, detail="missing capability")


def _client(monkeypatch_env=None, **router_kwargs) -> TestClient:
    import os

    from tests.test_ravn.test_ravn_api import _valkyrie_catalog

    os.environ["RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON"] = _valkyrie_catalog()
    try:
        app = FastAPI()
        app.include_router(create_valkyrie_router(**router_kwargs))
        return TestClient(app)
    finally:
        os.environ.pop("RAVN_VALKYRIE_DASHBOARD_ENVIRONMENTS_JSON", None)


def test_autonomy_endpoint_publishes_real_command() -> None:
    published: list[SleipnirEvent] = []

    class _Publisher:
        async def publish(self, event: SleipnirEvent) -> None:
            published.append(event)

    from ravn.api.valkyries import ValkyrieLearningCommandPublisher

    client = _client(
        learning_command_publisher=ValkyrieLearningCommandPublisher(_Publisher()),
    )
    valkyrie_id = client.get("/api/v1/ravn/valkyrie/dashboard").json()["valkyries"][0]["id"]
    response = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={"valkyrieId": valkyrie_id, "mode": "yolo", "reason": "ship it"},
    )
    assert response.status_code == 200
    commands = [e for e in published if e.event_type == registry.VALKYRIE_AUTONOMY_CHANGED]
    assert len(commands) == 1
    assert commands[0].payload["mode"] == "yolo"
    assert commands[0].payload["valkyrie_id"] == valkyrie_id


def test_autonomy_endpoint_rejects_legacy_modes() -> None:
    client = _client()
    valkyrie_id = client.get("/api/v1/ravn/valkyrie/dashboard").json()["valkyries"][0]["id"]
    response = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={"valkyrieId": valkyrie_id, "mode": "delegated"},
    )
    assert response.status_code == 422


def test_autonomy_endpoint_enforces_change_autonomy_capability() -> None:
    room = _DenyingRoom()
    client = _client(room_client=room)
    valkyrie_id = client.get("/api/v1/ravn/valkyrie/dashboard").json()["valkyries"][0]["id"]

    anonymous = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={"valkyrieId": valkyrie_id, "mode": "yolo"},
    )
    assert anonymous.status_code == 403

    denied = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={"valkyrieId": valkyrie_id, "mode": "yolo", "participantId": "human:jozef"},
    )
    assert denied.status_code == 403
    assert ("human:jozef", "change_autonomy") in room.calls


def test_learning_decisions_enforce_approve_capability() -> None:
    room = _DenyingRoom()
    client = _client(room_client=room)

    # The capability gate fires before the learning lookup, so any id works.
    response = client.post(
        "/api/v1/ravn/valkyrie/learnings/learning-x/adopt",
        json={"learningId": "learning-x", "operatorId": "human:jozef"},
    )
    assert response.status_code == 403
    assert ("human:jozef", "approve") in room.calls
