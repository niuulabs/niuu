"""Tests for learning promotion across Environment, Flock, domain, and shared scopes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.mimir import MimirPageMeta
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.adapters.reflection.learning_promotion import (
    LearningPromotionCandidate,
    LearningPromotionPolicy,
    LearningPromotionService,
    LearningPromotionStore,
)
from ravn.adapters.reflection.post_session import fetch_relevant_learnings
from ravn.domain.mimir import MimirMount, WriteRouting
from sleipnir.domain import registry


class FakePublisher:
    def __init__(self) -> None:
        self.events = []

    async def publish(self, event) -> None:
        self.events.append(event)

    async def publish_batch(self, events) -> None:
        self.events.extend(events)


def _candidate(**overrides) -> LearningPromotionCandidate:
    data = {
        "learning_id": "learn-k8s-oom",
        "source_path": "learnings/cluster-a/oom.md",
        "title": "OOM restart watch",
        "summary": "OOMKilled pods usually recover after cache pressure drops.",
        "content": "Watch pod events and memory pressure before restarting workloads.",
        "current_scope": "environment",
        "target_scope": "flock",
        "environment_id": "cluster-a",
        "source_valkyrie_id": "valkyrie:k8s-a",
        "domain": "k8s",
        "flock_id": "flock:k8s",
        "source_episode_ids": ["ep-1", "ep-2", "ep-3"],
        "confidence": 0.86,
        "repetition_count": 3,
        "successful_reuse_count": 2,
        "feedback_score": 0.4,
        "redaction_status": "redacted",
        "autonomy_mode": "yolo",
    }
    data.update(overrides)
    return LearningPromotionCandidate(**data)


def _mock_port() -> MagicMock:
    port = MagicMock()
    port.upsert_page = AsyncMock(return_value=None)
    port.list_pages = AsyncMock(return_value=[])
    return port


def _store(tmp_path: Path) -> LearningPromotionStore:
    return LearningPromotionStore(tmp_path / "promotions.json")


def test_policy_keeps_guarded_and_low_confidence_as_review() -> None:
    policy = LearningPromotionPolicy()

    guarded = policy.decide(_candidate(autonomy_mode="guarded"))
    low_confidence = policy.decide(_candidate(confidence=0.5))

    assert guarded.decision == "needs_review"
    assert low_confidence.decision == "needs_review"


def test_policy_blocks_negative_feedback() -> None:
    decision = LearningPromotionPolicy().decide(_candidate(feedback_score=-1.0))

    assert decision.decision == "deny"
    assert "negative feedback" in decision.reason


@pytest.mark.asyncio
async def test_yolo_flock_promotion_uses_existing_composite_shared_mount(tmp_path: Path) -> None:
    local_port = _mock_port()
    shared_port = _mock_port()
    mimir = CompositeMimirAdapter(
        mounts=[
            MimirMount("local", local_port, "local", read_priority=0),
            MimirMount("shared", shared_port, "shared", read_priority=1),
        ],
        write_routing=WriteRouting(default=["local"]),
    )
    publisher = FakePublisher()
    service = LearningPromotionService(
        mimir=mimir,
        store=_store(tmp_path),
        publisher=publisher,
        source="valkyrie:k8s-a",
    )

    record = await service.promote(_candidate())

    assert record.status == "promoted"
    assert record.target_mount == "shared"
    assert record.promoted_path.startswith("learnings/flock/flock-k8s/")
    shared_port.upsert_page.assert_called_once()
    local_port.upsert_page.assert_not_called()
    assert publisher.events[0].event_type == registry.LEARNING_PROMOTED
    assert publisher.events[0].payload["to_scope"] == "flock"


@pytest.mark.asyncio
async def test_autonomous_domain_promotion_defers_for_review(tmp_path: Path) -> None:
    mimir = MagicMock()
    mimir.upsert_page = AsyncMock()
    service = LearningPromotionService(mimir=mimir, store=_store(tmp_path))

    record = await service.promote(
        _candidate(target_scope="domain", autonomy_mode="autonomous", confidence=0.9)
    )

    assert record.status == "needs_review"
    assert record.to_scope == "domain"
    mimir.upsert_page.assert_not_called()


@pytest.mark.asyncio
async def test_peer_adoption_and_rejection_track_negative_transfer(tmp_path: Path) -> None:
    mimir = MagicMock()
    mimir.upsert_page = AsyncMock()
    publisher = FakePublisher()
    service = LearningPromotionService(
        mimir=mimir,
        store=_store(tmp_path),
        publisher=publisher,
    )
    record = await service.promote(_candidate())

    canary = await service.record_adoption(
        record.promotion_id,
        peer_environment_id="cluster-b",
        action="canary",
        canary_passed=True,
        rationale="Works on staging cluster.",
    )
    rejected = await service.record_adoption(
        record.promotion_id,
        peer_environment_id="cluster-c",
        action="rejected",
        rationale="Different memory-pressure baseline.",
    )

    assert canary.adoptions[0].action == "canary"
    assert rejected.adoptions[-1].action == "rejected"
    assert rejected.negative_transfer[-1]["peer_environment_id"] == "cluster-c"
    assert publisher.events[-1].event_type == registry.LEARNING_ADOPTION_RECORDED


@pytest.mark.asyncio
async def test_demotion_archives_promotion_state_after_regression(tmp_path: Path) -> None:
    mimir = MagicMock()
    mimir.upsert_page = AsyncMock()
    service = LearningPromotionService(mimir=mimir, store=_store(tmp_path))
    record = await service.promote(_candidate())

    demoted = service.demote(record.promotion_id, reason="Regression in cluster-c")
    reloaded = _store(tmp_path).get(record.promotion_id)

    assert demoted.status == "demoted"
    assert reloaded.status == "demoted"
    assert "Regression" in reloaded.negative_transfer[-1]["reason"]


@pytest.mark.asyncio
async def test_promoted_shared_and_flock_learnings_inject_with_token_budget() -> None:
    mimir = MagicMock()
    now = datetime(2026, 6, 3, tzinfo=UTC)
    metas = [
        MimirPageMeta(
            path="learnings/flock/flock-k8s/oom.md",
            title="OOM restart watch",
            summary="",
            category="learnings",
            updated_at=now,
            source_ids=[],
        ),
        MimirPageMeta(
            path="learnings/shared/too-large.md",
            title="Shared giant learning",
            summary="",
            category="learnings",
            updated_at=now,
            source_ids=[],
        ),
    ]
    mimir.list_pages = AsyncMock(return_value=metas)
    mimir.read_page = AsyncMock(
        side_effect=[
            "---\ntitle: OOM restart watch\n---\n\nUse event history before restarting.",
            "---\ntitle: Shared giant learning\n---\n\n" + ("x" * 5000),
        ]
    )

    block = await fetch_relevant_learnings(
        mimir,
        repo_slug="",
        max_pages=5,
        token_budget=50,
        domain="k8s",
        flock_id="flock:k8s",
    )

    assert "OOM restart watch" in block
    assert "Use event history" in block
    assert "Shared giant learning" not in block
