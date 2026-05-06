"""End-to-end pipeline integration tests (NIU-599).

Proves the full loop works: pipeline created → parallel personas run →
outcomes extracted → fan-in evaluated → next stage gets context →
approval gate → pipeline completes → learning written to Mimir.

Uses:
- InMemorySagaRepository (no Postgres)
- InMemoryEventBus (Tyr SSE bus)
- InProcessBus (Sleipnir bus for PostSessionReflectionService)
- MarkdownMimirAdapter with tmp_path (real filesystem, no external deps)
- StubVolundrFactory (mock Volundr sessions — no real pods)
- Mock LLM for PostSessionReflectionService
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.markdown import MarkdownMimirAdapter
from ravn.adapters.reflection.post_session import PostSessionReflectionService
from ravn.config import PostSessionReflectionConfig
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from tests.test_tyr.stubs import InMemorySagaRepository, StubVolundrFactory, StubVolundrPort
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.api.pipelines import create_pipelines_router, resolve_pipeline_executor
from tyr.config import AuthConfig
from tyr.domain.models import PhaseStatus, RaidStatus, SagaStatus
from tyr.domain.pipeline_executor import TemplateAwarePipelineExecutor
from tyr.ports.volundr import SpawnRequest, VolundrSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OWNER = "test-owner"

_REVIEW_PIPELINE = textwrap.dedent(
    """
    name: "Review: niuulabs/volundr#42"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "niuulabs/volundr"
    stages:
      - name: parallel-review
        parallel:
          - name: "Code review"
            persona: reviewer
            prompt: "Review this code"
          - name: "Security audit"
            persona: security-auditor
            prompt: "Audit this code"
        fan_in: all_must_pass
      - name: qa-test
        sequential:
          - name: "QA test run"
            persona: qa-agent
            prompt: "Run tests for {stages.parallel-review.summary}"
        condition: "stages.parallel-review.verdict == pass"
      - name: human-approval
        gate: human
        notify: []
        condition: "stages.qa-test.verdict == pass"
    """
)

_DYNAMIC_PIPELINE = textwrap.dedent(
    """
    name: "Quick review"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "niuulabs/volundr"
    stages:
      - name: review
        sequential:
          - name: "Review code"
            persona: reviewer
            prompt: "Quick review of the latest changes"
      - name: done
        gate: human
        notify: []
    """
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UniqueSessionVolundr(StubVolundrPort):
    """Volundr stub that returns a distinct session ID per spawn call."""

    def __init__(self) -> None:
        super().__init__()
        self._counter = 0

    async def spawn_session(self, request: SpawnRequest, *, auth_token=None) -> VolundrSession:
        self._counter += 1
        self.spawned.append(request)
        return VolundrSession(
            id=f"sess-{self._counter:03d}",
            name=request.name,
            status="running",
            tracker_issue_id=request.tracker_issue_id,
        )


def _make_executor(
    volundr: StubVolundrPort | None = None,
    repo: InMemorySagaRepository | None = None,
) -> tuple[TemplateAwarePipelineExecutor, InMemorySagaRepository, InMemoryEventBus]:
    repo = repo or InMemorySagaRepository()
    bus = InMemoryEventBus()
    factory = StubVolundrFactory(volundr or _UniqueSessionVolundr())
    executor = TemplateAwarePipelineExecutor(
        saga_repo=repo,
        volundr_factory=factory,
        event_bus=bus,
        owner_id=_OWNER,
    )
    return executor, repo, bus


def _make_reflection_service(
    sleipnir_bus: InProcessBus,
    mimir: MarkdownMimirAdapter,
    learning_json: str | None = None,
) -> PostSessionReflectionService:
    if learning_json is None:
        learning_json = json.dumps(
            {
                "title": "Review pipeline observation",
                "learning": "Automated review pipelines can catch issues reliably.",
                "type": "observation",
                "tags": ["review", "pipeline"],
                "evidence": "Full pipeline completed successfully.",
            }
        )
    resp = MagicMock()
    resp.content = learning_json
    llm = AsyncMock()
    llm.generate.return_value = resp

    config = PostSessionReflectionConfig(
        enabled=True,
        llm_alias="fast",
        max_tokens=512,
        learning_token_budget=500,
        max_learnings_injected=5,
    )
    return PostSessionReflectionService(
        subscriber=sleipnir_bus,
        mimir=mimir,
        llm=llm,
        config=config,
    )


async def _simulate_session_end(
    *,
    executor: TemplateAwarePipelineExecutor,
    sleipnir_bus: InProcessBus,
    raid_id: UUID,
    persona: str,
    outcome: dict,
    repo_slug: str = "niuulabs/volundr",
) -> None:
    """Simulate a raid completing: store outcome + publish Sleipnir event."""
    await executor.receive_outcome(raid_id=raid_id, outcome=outcome)

    event = ravn_session_ended(
        session_id=str(raid_id),
        persona=persona,
        outcome="success" if outcome.get("verdict") == "pass" else "failure",
        token_count=1000,
        duration_s=30.0,
        repo_slug=repo_slug,
        source="ravn:test",
    )
    event.payload["structured_outcome"] = outcome
    await sleipnir_bus.publish(event)


# ---------------------------------------------------------------------------
# Test 1: Full review pipeline (trigger → parallel → fan-in → QA → gate → done → Mimir)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_review_pipeline(tmp_path: Path) -> None:
    """End-to-end: pipeline creation → parallel review → fan-in → QA → gate → complete → Mimir."""
    # --- Setup -----------------------------------------------------------------
    volundr = _UniqueSessionVolundr()
    executor, repo, _ = _make_executor(volundr=volundr)
    sleipnir_bus = InProcessBus()
    mimir = MarkdownMimirAdapter(root=tmp_path)

    reflection_svc = _make_reflection_service(sleipnir_bus, mimir)
    await reflection_svc.start()

    # --- 1. Create pipeline from YAML ------------------------------------------
    saga = await executor.create_from_yaml(_REVIEW_PIPELINE)

    assert saga.status == SagaStatus.ACTIVE

    all_phases = await repo.get_phases_by_saga(saga.id)
    assert len(all_phases) == 3  # parallel-review, qa-test, human-approval

    # --- 2. Phase 1 should be ACTIVE with 2 raids running ----------------------
    phase1 = all_phases[0]
    assert phase1.status == PhaseStatus.ACTIVE
    assert phase1.name == "parallel-review"

    phase1_raids = await repo.get_raids_by_phase(phase1.id)
    assert len(phase1_raids) == 2
    assert all(r.status == RaidStatus.RUNNING for r in phase1_raids)

    reviewer_raid = next(r for r in phase1_raids if "review" in r.name.lower())
    security_raid = next(
        r for r in phase1_raids if "security" in r.name.lower() or "audit" in r.name.lower()
    )

    # --- 3. Simulate reviewer outcome (pass) -----------------------------------
    reviewer_outcome = {
        "verdict": "pass",
        "findings_count": 2,
        "critical_count": 0,
        "summary": "Clean code, minor style suggestions",
    }
    await _simulate_session_end(
        executor=executor,
        sleipnir_bus=sleipnir_bus,
        raid_id=reviewer_raid.id,
        persona="reviewer",
        outcome=reviewer_outcome,
    )

    # Phase 1 still ACTIVE — security-auditor not done yet
    phase1_updated = await repo.get_phase(phase1.id)
    assert phase1_updated.status == PhaseStatus.ACTIVE

    # --- 4. Simulate security-auditor outcome (pass) ---------------------------
    security_outcome = {
        "verdict": "pass",
        "critical_findings": 0,
        "summary": "No security issues detected",
    }
    await _simulate_session_end(
        executor=executor,
        sleipnir_bus=sleipnir_bus,
        raid_id=security_raid.id,
        persona="security-auditor",
        outcome=security_outcome,
    )

    # --- 5. Fan-in: all_must_pass → both passed → Phase 1 COMPLETE -------------
    phase1_final = await repo.get_phase(phase1.id)
    assert phase1_final.status == PhaseStatus.COMPLETE

    # --- 6. Phase 2 (QA) should be ACTIVE with 1 raid --------------------------
    phase2 = all_phases[1]
    phase2_updated = await repo.get_phase(phase2.id)
    assert phase2_updated.status == PhaseStatus.ACTIVE
    assert phase2_updated.name == "qa-test"

    phase2_raids = await repo.get_raids_by_phase(phase2.id)
    assert len(phase2_raids) == 1
    qa_raid = phase2_raids[0]
    assert qa_raid.status == RaidStatus.RUNNING

    # --- 7. Simulate QA outcome (pass) -----------------------------------------
    qa_outcome = {
        "verdict": "pass",
        "tests_run": 42,
        "tests_failed": 0,
        "summary": "All tests green",
    }
    await _simulate_session_end(
        executor=executor,
        sleipnir_bus=sleipnir_bus,
        raid_id=qa_raid.id,
        persona="qa-agent",
        outcome=qa_outcome,
    )

    # --- 8. Phase 3 (human-approval) should be GATED --------------------------
    phase3 = all_phases[2]
    phase3_updated = await repo.get_phase(phase3.id)
    assert phase3_updated.status == PhaseStatus.GATED
    assert phase3_updated.name == "human-approval"

    # --- 9. Approve the gate ---------------------------------------------------
    await executor.approve_gate(saga.id, phase3.id)

    # --- 10. Pipeline complete -------------------------------------------------
    saga_final = await executor.get_saga(saga.id)
    assert saga_final.status == SagaStatus.COMPLETE

    # --- 11. Verify learning written to Mimir ---------------------------------
    await sleipnir_bus.flush()

    pages = await mimir.list_pages(category="learnings")
    assert len(pages) > 0, "Expected at least one learning page after pipeline completed"

    await reflection_svc.stop()


# ---------------------------------------------------------------------------
# Test 2: Dynamic pipeline via API (POST YAML → executes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dynamic_pipeline_via_api() -> None:
    """POST a YAML pipeline definition → pipeline executes end-to-end."""
    volundr = _UniqueSessionVolundr()
    repo = InMemorySagaRepository()
    bus = InMemoryEventBus()
    factory = StubVolundrFactory(volundr)
    executor = TemplateAwarePipelineExecutor(
        saga_repo=repo,
        volundr_factory=factory,
        event_bus=bus,
        owner_id=_OWNER,
    )

    app = FastAPI()
    settings = MagicMock()
    settings.auth = AuthConfig(allow_anonymous_dev=True)
    app.state.settings = settings
    app.include_router(create_pipelines_router())
    app.dependency_overrides[resolve_pipeline_executor] = lambda: executor

    client = TestClient(app)

    response = client.post(
        "/api/v1/tyr/pipelines",
        json={
            "definition": _DYNAMIC_PIPELINE,
            "context": {},
            "auto_start": True,
        },
    )
    assert response.status_code == 201

    data = response.json()
    assert "saga_id" in data
    assert data["phase_count"] == 2  # review + done (gate)
    assert data["auto_started"] is True

    saga_id = UUID(data["saga_id"])
    saga = await executor.get_saga(saga_id)
    assert saga is not None
    assert saga.status == SagaStatus.ACTIVE

    # Phase 1 raid should be RUNNING (auto_start=True dispatched it)
    phases = await repo.get_phases_by_saga(saga_id)
    phase1_raids = await repo.get_raids_by_phase(phases[0].id)
    assert len(phase1_raids) == 1
    assert phase1_raids[0].status == RaidStatus.RUNNING

    # Simulate phase 1 completing → gate phase becomes GATED
    await executor.receive_outcome(
        raid_id=phase1_raids[0].id,
        outcome={"verdict": "pass"},
    )

    # Gate phase should now be GATED
    gate_phase = await executor.get_phase(phases[1].id)
    assert gate_phase.status == PhaseStatus.GATED

    # Approve gate → saga completes
    await executor.approve_gate(saga_id, phases[1].id)
    saga_final = await executor.get_saga(saga_id)
    assert saga_final.status == SagaStatus.COMPLETE


# ---------------------------------------------------------------------------
# Test 3: Failure propagation via fan-in rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_fails_on_fan_in_rejection() -> None:
    """Parallel stage where one persona fails → pipeline stops with FAILED status."""
    executor, repo, bus = _make_executor()

    saga = await executor.create_from_yaml(_REVIEW_PIPELINE)
    assert saga.status == SagaStatus.ACTIVE

    phases = await repo.get_phases_by_saga(saga.id)
    phase1 = phases[0]
    phase1_raids = await repo.get_raids_by_phase(phase1.id)
    assert len(phase1_raids) == 2

    reviewer_raid = next(r for r in phase1_raids if "review" in r.name.lower())
    security_raid = next(
        r for r in phase1_raids if "security" in r.name.lower() or "audit" in r.name.lower()
    )

    # Reviewer passes
    await executor.receive_outcome(
        raid_id=reviewer_raid.id,
        outcome={"verdict": "pass", "findings_count": 1},
    )

    # Security fails
    await executor.receive_outcome(
        raid_id=security_raid.id,
        outcome={"verdict": "fail", "critical_findings": 2, "summary": "SQL injection found"},
    )

    # Phase 1 should be COMPLETE (all raids terminal) but saga FAILED
    phase1_final = await repo.get_phase(phase1.id)
    assert phase1_final.status == PhaseStatus.COMPLETE

    saga_final = await repo.get_saga(saga.id)
    assert saga_final.status == SagaStatus.FAILED

    # Phase 2 (QA) should still be PENDING — never activated
    phase2 = await repo.get_phase(phases[1].id)
    assert phase2.status == PhaseStatus.PENDING

    # Verify the failure event was emitted
    failure_events = [e for e in bus.get_log(100) if e.event == "saga.failed"]
    assert len(failure_events) == 1
    assert "all_must_pass" in failure_events[0].data.get("reason", "").lower()


# ---------------------------------------------------------------------------
# Test 4: approve_gate edge cases (branch coverage for glue methods)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_gate_noop_when_phase_not_found() -> None:
    """approve_gate is a no-op when the phase ID does not exist."""
    import uuid

    executor, _, _ = _make_executor()
    # Should not raise
    await executor.approve_gate(uuid.uuid4(), uuid.uuid4())


@pytest.mark.asyncio
async def test_approve_gate_noop_when_saga_not_found() -> None:
    """approve_gate is a no-op when the saga has been removed from the repo."""
    executor, repo, _ = _make_executor()

    saga = await executor.create_from_yaml(_REVIEW_PIPELINE)
    phases = await repo.get_phases_by_saga(saga.id)
    # Manually gate phase 3 so approve_gate passes the phase-found and phase-GATED checks
    from dataclasses import replace as dc_replace

    gated = dc_replace(phases[2], status=PhaseStatus.GATED)
    await repo.save_phase(gated)
    # Delete the saga from the repo so get_saga returns None
    del repo.sagas[saga.id]

    # Should not raise even though saga is missing
    await executor.approve_gate(saga.id, phases[2].id)


@pytest.mark.asyncio
async def test_approve_gate_noop_when_phase_not_gated() -> None:
    """approve_gate is a no-op when the phase exists but is not GATED."""
    executor, repo, _ = _make_executor()

    saga = await executor.create_from_yaml(_REVIEW_PIPELINE, auto_start=False)
    phases = await repo.get_phases_by_saga(saga.id)
    # Phase 1 is ACTIVE (created as active), not GATED — approve_gate should be a no-op
    phase1 = phases[0]
    initial_status = repo.phases[phase1.id].status
    await executor.approve_gate(saga.id, phase1.id)
    # Phase status unchanged after the no-op call
    assert repo.phases[phase1.id].status == initial_status


@pytest.mark.asyncio
async def test_approve_gate_advances_to_next_pending_phase() -> None:
    """When a pipeline has gate→phase→gate, approving the first gate activates the middle phase."""
    # Pipeline: review → gate1 → qa → gate2
    pipeline = textwrap.dedent(
        """
        name: "two-gate"
        feature_branch: "feat/test"
        base_branch: "main"
        repos:
          - "test/repo"
        stages:
          - name: review
            sequential:
              - name: "Review"
                persona: reviewer
                prompt: "Review"
          - name: approval-1
            gate: human
            notify: []
          - name: qa
            sequential:
              - name: "QA"
                persona: qa-agent
                prompt: "Run tests"
          - name: approval-2
            gate: human
            notify: []
        """
    )
    executor, repo, bus = _make_executor()

    saga = await executor.create_from_yaml(pipeline)
    phases = await repo.get_phases_by_saga(saga.id)
    assert len(phases) == 4

    # Complete phase 1 → phase 2 (approval-1) becomes GATED
    phase1_raids = await repo.get_raids_by_phase(phases[0].id)
    await executor.receive_outcome(raid_id=phase1_raids[0].id, outcome={"verdict": "pass"})

    phase2 = await repo.get_phase(phases[1].id)
    assert phase2.status == PhaseStatus.GATED

    # Approve gate → phase 3 (qa) should become ACTIVE
    await executor.approve_gate(saga.id, phases[1].id)

    phase3 = await repo.get_phase(phases[2].id)
    assert phase3.status == PhaseStatus.ACTIVE

    # Saga still active (approval-2 gate not yet reached)
    saga_state = await executor.get_saga(saga.id)
    assert saga_state.status == SagaStatus.ACTIVE


# ---------------------------------------------------------------------------
# Test 8: gate→gate pipeline (regression for approve_gate bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_gate_followed_by_second_gate() -> None:
    """Approving gate1 when gate2 follows immediately must GATE (not ACTIVE) the next phase.

    Regression test for the bug where approve_gate called _advance_phase directly,
    bypassing gate detection in _transition_to_next_phase.
    """
    # Pipeline: review → gate1 → gate2
    pipeline = textwrap.dedent(
        """
        name: "chained-gates"
        feature_branch: "feat/test"
        base_branch: "main"
        repos:
          - "test/repo"
        stages:
          - name: review
            sequential:
              - name: "Review"
                persona: reviewer
                prompt: "Review"
          - name: gate1
            gate: human
            notify: []
          - name: gate2
            gate: human
            notify: []
        """
    )
    executor, repo, _ = _make_executor()

    saga = await executor.create_from_yaml(pipeline)
    phases = await repo.get_phases_by_saga(saga.id)
    assert len(phases) == 3  # review, gate1, gate2

    # Complete review → gate1 should become GATED
    phase1_raids = await repo.get_raids_by_phase(phases[0].id)
    await executor.receive_outcome(raid_id=phase1_raids[0].id, outcome={"verdict": "pass"})

    gate1 = await repo.get_phase(phases[1].id)
    assert gate1.status == PhaseStatus.GATED, "gate1 should be GATED after review completes"

    # Approve gate1 → gate2 must become GATED (not ACTIVE)
    await executor.approve_gate(saga.id, phases[1].id)

    gate2 = await repo.get_phase(phases[2].id)
    assert gate2.status == PhaseStatus.GATED, (
        "gate2 should be GATED after approving gate1 — was ACTIVE before the bug fix"
    )

    # Saga still active — gate2 not yet approved
    saga_state = await executor.get_saga(saga.id)
    assert saga_state.status == SagaStatus.ACTIVE

    # Approve gate2 → saga completes
    await executor.approve_gate(saga.id, phases[2].id)
    saga_final = await executor.get_saga(saga.id)
    assert saga_final.status == SagaStatus.COMPLETE
