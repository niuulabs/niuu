"""Persistent court audit and daemon wiring for the ODIN court (F1/F2)."""

from __future__ import annotations

from datetime import UTC, datetime

from ravn.cli.commands import _build_feedback_recorder, _build_odin_court
from ravn.config import Settings
from ravn.domain.models import Episode, Outcome
from ravn.odin import OdinCourt
from ravn.odin.audit import EpisodicCourtAuditSink, court_decision_to_episode
from ravn.odin.court import CourtDecisionRecord
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.catalog import valkyrie_judgment_proposed
from tests.ravn.fixtures.fakes import BusRecorder


class _FakeMemory:
    def __init__(self) -> None:
        self.episodes: list[Episode] = []

    async def record_episode(self, episode: Episode) -> None:
        self.episodes.append(episode)


def _record(decision: str = "notify", tier: str = "present") -> CourtDecisionRecord:
    return CourtDecisionRecord(
        audit_ref="audit-1",
        environment_id="cluster-a",
        root_correlation_id="sig-1",
        decision=decision,
        tier=tier,
        action_authorization="autonomous",
        escalation_path="surface:default",
        huddle_id="",
        judgment_refs=["j-1"],
        action_refs=[],
        rejected_refs=[],
        dissent=[],
        evidence=[{"signal": "sig-1"}],
        rationale="single judgment resolved",
        created_at=datetime.now(UTC),
        raw_event_ids=["e-1"],
    )


def test_court_decision_maps_to_queryable_episode() -> None:
    episode = court_decision_to_episode(_record())
    assert episode.episode_id == "court:audit-1"
    assert episode.session_id == "odin-court:cluster-a"
    assert "odin-court" in episode.tags
    assert "environment:cluster-a" in episode.tags
    assert "decision:notify" in episode.tags
    assert episode.structured_outcome["kind"] == "odin_court_decision"
    assert episode.structured_outcome["root_correlation_id"] == "sig-1"
    assert episode.outcome is Outcome.PARTIAL  # escalations need a human


def test_autonomous_decisions_record_success_outcome() -> None:
    episode = court_decision_to_episode(_record(decision="autonomous_action"))
    assert episode.outcome is Outcome.SUCCESS


async def test_sink_persists_through_memory_port() -> None:
    memory = _FakeMemory()
    await EpisodicCourtAuditSink(memory).record_decision(_record())
    assert len(memory.episodes) == 1
    assert memory.episodes[0].episode_id == "court:audit-1"


async def test_wired_court_resolves_judgment_and_persists_episode() -> None:
    """Judgment -> court decision -> attention event -> persisted episode."""
    bus = InProcessBus()
    recorder = BusRecorder(bus)
    await bus.subscribe(["*"], recorder)
    memory = _FakeMemory()
    court = OdinCourt(
        publisher=bus,
        subscriber=bus,
        audit_sink=EpisodicCourtAuditSink(memory),
        quorum_size=1,
    )
    await court.start()

    await bus.publish(
        valkyrie_judgment_proposed(
            environment_id="cluster-a",
            valkyrie_id="valkyrie:k8s-a",
            attention_tier="present",
            recommended_action="notify",
            authority_boundary="human_review_required",
            confidence=0.8,
            operational_state="watching",
            rationale="pod oomkilled",
            signal_refs=["sig-1"],
            evidence=[{"signal": "sig-1"}],
            correlation_ids={"root": "sig-1"},
            source="valkyrie:k8s-a",
            correlation_id="sig-1",
        )
    )
    await bus.flush()

    attention = await recorder.of_type(registry.ATTENTION_DECISION_MADE)
    assert len(attention) == 1
    assert attention[0].payload["decision"] == "notify"
    assert len(memory.episodes) == 1
    assert memory.episodes[0].structured_outcome["decision"] == "notify"
    await court.stop()


# ---------------------------------------------------------------------------
# Daemon builders
# ---------------------------------------------------------------------------


def _resident_settings(**overrides) -> Settings:
    data = {
        "environment": {"id": "cluster-a", "type": "k8s", "flocks": ["k8s-valkyries"]},
        **overrides,
    }
    return Settings(**data)


def test_build_odin_court_wires_for_resident_environments() -> None:
    bus = InProcessBus()
    court = _build_odin_court(_resident_settings(), publisher=bus, memory=_FakeMemory())
    assert court is not None
    assert isinstance(court, OdinCourt)


def test_build_odin_court_respects_disable_flag() -> None:
    bus = InProcessBus()
    settings = _resident_settings(odin_court={"enabled": False})
    assert _build_odin_court(settings, publisher=bus, memory=None) is None


def test_build_odin_court_skips_non_resident_daemons() -> None:
    bus = InProcessBus()
    assert _build_odin_court(Settings(), publisher=bus, memory=None) is None


def test_build_feedback_recorder_wires_for_resident_environments() -> None:
    bus = InProcessBus()
    recorder = _build_feedback_recorder(_resident_settings(), publisher=bus, memory=_FakeMemory())
    assert recorder is not None


def test_build_feedback_recorder_requires_memory() -> None:
    bus = InProcessBus()
    assert _build_feedback_recorder(_resident_settings(), publisher=bus, memory=None) is None
