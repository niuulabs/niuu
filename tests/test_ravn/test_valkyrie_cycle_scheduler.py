"""Tests for resident Valkyrie wakefulness and dream cycles."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ravn.adapters.triggers.valkyrie_cycle import (
    ValkyrieCycleConfig,
    ValkyrieCycleScheduler,
)
from ravn.context.autonomy import JsonProposalStore, ProposalStatus
from ravn.context.evolution import (
    PatternExtractor,
    PromptEvolution,
    SkillSuggestion,
    StrategyInjection,
    SystemWarning,
)
from ravn.domain.environment import k8s_environment_fixture
from ravn.domain.models import Episode, EpisodeMatch, Outcome, SessionSummary, SharedContext
from ravn.ports.memory import MemoryPort
from ravn.ports.tool import ToolPort
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent


class _FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 6, 3, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class _Memory(MemoryPort):
    def __init__(self, episodes: list[Episode]) -> None:
        self.episodes = episodes
        self.shared_context: SharedContext | None = None

    async def record_episode(self, episode: Episode) -> None:
        self.episodes.append(episode)

    async def query_episodes(
        self,
        query: str,
        *,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[EpisodeMatch]:
        terms = [term.lower() for term in query.split() if term.strip()]
        matches = []
        for episode in self.episodes:
            haystack = " ".join(
                [
                    episode.summary,
                    episode.task_description,
                    " ".join(episode.tags),
                    " ".join(episode.tools_used),
                    " ".join(episode.errors),
                ]
            ).lower()
            if not terms or any(term in haystack for term in terms):
                matches.append(EpisodeMatch(episode=episode, relevance=1.0))
        return matches[:limit]

    async def prefetch(self, context: str) -> str:
        return context

    async def search_sessions(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[SessionSummary]:
        return []

    def inject_shared_context(self, context: SharedContext) -> None:
        self.shared_context = context

    def get_shared_context(self) -> SharedContext | None:
        return self.shared_context

    def extra_tools(self, session_id: str) -> list[ToolPort]:
        return []

    async def count_episodes(self) -> int:
        return len(self.episodes)


def _evolution() -> PromptEvolution:
    return PromptEvolution(
        extracted_at=datetime(2026, 6, 3, 12, 5, tzinfo=UTC),
        episodes_analyzed=5,
        outcomes_analyzed=1,
        suggested_skills=[
            SkillSuggestion(
                tool_pattern=("kubectl", "mimir_write"),
                description="Probe pod events and record the useful evidence.",
                source_episode_ids=["ep-1", "ep-2", "ep-3"],
                occurrence_count=3,
            )
        ],
        system_warnings=[
            SystemWarning(
                warning_text="Never mutate global doctrine during resident dreams.",
                source_outcome_ids=["ep-4"],
                occurrence_count=1,
            )
        ],
        strategy_injections=[
            StrategyInjection(
                task_type="k8s",
                strategy_text="Check Kubernetes events before restarting a deployment.",
                source_episode_ids=["ep-5"],
                success_count=3,
            )
        ],
    )


async def _provider() -> PromptEvolution:
    return _evolution()


async def _record(events: list[SleipnirEvent], event: SleipnirEvent) -> None:
    events.append(event)


def _scheduler(
    *,
    bus: InProcessBus,
    tmp_path,
    clock: _FakeClock,
    autonomy_mode: str = "guarded",
    provider=_provider,
) -> ValkyrieCycleScheduler:
    environment = k8s_environment_fixture()
    return ValkyrieCycleScheduler(
        environment=environment,
        config=ValkyrieCycleConfig(
            valkyrie_id="valkyrie:k8s-prod-a",
            dream_interval_seconds=300,
            autonomy_mode=autonomy_mode,
            proposal_store_path=tmp_path / "proposals.json",
            domain="k8s",
        ),
        publisher=bus,
        evolution_provider=provider,
        proposal_store=JsonProposalStore(tmp_path / "proposals.json"),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_wake_cycle_emits_wakefulness_events_and_ui_status(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(["valkyrie.wakefulness.*"], lambda event: _record(events, event))
    clock = _FakeClock()
    scheduler = _scheduler(bus=bus, tmp_path=tmp_path, clock=clock)

    await scheduler.run_wake_cycle(reason="signal batch")
    await bus.flush()

    assert [event.payload["new_state"] for event in events] == ["awake", "watchful"]
    assert events[0].payload["environment_id"] == "cluster-prod-a"
    assert events[0].payload["nats_subject"] == "ravn.environment.valkyrie.wakefulness.changed"
    assert scheduler.status.to_ui_contract()["wakefulness"] == "watchful"
    assert scheduler.status.to_ui_contract()["last_wake_at"] == clock.now.isoformat()


@pytest.mark.asyncio
async def test_fake_clock_tick_runs_dream_only_when_due(tmp_path) -> None:
    bus = InProcessBus()
    dream_events: list[SleipnirEvent] = []
    await bus.subscribe(["learning.dream.*"], lambda event: _record(dream_events, event))
    clock = _FakeClock()
    scheduler = _scheduler(bus=bus, tmp_path=tmp_path, clock=clock)

    assert await scheduler.tick() is None
    clock.advance(301)
    result = await scheduler.tick()
    await bus.flush()

    assert result is not None
    assert result.status == "completed"
    assert [event.event_type for event in dream_events] == [
        "learning.dream.started",
        "learning.dream.completed",
    ]
    assert scheduler.status.next_dream_after == clock.now + timedelta(seconds=300)


@pytest.mark.asyncio
async def test_guarded_dream_records_but_defers_self_improvement(tmp_path) -> None:
    bus = InProcessBus()
    clock = _FakeClock()
    scheduler = _scheduler(bus=bus, tmp_path=tmp_path, clock=clock, autonomy_mode="guarded")

    result = await scheduler.run_dream_cycle()
    await bus.flush()
    stored = JsonProposalStore(tmp_path / "proposals.json").list()

    assert result.status == "completed"
    assert len(result.proposals) == 3
    assert result.applied == []
    assert len(result.deferred) == 3
    assert {proposal.status for proposal in stored} == {ProposalStatus.NEEDS_REVIEW.value}
    assert scheduler.status.proposals_deferred == 3


@pytest.mark.asyncio
async def test_yolo_dream_applies_allowed_environment_changes_only(tmp_path) -> None:
    bus = InProcessBus()
    clock = _FakeClock()
    scheduler = _scheduler(bus=bus, tmp_path=tmp_path, clock=clock, autonomy_mode="yolo")

    result = await scheduler.run_dream_cycle()
    await bus.flush()
    stored = JsonProposalStore(tmp_path / "proposals.json").list()

    assert len(result.applied) == 2
    assert len(result.deferred) == 1
    assert [proposal.artifact_type for proposal in result.applied] == ["skill", "strategy"]
    global_warning = next(
        proposal for proposal in stored if proposal.artifact_type == "system_prompt"
    )
    assert global_warning.status == ProposalStatus.NEEDS_REVIEW.value
    assert global_warning.scope == "global"
    assert scheduler.status.proposals_applied == 2


@pytest.mark.asyncio
async def test_dream_failure_emits_failed_event_and_unhealthy_state(tmp_path) -> None:
    bus = InProcessBus()
    events: list[SleipnirEvent] = []
    await bus.subscribe(
        ["learning.dream.*", "valkyrie.wakefulness.*"],
        lambda event: _record(events, event),
    )
    clock = _FakeClock()

    async def failing_provider() -> PromptEvolution:
        raise RuntimeError("mimir unavailable")

    scheduler = _scheduler(bus=bus, tmp_path=tmp_path, clock=clock, provider=failing_provider)

    result = await scheduler.run_dream_cycle()
    await bus.flush()

    assert result.status == "failed"
    assert result.error == "mimir unavailable"
    assert scheduler.status.wakefulness == "unhealthy"
    assert "learning.dream.failed" in [event.event_type for event in events]
    assert events[-1].payload["new_state"] == "unhealthy"


@pytest.mark.asyncio
async def test_episode_extraction_feeds_dream_skill_proposal(tmp_path) -> None:
    bus = InProcessBus()
    clock = _FakeClock()
    memory = _Memory(
        [
            Episode(
                episode_id=f"ep-{index}",
                session_id=f"s-{index}",
                timestamp=clock.now,
                summary="task completed by checking k8s events",
                task_description="task completed for k8s signal quality",
                tools_used=["kubectl", "mimir_write"],
                outcome=Outcome.SUCCESS,
                tags=["k8s"],
            )
            for index in range(3)
        ]
    )

    async def provider() -> PromptEvolution:
        return await PatternExtractor(
            memory,
            max_episodes_to_analyze=20,
            skill_suggestion_min_occurrences=3,
            strategy_min_occurrences=3,
            error_warning_min_occurrences=3,
        ).extract()

    scheduler = _scheduler(
        bus=bus,
        tmp_path=tmp_path,
        clock=clock,
        autonomy_mode="autonomous",
        provider=provider,
    )

    result = await scheduler.run_dream_cycle()
    await bus.flush()

    assert len(result.proposals) == 2
    assert [proposal.artifact_type for proposal in result.proposals] == ["skill", "strategy"]
    assert result.applied[0].artifact_type == "skill"
    assert result.deferred[0].artifact_type == "strategy"
