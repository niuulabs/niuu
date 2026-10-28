"""Wakefulness state machine and scheduled consolidation dreams (NIU-1040)."""

from __future__ import annotations

import asyncio
import json

import pytest

from ravn.adapters.skill.file_registry import FileSkillRegistry
from ravn.skills.management import SkillManagementRegistry
from ravn.valkyrie_evolution.resident_learning import (
    ResidentLearningIdentity,
    ResidentLearningRuntime,
)
from ravn.valkyrie_evolution.wakefulness import ResidentWakefulness
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from tests.ravn.fixtures.fakes import BusRecorder, ManualClock


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
) -> tuple[ResidentWakefulness, BusRecorder, ManualClock]:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    clock = ManualClock()
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


async def _transitions(recorder: BusRecorder) -> list[tuple[str, str]]:
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


async def test_dream_holds_promotion_for_skills_implicated_by_feedback(tmp_path) -> None:
    """Failure feedback naming a skill blocks its automatic promotion (F3)."""
    from datetime import UTC, datetime

    from ravn.domain.models import Episode, EpisodeMatch, Outcome

    skills = _skills(tmp_path)
    await skills.create(
        name="suspect-probe",
        content="# skill: suspect-probe\n\nmetadata:\n  capability: x\n",
        scope="private",
    )
    for _ in range(2):
        await skills.record_usage("suspect-probe", success=True)

    class _FeedbackMemory:
        async def query_episodes(self, query, *, limit, min_relevance):
            episode = Episode(
                episode_id="feedback:f-1",
                session_id="feedback:cluster-a",
                timestamp=datetime.now(UTC),
                summary="bad action",
                task_description="capture feedback",
                tools_used=[],
                outcome=Outcome.FAILURE,
                tags=["valkyrie-feedback", "environment:cluster-a"],
                structured_outcome={
                    "kind": "environment_feedback",
                    "environment_id": "cluster-a",
                    "feedback_type": "bad_action",
                    "correction": {"skill_name": "suspect-probe"},
                },
                outcome_valid=True,
            )
            return [EpisodeMatch(episode=episode, relevance=1.0)]

    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    machine = ResidentWakefulness(
        identity=_identity("autonomous"),
        skills=skills,
        publisher=bus,
        memory=_FeedbackMemory(),
        promote_min_successes=2,
        clock=ManualClock(),
    )
    summary = await machine.dream()

    assert summary["promoted"] == []
    assert summary["promotion_candidates"] == ["suspect-probe"]
    assert summary["feedback"]["negative"] == 1
    assert summary["feedback"]["implicated_skills"] == ["suspect-probe"]
    assert (await skills.show("suspect-probe"))["metadata"]["scope"] == "private"


async def test_dream_tallies_delivery_and_positive_feedback(tmp_path) -> None:
    """Snooze/escalate count as delivery, other feedback as positive (F3)."""
    from datetime import UTC, datetime

    from ravn.domain.models import Episode, EpisodeMatch, Outcome

    def _episode(feedback_type: str, environment_id: str = "cluster-a") -> EpisodeMatch:
        episode = Episode(
            episode_id=f"feedback:{feedback_type}",
            session_id="feedback:cluster-a",
            timestamp=datetime.now(UTC),
            summary="fb",
            task_description="capture feedback",
            tools_used=[],
            outcome=Outcome.SUCCESS,
            tags=["valkyrie-feedback"],
            structured_outcome={
                "kind": "environment_feedback",
                "environment_id": environment_id,
                "feedback_type": feedback_type,
            },
            outcome_valid=True,
        )
        return EpisodeMatch(episode=episode, relevance=1.0)

    class _FeedbackMemory:
        async def query_episodes(self, query, *, limit, min_relevance):
            return [
                _episode("snooze"),
                _episode("escalate"),
                _episode("acknowledged"),
                _episode("snooze", environment_id="other-env"),  # filtered out
            ]

    machine, _recorder, _clock = await _machine(tmp_path)
    machine._memory = _FeedbackMemory()
    summary = await machine.dream()

    assert summary["feedback"]["delivery"] == 2
    assert summary["feedback"]["positive"] == 1
    assert summary["feedback"]["negative"] == 0


async def test_guarded_promotion_candidates_become_review_items(tmp_path) -> None:
    """Held promotions go to the operator on the unified review path."""
    from ravn.odin.review import JsonReviewStore, ReviewRequester

    skills = _skills(tmp_path)
    await skills.create(
        name="proven-probe",
        content="# skill: proven-probe\n\nmetadata:\n  capability: x\n",
        scope="private",
    )
    for _ in range(2):
        await skills.record_usage("proven-probe", success=True)

    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    requester = ReviewRequester(
        publisher=bus,
        store=JsonReviewStore(tmp_path / "review_outbox.json"),
        source="valkyrie:k8s-a",
    )
    machine = ResidentWakefulness(
        identity=_identity("guarded"),
        skills=skills,
        publisher=bus,
        review_requester=requester,
        promote_min_successes=2,
        clock=ManualClock(),
    )
    summary = await machine.dream()
    assert summary["promotion_candidates"] == ["proven-probe"]

    requested = await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)
    assert len(requested) == 1
    assert requested[0].payload["kind"] == "skill_promotion"
    assert requested[0].payload["evidence"]["skill_name"] == "proven-probe"

    reloaded = JsonReviewStore(tmp_path / "review_outbox.json")
    pending = reloaded.list(status="pending", kind="skill_promotion")
    assert len(pending) == 1
    assert pending[0].title == "proven-probe"
    assert pending[0].environment_id == "cluster-a"

    # A second dream must not file a duplicate while the first is pending.
    await machine.dream()
    assert len(reloaded.list(kind="skill_promotion")) == 1
    assert len(await recorder.of_type(registry.ODIN_REVIEW_REQUESTED)) == 1


# ---------------------------------------------------------------------------
# Persisted dream time: a restart does not postpone the next dream
# ---------------------------------------------------------------------------


def _persisted_machine(tmp_path, *, wall_now: float) -> tuple[ResidentWakefulness, ManualClock]:
    clock = ManualClock()
    machine = ResidentWakefulness(
        identity=_identity(),
        skills=_skills(tmp_path),
        publisher=InProcessBus(),
        dream_interval_seconds=100.0,
        dream_min_idle_seconds=20.0,
        clock=clock,
        state_path=tmp_path / "state" / "consolidation_dream_state.json",
        wall_clock=lambda: wall_now,
    )
    return machine, clock


async def test_resident_with_no_recorded_dream_dreams_at_the_first_idle_tick(tmp_path) -> None:
    machine, clock = _persisted_machine(tmp_path, wall_now=1_000.0)
    await machine._transition("watching", reason="test start")

    clock.advance(21.0)
    await machine.tick()

    assert (tmp_path / "state" / "consolidation_dream_state.json").exists()
    assert machine.state == "watching"
    saved = (tmp_path / "state" / "consolidation_dream_state.json").read_text()
    assert '"last_dream_at": 1000.0' in saved


async def test_restart_keeps_counting_from_the_recorded_dream(tmp_path) -> None:
    state = tmp_path / "state" / "consolidation_dream_state.json"
    state.parent.mkdir()
    state.write_text('{"last_dream_at": 1000.0}', encoding="utf-8")

    # Restarted 90s after the last dream: due 10s later, not a full interval later.
    machine, clock = _persisted_machine(tmp_path, wall_now=1_090.0)
    await machine._transition("watching", reason="test start")
    clock.advance(9.0)
    machine.notify_activity()
    clock.advance(21.0)
    await machine.tick()

    assert '"last_dream_at": 1090.0' in state.read_text()


async def test_restart_soon_after_a_dream_does_not_dream_again(tmp_path) -> None:
    state = tmp_path / "state" / "consolidation_dream_state.json"
    state.parent.mkdir()
    state.write_text('{"last_dream_at": 1000.0}', encoding="utf-8")

    machine, clock = _persisted_machine(tmp_path, wall_now=1_010.0)
    await machine._transition("watching", reason="test start")
    clock.advance(21.0)
    await machine.tick()

    assert state.read_text() == '{"last_dream_at": 1000.0}'


def test_unreadable_dream_state_raises_with_remedy(tmp_path) -> None:
    state = tmp_path / "state" / "consolidation_dream_state.json"
    state.parent.mkdir()
    state.write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="delete it to dream at the next idle minute"):
        _persisted_machine(tmp_path, wall_now=1_000.0)


# ---------------------------------------------------------------------------
# Dream outcome: published, persisted, and republished as a snapshot
# ---------------------------------------------------------------------------


def _reporting_machine(
    tmp_path, *, bus: InProcessBus, wall_now: list[float], tool_curator=None
) -> tuple[ResidentWakefulness, ManualClock]:
    clock = ManualClock()
    machine = ResidentWakefulness(
        identity=_identity(),
        skills=_skills(tmp_path),
        publisher=bus,
        tool_curator=tool_curator,
        dream_interval_seconds=100.0,
        dream_min_idle_seconds=20.0,
        dream_snapshot_interval_seconds=50.0,
        clock=clock,
        state_path=tmp_path / "state" / "consolidation_dream_state.json",
        wall_clock=lambda: wall_now[0],
    )
    return machine, clock


class _RetiringToolCurator:
    def __init__(self, clock: list[ManualClock]) -> None:
        self._clock = clock

    async def curate(self) -> dict:
        self._clock[0].advance(42.5)
        return {
            "installed_before": 3,
            "retired": [{"name": "probe_v1", "reason": "superseded", "detail": "v2"}],
            "kept": 2,
        }


class _FailingToolCurator:
    async def curate(self) -> dict:
        raise RuntimeError("tool archive unwritable")


async def test_completed_dream_reports_timing_and_persists_its_outcome(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    clocks: list[ManualClock] = []
    machine, clock = _reporting_machine(
        tmp_path, bus=bus, wall_now=[1_000.0], tool_curator=_RetiringToolCurator(clocks)
    )
    clocks.append(clock)

    await machine.dream()

    started = (await recorder.of_type("valkyrie.dream.started"))[0].payload
    completed = (await recorder.of_type("valkyrie.dream.completed"))[0].payload
    assert started["started_at"] == "1970-01-01T00:16:40+00:00"
    assert completed["status"] == "completed"
    assert completed["dream_id"] == started["dream_id"]
    assert completed["started_at"] == started["started_at"]
    assert completed["duration_seconds"] == 42.5
    assert completed["error"] == ""
    assert completed["learned_tools"]["retired"][0]["name"] == "probe_v1"

    state = json.loads((tmp_path / "state" / "consolidation_dream_state.json").read_text())
    assert state["last_dream"] == machine.last_dream
    assert completed == {
        "environment_id": "cluster-a",
        "environment_type": "k8s",
        "valkyrie_id": "valkyrie:k8s-a",
        **machine.last_dream,
    }


async def test_failed_dream_is_published_with_its_error_and_reraised(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    machine, _clock = _reporting_machine(
        tmp_path, bus=bus, wall_now=[1_000.0], tool_curator=_FailingToolCurator()
    )
    await machine._transition("watching", reason="test start")

    with pytest.raises(RuntimeError, match="tool archive unwritable"):
        await machine.dream()

    failed = await recorder.of_type("valkyrie.dream.failed")
    assert len(failed) == 1
    assert failed[0].payload["status"] == "failed"
    assert failed[0].payload["error"] == "RuntimeError: tool archive unwritable"
    assert not await recorder.of_type("valkyrie.dream.completed")
    assert machine.state == "watching"
    state = json.loads((tmp_path / "state" / "consolidation_dream_state.json").read_text())
    assert state["last_dream"]["status"] == "failed"


async def test_start_snapshot_reports_no_dream_then_the_restored_outcome(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    machine, _clock = _reporting_machine(tmp_path, bus=bus, wall_now=[1_000.0])
    await machine.start()
    await machine.stop()
    first = await recorder.of_type("valkyrie.dream.snapshot")
    assert first[0].payload["last_dream"] is None
    assert first[0].payload["valkyrie_id"] == "valkyrie:k8s-a"

    await machine.dream()
    outcome = machine.last_dream

    # A restarted resident republishes the outcome it persisted.
    restarted, _clock = _reporting_machine(tmp_path, bus=bus, wall_now=[1_010.0])
    assert restarted.last_dream == outcome
    await restarted.start()
    await restarted.stop()
    snapshots = await recorder.of_type("valkyrie.dream.snapshot")
    assert snapshots[-1].payload["last_dream"] == outcome
    assert snapshots[-1].correlation_id == outcome["dream_id"]


async def test_snapshot_is_republished_once_its_interval_elapses(tmp_path) -> None:
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    machine, clock = _reporting_machine(tmp_path, bus=bus, wall_now=[1_000.0])

    await machine._publish_due_dream_snapshot()
    clock.advance(49.0)
    await machine._publish_due_dream_snapshot()
    assert len(await recorder.of_type("valkyrie.dream.snapshot")) == 1

    clock.advance(1.0)
    await machine._publish_due_dream_snapshot()
    assert len(await recorder.of_type("valkyrie.dream.snapshot")) == 2


def test_dream_state_with_a_non_object_outcome_raises_with_remedy(tmp_path) -> None:
    state = tmp_path / "state" / "consolidation_dream_state.json"
    state.parent.mkdir()
    state.write_text('{"last_dream_at": 1000.0, "last_dream": "done"}', encoding="utf-8")

    with pytest.raises(ValueError, match="delete it to dream at the next idle minute"):
        _persisted_machine(tmp_path, wall_now=1_000.0)


class _BlockingToolCurator:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def curate(self) -> dict:
        self.entered.set()
        await asyncio.Event().wait()
        return {}


async def test_cancelled_dream_is_not_recorded_as_a_dream(tmp_path) -> None:
    state = tmp_path / "state" / "consolidation_dream_state.json"
    state.parent.mkdir()
    state.write_text('{"last_dream_at": 1000.0, "last_dream": null}', encoding="utf-8")
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    curator = _BlockingToolCurator()
    machine, _clock = _reporting_machine(
        tmp_path, bus=bus, wall_now=[2_000.0], tool_curator=curator
    )
    await machine._transition("watching", reason="test start")

    task = asyncio.create_task(machine.dream())
    await curator.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The interrupted pass did not count: the persisted time is unchanged, so a
    # restart finds the dream still due.
    assert state.read_text() == '{"last_dream_at": 1000.0, "last_dream": null}'
    assert machine.last_dream is None
    assert machine.state == "watching"
    assert not await recorder.of_type("valkyrie.dream.completed")
    assert not await recorder.of_type("valkyrie.dream.failed")
    restarted, _clock = _reporting_machine(tmp_path, bus=bus, wall_now=[2_000.0])
    assert restarted._clock() - restarted._last_dream_at >= 100.0
