"""Tests for resident Valkyrie Environment feedback capture."""

from __future__ import annotations

from ravn.context.evolution import PatternExtractor
from ravn.domain.models import Episode, EpisodeMatch, SessionSummary, SharedContext
from ravn.feedback import EnvironmentFeedbackRecorder, feedback_event_to_episode
from ravn.ports.memory import MemoryPort
from ravn.ports.tool import ToolPort
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.catalog import feedback_recorded
from sleipnir.domain.events import SleipnirEvent


class _Memory(MemoryPort):
    def __init__(self) -> None:
        self.episodes: list[Episode] = []
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
        matches: list[EpisodeMatch] = []
        for episode in self.episodes:
            haystack = " ".join(
                [
                    episode.summary,
                    episode.task_description,
                    " ".join(episode.tags),
                    " ".join(episode.errors),
                ]
            ).lower()
            if not terms or any(term in haystack for term in terms):
                matches.append(EpisodeMatch(episode=episode, relevance=1.0))
        return matches[:limit]

    async def prefetch(self, context: str) -> str:
        matches = await self.query_episodes(context, min_relevance=0.0)
        return "\n".join(match.episode.summary for match in matches)

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


def _feedback(
    *,
    feedback_type: str = "wrong_tier",
    rating: str = "incorrect",
    notes: str = "This should have been ambient, not urgent.",
    target_event_id: str = "decision-1",
) -> SleipnirEvent:
    return feedback_recorded(
        environment_id="cluster-a",
        target_event_id=target_event_id,
        feedback_type=feedback_type,
        rating=rating,
        notes=notes,
        signal_refs=["signal-1"],
        judgment_refs=["judgment-1"],
        court_decision_id="decision-1",
        action_id="action-1" if "action" in feedback_type else "",
        surface_id="surface:ops",
        user_id="human:operator",
        correction={"tier": "ambient"},
        delivery_effect={"effective_until": "2026-06-04T00:00:00Z"},
        responsible_valkyrie_id="valkyrie:k8s-a",
        environment_type="k8s",
        signal_source="kubernetes.events",
        domain_scope="infrastructure",
        root_correlation_id="root-1",
        source="human:operator",
        correlation_id="root-1",
    )


def test_feedback_event_to_episode_preserves_lineage_and_query_tags() -> None:
    event = _feedback()

    episode = feedback_event_to_episode(event)

    assert episode.episode_id == f"feedback:{event.event_id}"
    assert episode.session_id == "feedback:cluster-a:valkyrie:k8s-a"
    assert episode.outcome == "failure"
    assert episode.outcome_valid is True
    assert episode.structured_outcome is not None
    assert episode.structured_outcome["environment_id"] == "cluster-a"
    assert episode.structured_outcome["signal_refs"] == ["signal-1"]
    assert episode.structured_outcome["judgment_refs"] == ["judgment-1"]
    assert episode.structured_outcome["court_decision_id"] == "decision-1"
    assert "environment:cluster-a" in episode.tags
    assert "feedback:wrong_tier" in episode.tags
    assert "signal_source:kubernetes.events" in episode.tags
    assert episode.errors == ["wrong_tier feedback: This should have been ambient, not urgent."]


async def test_feedback_recorder_writes_private_environment_episode() -> None:
    bus = InProcessBus()
    memory = _Memory()
    recorder = EnvironmentFeedbackRecorder(subscriber=bus, publisher=bus, memory=memory)
    await recorder.start()

    await bus.publish(_feedback(feedback_type="good_action", rating="correct", notes="Safe fix."))
    await bus.flush()

    assert len(memory.episodes) == 1
    episode = memory.episodes[0]
    assert episode.outcome == "success"
    assert episode.tools_used == ["feedback.capture", "feedback.action"]
    assert "feedback:good_action" in episode.tags
    assert await memory.count_episodes() == 1

    await recorder.stop()


async def test_snooze_feedback_updates_future_delivery_state_without_mutating_episode() -> None:
    bus = InProcessBus()
    memory = _Memory()
    preference_events: list[SleipnirEvent] = []

    async def collect(event: SleipnirEvent) -> None:
        preference_events.append(event)

    await bus.subscribe([registry.FEEDBACK_PREFERENCE_UPDATED], collect)
    recorder = EnvironmentFeedbackRecorder(subscriber=bus, publisher=bus, memory=memory)
    await recorder.start()
    event = _feedback(
        feedback_type="snooze",
        rating="too_noisy",
        notes="Quiet this surface until morning.",
    )

    await bus.publish(event)
    await bus.flush()
    await bus.flush()

    state = recorder.current_delivery_state(
        environment_id="cluster-a",
        target_event_id="decision-1",
        surface_id="surface:ops",
    )
    assert state is not None
    assert state.delivery_state == "snoozed"
    assert state.source_feedback_id == event.event_id
    assert memory.episodes[0].structured_outcome is not None
    assert memory.episodes[0].structured_outcome["delivery_effect"] == {
        "effective_until": "2026-06-04T00:00:00Z"
    }
    assert len(preference_events) == 1
    assert preference_events[0].payload["delivery_state"] == "snoozed"
    assert preference_events[0].payload["source_feedback_id"] == event.event_id

    await recorder.stop()


async def test_feedback_episodes_feed_existing_evolution_extractor() -> None:
    memory = _Memory()
    for index in range(3):
        event = _feedback(
            feedback_type="good_action",
            rating="correct",
            notes=f"Kubernetes probe was useful {index}.",
            target_event_id=f"decision-{index}",
        )
        await memory.record_episode(feedback_event_to_episode(event))

    evolution = await PatternExtractor(
        memory,
        strategy_min_occurrences=3,
        skill_suggestion_min_occurrences=3,
        error_warning_min_occurrences=3,
        max_strategy_injections=10,
    ).extract()

    assert evolution.episodes_analyzed == 3
    assert any(
        strategy.task_type == "signal_source:kubernetes.events"
        for strategy in evolution.strategy_injections
    )


async def test_full_feedback_vocabulary_is_recorded(tmp_path) -> None:
    """Every NIU-1022 feedback type round-trips into an episode (F3)."""
    from ravn.feedback.recorder import KNOWN_FEEDBACK_TYPES, feedback_event_to_episode
    from sleipnir.domain.catalog import feedback_recorded

    expected = {
        "useful",
        "good_action",
        "draft_accepted",
        "bad_action",
        "draft_rejected",
        "draft_edited",
        "dismissed",
        "wrong_tier",
        "wrong_state",
        "should_have_shown",
        "too_late",
        "snooze",
        "escalate",
    }
    assert expected <= KNOWN_FEEDBACK_TYPES

    for feedback_type in sorted(expected):
        event = feedback_recorded(
            environment_id="cluster-a",
            feedback_type=feedback_type,
            target_event_id="evt-1",
            rating="",
            notes="",
            source="test",
        )
        episode = feedback_event_to_episode(event)
        assert episode.structured_outcome["feedback_type"] == feedback_type
