"""Wakefulness state machine and scheduled consolidation dreams (NIU-1040)."""

from __future__ import annotations

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Recorder:
    def __init__(self, bus: InProcessBus) -> None:
        self._bus = bus
        self.events: list[SleipnirEvent] = []

    async def __call__(self, event: SleipnirEvent) -> None:
        self.events.append(event)

    async def of_type(self, event_type: str) -> list[SleipnirEvent]:
        await self._bus.flush()
        return [event for event in self.events if event.event_type == event_type]


def _identity(autonomy_mode: str = "autonomous") -> ResidentLearningIdentity:
    return ResidentLearningIdentity(
        environment_id="cluster-a",
        valkyrie_id="valkyrie:k8s-a",
        domain="k8s",
        flock_ids=["flock:k8s-valkyries"],
        autonomy_mode=autonomy_mode,
        environment_type="k8s",
    )


def _skills(tmp_path) -> SkillManagementRegistry:
    skill_dir = tmp_path / "skills"
    return SkillManagementRegistry(
        FileSkillRegistry(skill_dirs=[str(skill_dir)], write_dir=skill_dir, include_builtin=False),
        metadata_path=tmp_path / "skill_management.json",
    )


async def _machine(
    tmp_path,
    *,
    autonomy_mode: str = "autonomous",
    skills: SkillManagementRegistry | None = None,
    resident_learning: ResidentLearningRuntime | None = None,
) -> tuple[ResidentWakefulness, _Recorder, _Clock]:
    bus = InProcessBus()
    recorder = _Recorder(bus)
    await bus.subscribe(["*"], recorder)
    clock = _Clock()
    machine = ResidentWakefulness(
        identity=_identity(autonomy_mode),
        skills=skills or _skills(tmp_path),
        publisher=bus,
        resident_learning=resident_learning,
        tick_interval_seconds=1.0,
        wakeful_window_seconds=10.0,
        dream_interval_seconds=100.0,
        dream_min_idle_seconds=20.0,
        stale_skill_age_seconds=3600.0,
        promote_min_successes=2,
        clock=clock,
    )
    return machine, recorder, clock


async def _transitions(recorder: _Recorder) -> list[tuple[str, str]]:
    events = await recorder.of_type(registry.VALKYRIE_STATE_CHANGED)
    return [(e.payload["previous_state"], e.payload["new_state"]) for e in events]


# ---------------------------------------------------------------------------
# State machine transitions
# ---------------------------------------------------------------------------


async def test_activity_drives_wakeful_and_idle_returns_to_watching(tmp_path) -> None:
    machine, recorder, clock = await _machine(tmp_path)
    await machine._transition("watching", reason="test start")
    assert machine.state == "watching"

    machine.notify_activity()
    await machine.tick()
    assert machine.state == "wakeful"

    clock.advance(11.0)
    await machine.tick()
    assert machine.state == "watching"

    transitions = await _transitions(recorder)
    assert ("watching", "wakeful") in transitions
    assert ("wakeful", "watching") in transitions


async def test_dream_fires_on_schedule_only_when_idle(tmp_path) -> None:
    machine, recorder, clock = await _machine(tmp_path)
    await machine._transition("watching", reason="test start")

    # Dream interval elapsed but the resident is busy: no dream.
    clock.advance(150.0)
    machine.notify_activity()
    await machine.tick()
    assert machine.state == "wakeful"
    assert not await recorder.of_type("valkyrie.dream.completed")

    # Once idle long enough, the due dream runs and returns to watching.
    clock.advance(30.0)
    await machine.tick()
    assert machine.state == "watching"
    completed = await recorder.of_type("valkyrie.dream.completed")
    assert len(completed) == 1
    assert completed[0].payload["dream_kind"] == "consolidation"

    transitions = await _transitions(recorder)
    assert ("wakeful", "dreaming") in transitions
    assert ("dreaming", "watching") in transitions


async def test_start_and_stop_publish_lifecycle_transitions(tmp_path) -> None:
    machine, recorder, _clock = await _machine(tmp_path)
    await machine.start()
    assert machine.state == "watching"
    await machine.stop()
    assert machine.state == "sleeping"
    transitions = await _transitions(recorder)
    assert ("sleeping", "watching") in transitions
    assert ("watching", "sleeping") in transitions


# ---------------------------------------------------------------------------
# Consolidation dream
# ---------------------------------------------------------------------------


async def test_dream_promotes_successful_private_skills_when_policy_allows(tmp_path) -> None:
    skills = _skills(tmp_path)
    await skills.create(
        name="proven-probe",
        content="# skill: proven-probe\n\nmetadata:\n  capability: x\n",
        scope="private",
    )
    for _ in range(2):
        await skills.record_usage("proven-probe", success=True)

    machine, recorder, _clock = await _machine(tmp_path, skills=skills)
    summary = await machine.dream()

    assert summary["promoted"] == ["proven-probe"]
    lifecycle = (await skills.show("proven-probe"))["metadata"]
    assert lifecycle["scope"] == "environment"
    assert lifecycle["environment_id"] == "cluster-a"

    promotions = await recorder.of_type(registry.LEARNING_PROMOTED)
    assert len(promotions) == 1
    assert promotions[0].payload["from_scope"] == "private"
    assert promotions[0].payload["to_scope"] == "environment"


async def test_guarded_mode_keeps_promotions_as_candidates(tmp_path) -> None:
    skills = _skills(tmp_path)
    await skills.create(
        name="proven-probe",
        content="# skill: proven-probe\n\nmetadata:\n  capability: x\n",
        scope="private",
    )
    for _ in range(2):
        await skills.record_usage("proven-probe", success=True)

    machine, recorder, _clock = await _machine(tmp_path, autonomy_mode="guarded", skills=skills)
    summary = await machine.dream()

    assert summary["promoted"] == []
    assert summary["promotion_candidates"] == ["proven-probe"]
    assert (await skills.show("proven-probe"))["metadata"]["scope"] == "private"
    assert not await recorder.of_type(registry.LEARNING_PROMOTED)


async def test_dream_marks_long_unused_skills_stale(tmp_path) -> None:
    skills = _skills(tmp_path)
    await skills.create(
        name="dusty-probe",
        content="# skill: dusty-probe\n\nmetadata:\n  capability: y\n",
        scope="environment",
    )
    meta = (await skills.show("dusty-probe"))["metadata"]
    # Backdate last use beyond the stale window.
    lifecycle = skills._metadata_for_name("dusty-probe")
    lifecycle.last_used_at = "2020-01-01T00:00:00+00:00"
    skills._save()
    assert meta["status"] == "active"

    machine, _recorder, _clock = await _machine(tmp_path, skills=skills)
    summary = await machine.dream()

    assert summary["marked_stale"] == ["dusty-probe"]
    assert (await skills.show("dusty-probe"))["metadata"]["status"] == "stale"


async def test_dream_reopens_unresolved_capability_gaps(tmp_path) -> None:
    skills = _skills(tmp_path)
    bus = InProcessBus()
    learner = ResidentLearningRuntime(
        identity=_identity("guarded"),
        skills=skills,
        publisher=bus,
        subscriber=bus,
        tools_dir=tmp_path / "tools",
    )
    # Guarded mode defers the gap and parks the capability.
    from ravn.valkyrie_evolution.models import OperationalSignal

    deferred = await learner.process_signal(
        OperationalSignal(
            signal_id="sig-1",
            event_type="signal.kubernetes.event",
            environment_id="cluster-a",
            domain="k8s",
            severity="warning",
            summary="Pod OOMKilled",
            payload={"kind": "Pod", "reason": "OOMKilled"},
        )
    )
    assert deferred["decision"] == "defer_and_request_capability"

    machine, _recorder, _clock = await _machine(tmp_path, skills=skills, resident_learning=learner)
    summary = await machine.dream()
    assert summary["capability_gaps_reopened"] == 1
