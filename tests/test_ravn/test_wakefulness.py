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
