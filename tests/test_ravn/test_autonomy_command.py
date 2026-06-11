"""Operator autonomy decisions ride the unified ODIN review path (F7/F8)."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.api.valkyries import create_valkyrie_router
from ravn.odin.review import ReviewItem, ReviewKind, review_decided_event
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


def _autonomy_decided_event(
    mode: str,
    *,
    valkyrie_id: str = "valkyrie:k8s-b",
    decision: str = "approved",
) -> SleipnirEvent:
    item = ReviewItem.new(
        kind=ReviewKind.AUTONOMY_CHANGE.value,
        requested_action="set_autonomy_mode",
        environment_id="cluster-b",
        valkyrie_id=valkyrie_id,
        title=f"Set {valkyrie_id} autonomy to {mode}",
        summary="test",
        evidence={"mode": mode},
        requested_by="human:jozef",
    )
    item.decide(decision=decision, operator_id="human:jozef", reason="test")
    return review_decided_event(item, source="test-operator")


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


async def test_autonomy_decision_changes_resident_mode_and_confirms(tmp_path) -> None:
    runtime, recorder, bus = await _runtime(tmp_path)
    assert runtime.identity.autonomy_mode == "guarded"

    await bus.publish(_autonomy_decided_event("autonomous"))
    await bus.flush()

    assert runtime.identity.autonomy_mode == "autonomous"
    updates = await recorder.of_type(registry.VALKYRIE_STATE_UPDATED)
    confirmation = next(
        event for event in updates if event.payload.get("autonomy_mode") == "autonomous"
    )
    assert confirmation.payload["previous_autonomy_mode"] == "guarded"
    assert confirmation.payload["operator_id"] == "human:jozef"

    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    assert len(resolved) == 1
    assert resolved[0].payload["apply_outcome"] == "applied"
    assert resolved[0].payload["status"] == "applied"
    await runtime.stop()


async def test_rejected_autonomy_decision_keeps_mode(tmp_path) -> None:
    runtime, recorder, bus = await _runtime(tmp_path)

    await bus.publish(_autonomy_decided_event("yolo", decision="rejected"))
    await bus.flush()

    assert runtime.identity.autonomy_mode == "guarded"
    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    assert len(resolved) == 1
    assert resolved[0].payload["apply_outcome"] == "applied"
    assert resolved[0].payload["status"] == "rejected"
    await runtime.stop()


async def test_unknown_mode_and_wrong_target_are_not_applied(tmp_path) -> None:
    runtime, recorder, bus = await _runtime(tmp_path)

    await bus.publish(_autonomy_decided_event("delegated"))  # not canonical
    await bus.publish(_autonomy_decided_event("yolo", valkyrie_id="valkyrie:someone-else"))
    await bus.flush()

    assert runtime.identity.autonomy_mode == "guarded"
    resolved = await recorder.of_type(registry.ODIN_REVIEW_RESOLVED)
    # The non-canonical mode fails loudly; the wrong-target item is ignored.
    assert len(resolved) == 1
    assert resolved[0].payload["apply_outcome"] == "apply_failed"
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

    await bus.publish(_autonomy_decided_event("yolo"))
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


def test_autonomy_endpoint_publishes_decided_review_item() -> None:
    published: list[SleipnirEvent] = []

    class _Publisher:
        async def publish(self, event: SleipnirEvent) -> None:
            published.append(event)

    from ravn.api.valkyries import OdinReviewCommandPublisher

    client = _client(
        review_command_publisher=OdinReviewCommandPublisher(_Publisher()),
    )
    valkyrie_id = client.get("/api/v1/ravn/valkyrie/dashboard").json()["valkyries"][0]["id"]
    response = client.post(
        "/api/v1/ravn/valkyrie/autonomy",
        json={"valkyrieId": valkyrie_id, "mode": "yolo", "reason": "ship it"},
    )
    assert response.status_code == 200
    commands = [e for e in published if e.event_type == registry.ODIN_REVIEW_DECIDED]
    assert len(commands) == 1
    payload = commands[0].payload
    assert payload["kind"] == ReviewKind.AUTONOMY_CHANGE.value
    assert payload["status"] == "approved"
    assert payload["evidence"]["mode"] == "yolo"
    assert payload["valkyrie_id"] == valkyrie_id
    assert payload["requested_capability"] == "change_autonomy"


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
