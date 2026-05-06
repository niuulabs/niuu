"""Tests for dispatch integration — flow resolution, snapshotting, and log output."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

# Reuse stubs from existing test infrastructure
from tests.test_tyr.stubs import (
    InMemorySagaRepository,
    StubTracker,
    StubTrackerFactory,
    StubVolundrFactory,
)
from tyr.adapters.flows.config import ConfigFlockFlowProvider
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.domain.models import (
    Saga,
    SagaStatus,
    TrackerIssue,
)
from tyr.domain.services.dispatch_service import (
    DispatchConfig,
    DispatchItem,
    DispatchService,
    _snapshot_hash,
)

_NOW = datetime.now(UTC)


def _make_saga(saga_id=None, repos=None) -> Saga:
    return Saga(
        id=saga_id or uuid4(),
        tracker_id="proj-1",
        tracker_type="linear",
        slug="test-saga",
        name="Test Saga",
        repos=repos or ["github.com/test/repo"],
        feature_branch="feat/test",
        base_branch="main",
        status=SagaStatus.ACTIVE,
        confidence=0.5,
        created_at=_NOW,
        owner_id="test-owner",
    )


def _make_issue(identifier: str = "NIU-100") -> TrackerIssue:
    return TrackerIssue(
        id="issue-1",
        identifier=identifier,
        title="Test Issue",
        description="A test issue description",
        status="todo",
        priority=1,
        priority_label="High",
        estimate=2.0,
        url="https://linear.app/test/NIU-100",
    )


def _make_dispatch_config(flock_enabled: bool = True) -> DispatchConfig:
    return DispatchConfig(
        flock_enabled=flock_enabled,
        flock_default_personas=[
            {"name": "coordinator"},
            {"name": "reviewer"},
        ],
    )


def _make_dispatch_item(saga_id: str) -> DispatchItem:
    return DispatchItem(
        saga_id=saga_id,
        issue_id="issue-1",
        repo="github.com/test/repo",
    )


def _make_flow(name: str = "code-review-flow") -> FlockFlowConfig:
    return FlockFlowConfig(
        name=name,
        description="Standard code review",
        personas=[
            FlockPersonaOverride(name="coordinator", llm={"model": "claude-opus-4-6"}),
            FlockPersonaOverride(
                name="reviewer",
                system_prompt_extra="Focus on test coverage",
            ),
            FlockPersonaOverride(name="security-auditor"),
        ],
        mimir_hosted_url="http://mimir:8080",
        sleipnir_publish_urls=["http://sleipnir:4222"],
        max_concurrent_tasks=5,
    )


class TestFlowResolution:
    """Test that dispatching with flock_flow resolves the flow and snapshots it."""

    def test_build_spawn_request_with_flow(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )

        assert request.workload_type == "ravn_flock"
        personas = request.workload_config["personas"]
        assert len(personas) == 3
        assert personas[0]["name"] == "coordinator"
        assert personas[0]["llm"] == {"model": "claude-opus-4-6"}
        assert personas[1]["name"] == "reviewer"
        assert personas[1]["system_prompt_extra"] == "Focus on test coverage"
        assert personas[2]["name"] == "security-auditor"

    def test_build_spawn_request_flow_not_found_uses_defaults(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="nonexistent-flow",
        )

        personas = request.workload_config["personas"]
        assert len(personas) == 2
        assert personas[0]["name"] == "coordinator"
        assert personas[1]["name"] == "reviewer"

    def test_build_spawn_request_no_flow_provider(self) -> None:
        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=None,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )

        # Falls back to defaults since no provider
        personas = request.workload_config["personas"]
        assert len(personas) == 2

    def test_build_spawn_request_without_flow(self) -> None:
        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
        )

        personas = request.workload_config["personas"]
        assert len(personas) == 2


class TestSnapshotIsolation:
    """Snapshot test: mutate a flow after dispatch — in-flight config unaffected."""

    def test_flow_mutation_does_not_affect_snapshot(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        # Build the spawn request (snapshot taken)
        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )
        snapshot_personas = copy.deepcopy(request.workload_config["personas"])

        # Mutate the flow in the provider
        mutated_flow = FlockFlowConfig(
            name="code-review-flow",
            personas=[FlockPersonaOverride(name="completely-different-persona")],
        )
        flow_provider.save(mutated_flow)

        # The snapshot from before the mutation is unaffected
        assert request.workload_config["personas"] == snapshot_personas
        assert len(request.workload_config["personas"]) == 3

    def test_flow_deletion_does_not_affect_snapshot(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )

        flow_provider.delete("code-review-flow")

        # Snapshot still has the original personas
        assert len(request.workload_config["personas"]) == 3


class TestPersonaOverridePrecedence:
    """Per-dispatch persona_overrides take precedence over flow-level."""

    def test_dispatch_override_merges_into_flow(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
            persona_overrides=[
                {"name": "coordinator", "llm": {"model": "claude-sonnet-4-6"}},
            ],
        )

        personas = request.workload_config["personas"]
        coordinator = next(p for p in personas if p["name"] == "coordinator")
        # Dispatch override takes precedence
        assert coordinator["llm"] == {"model": "claude-sonnet-4-6"}

    def test_dispatch_override_adds_new_persona(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
            persona_overrides=[
                {"name": "new-persona", "llm": {"model": "claude-opus-4-6"}},
            ],
        )

        personas = request.workload_config["personas"]
        names = [p["name"] for p in personas]
        assert "new-persona" in names
        assert len(personas) == 4  # 3 from flow + 1 new


class TestFlowMimirAndSleipnir:
    """Flow-level mimir and sleipnir URLs override config defaults."""

    def test_flow_overrides_mimir_url(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow = _make_flow()
        flow_provider.save(flow)

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )

        assert request.workload_config["mimir_hosted_url"] == "http://mimir:8080"
        assert request.workload_config["sleipnir_publish_urls"] == ["http://sleipnir:4222"]


class TestFlockDisabled:
    """When flock is disabled, flows are not resolved."""

    def test_solo_dispatch_ignores_flow(self) -> None:
        flow_provider = ConfigFlockFlowProvider()
        flow_provider.save(_make_flow())

        saga = _make_saga()
        svc = DispatchService(
            tracker_factory=StubTrackerFactory(StubTracker()),
            volundr_factory=StubVolundrFactory(),
            saga_repo=InMemorySagaRepository(),
            dispatcher_repo=AsyncMock(),
            config=_make_dispatch_config(flock_enabled=False),
            flow_provider=flow_provider,
        )

        request = svc._build_spawn_request(
            item=_make_dispatch_item(str(saga.id)),
            saga=saga,
            issue=_make_issue(),
            effective_model="claude-sonnet-4-6",
            effective_prompt="",
            integration_ids=[],
            flock_flow="code-review-flow",
        )

        # Solo mode — no flock personas; workload_type is the default, not ravn_flock
        assert request.workload_type != "ravn_flock"
        assert "personas" not in (request.workload_config or {})


class TestSnapshotHash:
    def test_deterministic(self) -> None:
        personas = [{"name": "coordinator"}, {"name": "reviewer"}]
        h1 = _snapshot_hash(personas)
        h2 = _snapshot_hash(personas)
        assert h1 == h2
        assert len(h1) == 8

    def test_different_input_different_hash(self) -> None:
        h1 = _snapshot_hash([{"name": "coordinator"}])
        h2 = _snapshot_hash([{"name": "reviewer"}])
        assert h1 != h2
