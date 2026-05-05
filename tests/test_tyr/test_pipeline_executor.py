"""Tests for tyr.domain.pipeline_executor — PipelineExecutor and fan-in logic."""

from __future__ import annotations

import textwrap
import uuid

import pytest

from tests.test_tyr.stubs import (
    InMemorySagaRepository,
    StubFlockFlowProvider,
    StubVolundrFactory,
    StubVolundrPort,
)
from tyr.adapters.memory_event_bus import InMemoryEventBus
from tyr.domain.flock_flow import FlockFlowConfig, FlockPersonaOverride
from tyr.domain.flock_merge import build_flock_workload_config, merge_persona_override
from tyr.domain.models import PhaseStatus, RaidStatus, SagaStatus
from tyr.domain.pipeline_executor import (
    TemplateAwarePipelineExecutor,
    build_stage_context_from_outcomes,
    evaluate_fan_in,
    interpolate_stage_refs,
    merge_outcomes,
    merge_stage_outcomes,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_OWNER = "test-owner"

_PARALLEL_PIPELINE = textwrap.dedent(
    """
    name: "test-pipeline"
    feature_branch: "feat/test"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        parallel:
          - name: "Code review"
            persona: reviewer
            prompt: "Review this code"
          - name: "Security audit"
            persona: security-auditor
            prompt: "Audit this code"
        fan_in: all_must_pass
      - name: test
        sequential:
          - name: "QA test"
            persona: qa-agent
            prompt: "Run tests"
        condition: "stages.review.verdict == pass"
    """
)

_SINGLE_STAGE_PIPELINE = textwrap.dedent(
    """
    name: "single-stage"
    feature_branch: "feat/single"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        sequential:
          - name: "Review"
            persona: reviewer
            prompt: "Review code"
    """
)

_HUMAN_GATE_PIPELINE = textwrap.dedent(
    """
    name: "gated-pipeline"
    feature_branch: "feat/gated"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        sequential:
          - name: "Review"
            persona: reviewer
            prompt: "Review code"
      - name: approval
        gate: human
        notify: [slack]
    """
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor(
    volundr: StubVolundrPort | None = None,
    repo: InMemorySagaRepository | None = None,
    event_bus: InMemoryEventBus | None = None,
) -> tuple[TemplateAwarePipelineExecutor, InMemorySagaRepository, InMemoryEventBus]:
    repo = repo or InMemorySagaRepository()
    bus = event_bus or InMemoryEventBus()
    factory = StubVolundrFactory(volundr or StubVolundrPort())
    executor = TemplateAwarePipelineExecutor(
        saga_repo=repo,
        volundr_factory=factory,
        event_bus=bus,
        owner_id=_OWNER,
    )
    return executor, repo, bus


# ---------------------------------------------------------------------------
# evaluate_fan_in
# ---------------------------------------------------------------------------


class TestEvaluateFanIn:
    def test_merge_always_passes(self):
        assert evaluate_fan_in("merge", [{"verdict": "fail"}]) is True

    def test_all_must_pass_all_pass(self):
        outcomes = [{"verdict": "pass"}, {"verdict": "pass"}]
        assert evaluate_fan_in("all_must_pass", outcomes) is True

    def test_all_must_pass_one_fail(self):
        outcomes = [{"verdict": "pass"}, {"verdict": "fail"}]
        assert evaluate_fan_in("all_must_pass", outcomes) is False

    def test_all_must_pass_no_verdict_defaults_to_pass(self):
        outcomes = [{"findings_count": 5}, {"findings_count": 2}]
        assert evaluate_fan_in("all_must_pass", outcomes) is True

    def test_any_pass_one_passes(self):
        outcomes = [{"verdict": "fail"}, {"verdict": "pass"}]
        assert evaluate_fan_in("any_pass", outcomes) is True

    def test_any_pass_all_fail(self):
        outcomes = [{"verdict": "fail"}, {"verdict": "fail"}]
        assert evaluate_fan_in("any_pass", outcomes) is False

    def test_majority_more_than_half_pass(self):
        outcomes = [{"verdict": "pass"}, {"verdict": "pass"}, {"verdict": "fail"}]
        assert evaluate_fan_in("majority", outcomes) is True

    def test_majority_exactly_half_fails(self):
        outcomes = [{"verdict": "pass"}, {"verdict": "fail"}]
        assert evaluate_fan_in("majority", outcomes) is False

    def test_unknown_strategy_raises(self):
        with pytest.raises(ValueError, match="Unknown fan-in"):
            evaluate_fan_in("invalid", [])


# ---------------------------------------------------------------------------
# merge_outcomes
# ---------------------------------------------------------------------------


class TestMergeOutcomes:
    def test_all_pass_gives_pass(self):
        merged = merge_outcomes([{"verdict": "pass"}, {"verdict": "pass"}])
        assert merged["verdict"] == "pass"

    def test_any_fail_gives_fail(self):
        merged = merge_outcomes([{"verdict": "pass"}, {"verdict": "fail"}])
        assert merged["verdict"] == "fail"

    def test_findings_count_summed(self):
        merged = merge_outcomes([{"findings_count": 3}, {"findings_count": 2}])
        assert merged["findings_count"] == 5

    def test_critical_findings_summed(self):
        merged = merge_outcomes([{"critical_findings": 1}, {"critical_findings": 0}])
        assert merged["critical_findings"] == 1

    def test_participants_included(self):
        outcomes = [{"verdict": "pass"}, {"verdict": "pass"}]
        merged = merge_outcomes(outcomes)
        assert merged["participants"] == outcomes

    def test_no_findings_skips_key(self):
        merged = merge_outcomes([{"verdict": "pass"}])
        assert "findings_count" not in merged

    def test_summary_carried_from_first(self):
        merged = merge_outcomes([{"summary": "looks good"}, {"verdict": "pass"}])
        assert merged["summary"] == "looks good"


# ---------------------------------------------------------------------------
# PipelineExecutor.create_from_yaml
# ---------------------------------------------------------------------------


class TestCreateFromYaml:
    @pytest.mark.asyncio
    async def test_creates_saga(self):
        executor, repo, _ = _make_executor()
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE)
        assert saga.id in repo.sagas
        assert saga.name == "single-stage"

    @pytest.mark.asyncio
    async def test_creates_phases_and_raids(self):
        executor, repo, _ = _make_executor()
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE)
        phases = await repo.get_phases_by_saga(saga.id)
        assert len(phases) == 1
        raids = await repo.get_raids_by_phase(phases[0].id)
        assert len(raids) == 1

    @pytest.mark.asyncio
    async def test_parallel_stage_creates_two_raids(self):
        executor, repo, _ = _make_executor()
        saga = await executor.create_from_yaml(_PARALLEL_PIPELINE)
        phases = await repo.get_phases_by_saga(saga.id)
        assert len(phases) == 2
        raids = await repo.get_raids_by_phase(phases[0].id)
        assert len(raids) == 2

    @pytest.mark.asyncio
    async def test_auto_start_dispatches_first_phase_raids(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)
        # Should have spawned 1 session
        assert len(volundr.spawned) == 1

    @pytest.mark.asyncio
    async def test_auto_start_resolves_owner_integrations(self):
        volundr = StubVolundrPort()
        volundr.integration_ids = ["int-telegram", "int-linear"]
        executor, repo, _ = _make_executor(volundr=volundr)
        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)
        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].integration_ids == ["int-telegram", "int-linear"]

    @pytest.mark.asyncio
    async def test_auto_start_false_does_not_dispatch(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=False)
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_parallel_auto_start_dispatches_both_raids(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        await executor.create_from_yaml(_PARALLEL_PIPELINE, auto_start=True)
        # Phase 1 has 2 parallel participants
        assert len(volundr.spawned) == 2

    @pytest.mark.asyncio
    async def test_invalid_yaml_raises(self):
        executor, _, _ = _make_executor()
        with pytest.raises(Exception):
            await executor.create_from_yaml("name: ''\nstages: []")

    @pytest.mark.asyncio
    async def test_emits_saga_created_event(self):
        executor, _, bus = _make_executor()
        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE)
        events = [e for e in bus.get_log(100) if e.event == "saga.created"]
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_context_substitution(self):
        pipeline_yaml = """
name: "Review {event.repo}"
feature_branch: "feat/x"
base_branch: "main"
repos: ["{event.repo}"]
stages:
  - name: review
    sequential:
      - name: "Review {event.repo}"
        persona: reviewer
        prompt: "Review {event.repo}"
"""
        executor, repo, _ = _make_executor()
        saga = await executor.create_from_yaml(pipeline_yaml, context={"repo": "acme/app"})
        assert "acme/app" in saga.name


# ---------------------------------------------------------------------------
# PipelineExecutor.receive_outcome
# ---------------------------------------------------------------------------


class TestReceiveOutcome:
    @pytest.mark.asyncio
    async def test_receive_outcome_stores_structured_outcome(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)
        raid = raids[0]

        outcome = {"verdict": "pass", "findings_count": 0}
        await executor.receive_outcome(raid_id=raid.id, outcome=outcome)

        updated_raid = await repo.get_raid(raid.id)
        assert updated_raid is not None
        assert updated_raid.structured_outcome == outcome

    @pytest.mark.asyncio
    async def test_pass_verdict_sets_merged_status(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)
        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        updated = await repo.get_raid(raids[0].id)
        assert updated.status == RaidStatus.MERGED

    @pytest.mark.asyncio
    async def test_fail_verdict_sets_failed_status(self):
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)
        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "fail"})

        updated = await repo.get_raid(raids[0].id)
        assert updated.status == RaidStatus.FAILED

    @pytest.mark.asyncio
    async def test_unknown_raid_is_noop(self):
        executor, _, _ = _make_executor()
        # Should not raise
        await executor.receive_outcome(raid_id=uuid.uuid4(), outcome={"verdict": "pass"})


# ---------------------------------------------------------------------------
# E2E: Parallel fan-in advancing pipeline
# ---------------------------------------------------------------------------


class TestParallelFanIn:
    @pytest.mark.asyncio
    async def test_both_raids_must_complete_before_phase_advances(self):
        """Phase 1 has 2 parallel raids. Phase 2 should not start until both complete."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_PARALLEL_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)
        assert len(raids) == 2

        # Simulate first reviewer completing
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        # Phase 1 should still be ACTIVE (second raid not done yet)
        p1_after_first = repo.phases[phase1.id]
        assert p1_after_first.status == PhaseStatus.ACTIVE

        # Simulate second security-auditor completing
        await executor.receive_outcome(
            raid_id=raids[1].id, outcome={"verdict": "pass", "critical_findings": 0}
        )

        # Phase 1 should now be COMPLETE
        p1_final = repo.phases[phase1.id]
        assert p1_final.status == PhaseStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_all_must_pass_fan_in_fails_saga_when_raid_fails(self):
        """When fan_in=all_must_pass and a raid fails, saga should fail."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_PARALLEL_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # First raid passes
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})
        # Second raid fails
        await executor.receive_outcome(raid_id=raids[1].id, outcome={"verdict": "fail"})

        # Saga should be failed (fan-in all_must_pass failed)
        updated_saga = await repo.get_saga(saga.id)
        assert updated_saga.status == SagaStatus.FAILED

    @pytest.mark.asyncio
    async def test_human_gate_emits_approval_event(self):
        """Human gate phase should emit phase.needs_approval event."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_HUMAN_GATE_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # Complete phase 1
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        # Phase 2 should be GATED
        phase2 = repo.phases[phases[1].id]
        assert phase2.status == PhaseStatus.GATED

        # Should have emitted phase.needs_approval event
        approval_events = [e for e in bus.get_log(100) if e.event == "phase.needs_approval"]
        assert len(approval_events) == 1

    @pytest.mark.asyncio
    async def test_single_phase_completion_marks_saga_complete(self):
        """When all phases complete, saga should be COMPLETE."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        updated_saga = await repo.get_saga(saga.id)
        assert updated_saga.status == SagaStatus.COMPLETE


# ---------------------------------------------------------------------------
# Template loading: new pipeline format
# ---------------------------------------------------------------------------


class TestPipelineTemplateLoading:
    def test_load_review_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template(
            "review",
            BUNDLED_TEMPLATES_DIR,
            payload={
                "repo": "acme/app",
                "pr_number": "42",
                "branch": "feat/x",
                "base_branch": "main",
                "title": "My PR",
                "author": "alice",
                "pr_url": "https://github.com/acme/app/pull/42",
            },
        )
        assert template.name == "Review: acme/app#42"
        # First phase should be parallel (parallel-review)
        assert template.phases[0].parallel is True
        assert len(template.phases[0].raids) == 2
        assert template.phases[0].fan_in == "all_must_pass"
        # Second phase should have condition
        assert template.phases[1].condition is not None
        # Third phase should be a human gate
        assert template.phases[2].gate == "human"
        assert template.phases[2].needs_approval is True

    def test_load_investigate_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template(
            "investigate",
            BUNDLED_TEMPLATES_DIR,
            payload={"repo": "acme/app", "issue_number": "7", "title": "Bug", "author": "bob"},
        )
        assert template.name == "Investigate: Bug"
        assert len(template.phases) == 1
        assert template.phases[0].parallel is False

    def test_load_retro_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template("retro", BUNDLED_TEMPLATES_DIR, payload={"week": "2026-W15"})
        assert len(template.phases) == 2

    def test_load_reflect_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template(
            "reflect",
            BUNDLED_TEMPLATES_DIR,
            payload={
                "session_id": "sess-001",
                "repo": "acme/app",
                "outcome": "success",
                "duration_seconds": 120,
            },
        )
        assert "sess-001" in template.name

    def test_load_deploy_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template(
            "deploy",
            BUNDLED_TEMPLATES_DIR,
            payload={
                "repo": "acme/app",
                "sha_short": "abc1234",
                "sha": "abc1234def",
                "title": "feat: add feature",
                "pr_url": "https://github.com/acme/app/pull/99",
                "author": "carol",
            },
        )
        assert len(template.phases) == 3
        # Second stage has condition referencing smoke-test
        assert template.phases[1].condition is not None
        assert "smoke-test" in template.phases[1].condition

    def test_load_ship_template(self):
        from tyr.domain.templates import BUNDLED_TEMPLATES_DIR, load_template

        template = load_template(
            "ship",
            BUNDLED_TEMPLATES_DIR,
            payload={"repo": "acme/app", "branch": "feat/ship", "base_branch": "main"},
        )
        assert len(template.phases) == 4


# ---------------------------------------------------------------------------
# Error paths and edge cases
# ---------------------------------------------------------------------------


_CONDITION_PIPELINE = textwrap.dedent(
    """
    name: "condition-pipeline"
    feature_branch: "feat/cond"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        sequential:
          - name: "Review"
            persona: reviewer
            prompt: "Review code"
      - name: test
        sequential:
          - name: "Test"
            persona: qa-agent
            prompt: "Run tests"
        condition: "stages.review.verdict == pass"
    """
)

_CONDITION_FAIL_PIPELINE = textwrap.dedent(
    """
    name: "condition-fail-pipeline"
    feature_branch: "feat/cond-fail"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        sequential:
          - name: "Review"
            persona: reviewer
            prompt: "Review code"
      - name: test
        sequential:
          - name: "Test"
            persona: qa-agent
            prompt: "Run tests"
        condition: "stages.review.verdict == pass"
    """
)


class TestConditionBasedAdvancement:
    @pytest.mark.asyncio
    async def test_condition_passes_advances_to_next_phase(self):
        """When condition passes, saga advances to next phase."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_CONDITION_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # Complete phase 1 with pass
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        # Phase 1 complete, phase 2 should now be ACTIVE
        p1 = repo.phases[phase1.id]
        assert p1.status == PhaseStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_condition_failure_fails_saga(self):
        """When condition fails (verdict != pass), saga is marked FAILED."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_CONDITION_FAIL_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # Complete phase 1 with fail — condition "stages.review.verdict == pass" will not match
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "fail"})

        # Saga should be failed because fan-in failed (FAILED raid → has_failed=True)
        updated_saga = await repo.get_saga(saga.id)
        assert updated_saga.status == SagaStatus.FAILED

    @pytest.mark.asyncio
    async def test_no_volundr_adapter_does_not_raise(self):
        """When no Volundr adapter is available, dispatch logs error but doesn't raise."""

        class NullFactory:
            async def primary_for_owner(self, owner_id):
                return None

        repo = InMemorySagaRepository()
        bus = InMemoryEventBus()
        executor = TemplateAwarePipelineExecutor(
            saga_repo=repo,
            volundr_factory=NullFactory(),
            event_bus=bus,
            owner_id=_OWNER,
        )

        # Should not raise even without a Volundr adapter
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)
        assert saga.id in repo.sagas

    @pytest.mark.asyncio
    async def test_receive_outcome_for_untracked_saga_does_not_raise(self):
        """When saga is not in _saga_templates, base _finalize_phase is used without raising."""
        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)

        # Remove from tracking to simulate untracked saga
        executor._saga_templates.clear()

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        # Should not raise even when template context is gone
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

    @pytest.mark.asyncio
    async def test_advance_phase_with_no_volundr_does_not_raise(self):
        """When advancing to next phase but Volundr returns None, no exception raised."""

        class NullFactory:
            async def primary_for_owner(self, owner_id):
                return None

        repo = InMemorySagaRepository()
        bus = InMemoryEventBus()
        executor = TemplateAwarePipelineExecutor(
            saga_repo=repo,
            volundr_factory=NullFactory(),
            event_bus=bus,
            owner_id=_OWNER,
        )

        # 2-phase pipeline: complete phase 1 (no volundr = auto_start does nothing),
        # then receive outcome to trigger phase advance
        saga = await executor.create_from_yaml(_CONDITION_PIPELINE, auto_start=False)
        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # Mark raid as RUNNING manually (skip auto_start since no volundr)
        from datetime import UTC, datetime

        from tyr.domain.models import Raid

        updated = Raid(
            id=raids[0].id,
            phase_id=raids[0].phase_id,
            tracker_id=raids[0].tracker_id,
            name=raids[0].name,
            description=raids[0].description,
            acceptance_criteria=raids[0].acceptance_criteria,
            declared_files=raids[0].declared_files,
            estimate_hours=raids[0].estimate_hours,
            status=RaidStatus.RUNNING,
            confidence=raids[0].confidence,
            session_id="mock-session",
            branch=raids[0].branch,
            chronicle_summary=raids[0].chronicle_summary,
            pr_url=raids[0].pr_url,
            pr_id=raids[0].pr_id,
            retry_count=raids[0].retry_count,
            created_at=raids[0].created_at,
            updated_at=datetime.now(UTC),
        )
        await repo.save_raid(updated)

        # Receive outcome: phase 1 completes, tries to advance to phase 2 but no volundr
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})
        # Phase 1 should be COMPLETE even though Volundr can't dispatch phase 2
        assert repo.phases[phase1.id].status == PhaseStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_condition_eval_error_fails_saga(self):
        """When condition expression is invalid, saga is failed gracefully."""
        from unittest.mock import patch

        from tyr.domain.condition_evaluator import ConditionError

        volundr = StubVolundrPort()
        executor, repo, bus = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_CONDITION_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        # Patch evaluate_condition to raise ConditionError
        with patch(
            "tyr.domain.pipeline_executor.evaluate_condition",
            side_effect=ConditionError("bad condition"),
        ):
            await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        updated_saga = await repo.get_saga(saga.id)
        assert updated_saga.status == SagaStatus.FAILED

    @pytest.mark.asyncio
    async def test_base_create_from_yaml_works(self):
        """Base PipelineExecutor.create_from_yaml creates a saga."""
        from tyr.domain.pipeline_executor import PipelineExecutor

        repo = InMemorySagaRepository()
        bus = InMemoryEventBus()
        executor = PipelineExecutor(
            saga_repo=repo,
            volundr_factory=StubVolundrFactory(StubVolundrPort()),
            event_bus=bus,
            owner_id=_OWNER,
        )

        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=False)
        assert saga.id in repo.sagas


# ---------------------------------------------------------------------------
# NIU-597: Stage context injection
# ---------------------------------------------------------------------------

_STAGE_REF_PIPELINE = textwrap.dedent(
    """
    name: "ref-pipeline"
    feature_branch: "feat/refs"
    base_branch: "main"
    repos:
      - "test/repo"
    stages:
      - name: review
        sequential:
          - name: "Reviewer"
            persona: reviewer
            prompt: "Review the code"
      - name: test
        sequential:
          - name: "Tester"
            persona: qa-agent
            prompt: "Run tests. Review verdict: {stages.review.verdict}"
    """
)


class TestBuildStageContextFromOutcomes:
    def test_participant_names_in_context(self):
        outcomes = {
            "reviewer": {"verdict": "pass", "findings_count": 3, "summary": "Minor issues only"},
            "security-auditor": {
                "verdict": "pass",
                "critical_findings": 0,
                "summary": "No security issues",
            },
        }
        context = build_stage_context_from_outcomes("review", outcomes)
        assert "reviewer" in context
        assert "security-auditor" in context

    def test_verdict_in_context(self):
        outcomes = {"reviewer": {"verdict": "pass"}}
        context = build_stage_context_from_outcomes("review", outcomes)
        assert "verdict: pass" in context

    def test_summary_in_context(self):
        outcomes = {"reviewer": {"summary": "Minor issues only"}}
        context = build_stage_context_from_outcomes("review", outcomes)
        assert "Minor issues only" in context

    def test_stage_name_in_context(self):
        context = build_stage_context_from_outcomes("stage-one", {"agent": {"verdict": "pass"}})
        assert "stage-one" in context

    def test_empty_outcome_shows_completed_message(self):
        outcomes = {"reviewer": {}}
        context = build_stage_context_from_outcomes("review", outcomes)
        assert "completed (no structured outcome)" in context

    def test_header_present(self):
        context = build_stage_context_from_outcomes("s", {"a": {"x": "y"}})
        assert "## Previous Stage Outcomes" in context


class TestMergeStageOutcomes:
    def test_verdicts_aggregated_pass(self):
        outcomes = {
            "reviewer": {"verdict": "pass"},
            "auditor": {"verdict": "pass"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert merged["verdict"] == "pass"

    def test_any_fail_gives_fail_merge(self):
        outcomes = {
            "reviewer": {"verdict": "pass"},
            "auditor": {"verdict": "fail"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert merged["verdict"] == "fail"

    def test_summaries_concatenated_with_participant_prefix(self):
        outcomes = {
            "reviewer": {"summary": "Minor issues"},
            "auditor": {"summary": "No security issues"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert "reviewer: Minor issues" in merged["summary"]
        assert "auditor: No security issues" in merged["summary"]

    def test_summary_joined_by_pipe(self):
        outcomes = {
            "a": {"summary": "first"},
            "b": {"summary": "second"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert " | " in merged["summary"]

    def test_numeric_fields_summed(self):
        outcomes = {
            "reviewer": {"findings_count": 3},
            "auditor": {"findings_count": 2},
        }
        merged = merge_stage_outcomes(outcomes)
        assert merged["findings_count"] == 5

    def test_non_numeric_fields_last_writer_wins(self):
        outcomes = {
            "a": {"tag": "alpha"},
            "b": {"tag": "beta"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert merged["tag"] == "beta"

    def test_all_must_pass_fan_in_one_fail(self):
        outcomes = {
            "a": {"verdict": "pass"},
            "b": {"verdict": "fail"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="all_must_pass")
        assert merged["verdict"] == "fail"

    def test_all_must_pass_fan_in_all_pass(self):
        outcomes = {
            "a": {"verdict": "pass"},
            "b": {"verdict": "pass"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="all_must_pass")
        assert merged["verdict"] == "pass"

    def test_any_pass_fan_in_one_pass(self):
        outcomes = {
            "a": {"verdict": "fail"},
            "b": {"verdict": "pass"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="any_pass")
        assert merged["verdict"] == "pass"

    def test_any_pass_fan_in_all_fail(self):
        outcomes = {
            "a": {"verdict": "fail"},
            "b": {"verdict": "fail"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="any_pass")
        assert merged["verdict"] == "fail"

    def test_majority_fan_in_majority_pass(self):
        outcomes = {
            "a": {"verdict": "pass"},
            "b": {"verdict": "pass"},
            "c": {"verdict": "fail"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="majority")
        assert merged["verdict"] == "pass"

    def test_majority_fan_in_exactly_half(self):
        outcomes = {
            "a": {"verdict": "pass"},
            "b": {"verdict": "fail"},
        }
        merged = merge_stage_outcomes(outcomes, fan_in="majority")
        assert merged["verdict"] == "fail"

    def test_empty_outcomes_returns_empty_dict(self):
        assert merge_stage_outcomes({}) == {}

    def test_internal_accumulators_not_in_result(self):
        outcomes = {
            "a": {"verdict": "pass", "summary": "ok"},
            "b": {"verdict": "pass", "summary": "fine"},
        }
        merged = merge_stage_outcomes(outcomes)
        assert "verdicts" not in merged
        assert "summaries" not in merged

    def test_float_numeric_fields_summed(self):
        outcomes = {
            "a": {"score": 1.5},
            "b": {"score": 2.5},
        }
        merged = merge_stage_outcomes(outcomes)
        assert merged["score"] == pytest.approx(4.0)


class TestInterpolateStageRefs:
    def test_basic_interpolation(self):
        prompt = "Result: {stages.review.verdict}"
        resolved = interpolate_stage_refs(prompt, {"review": {"verdict": "pass"}})
        assert resolved == "Result: pass"

    def test_missing_field_becomes_empty_string(self):
        prompt = "Result: {stages.review.missing_field}"
        resolved = interpolate_stage_refs(prompt, {"review": {"verdict": "pass"}})
        assert resolved == "Result: "

    def test_missing_stage_becomes_empty_string(self):
        prompt = "Result: {stages.nonexistent.verdict}"
        resolved = interpolate_stage_refs(prompt, {})
        assert resolved == "Result: "

    def test_multiple_refs_resolved(self):
        prompt = "{stages.review.verdict} - {stages.review.summary}"
        resolved = interpolate_stage_refs(
            prompt, {"review": {"verdict": "pass", "summary": "All good"}}
        )
        assert "pass" in resolved
        assert "All good" in resolved

    def test_no_refs_unchanged(self):
        prompt = "No stage refs here."
        resolved = interpolate_stage_refs(prompt, {"review": {"verdict": "pass"}})
        assert resolved == "No stage refs here."

    def test_numeric_value_converted_to_string(self):
        prompt = "Count: {stages.review.findings_count}"
        resolved = interpolate_stage_refs(prompt, {"review": {"findings_count": 5}})
        assert resolved == "Count: 5"


class TestContextInjectionE2E:
    @pytest.mark.asyncio
    async def test_stage2_receives_stage1_outcomes_in_prompt(self):
        """Stage 2 persona gets stage 1 outcomes injected into its initial_prompt."""
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_PARALLEL_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        phase1 = phases[0]
        raids = await repo.get_raids_by_phase(phase1.id)

        # Complete both stage 1 raids
        await executor.receive_outcome(
            raid_id=raids[0].id,
            outcome={"verdict": "pass", "summary": "Minor issues only"},
        )
        await executor.receive_outcome(
            raid_id=raids[1].id,
            outcome={"verdict": "pass", "critical_findings": 0},
        )

        # Phase 1 spawned 2 raids, phase 2 spawns 1 raid → 3 total
        assert len(volundr.spawned) == 3
        stage2_prompt = volundr.spawned[2].initial_prompt
        assert "## Previous Stage Outcomes" in stage2_prompt
        assert "review" in stage2_prompt

    @pytest.mark.asyncio
    async def test_stage2_context_includes_stage1_verdicts(self):
        """The context block contains participant verdicts from stage 1."""
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_PARALLEL_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        await executor.receive_outcome(
            raid_id=raids[0].id, outcome={"verdict": "pass", "summary": "looks good"}
        )
        await executor.receive_outcome(
            raid_id=raids[1].id, outcome={"verdict": "pass", "critical_findings": 0}
        )

        stage2_prompt = volundr.spawned[2].initial_prompt
        assert "verdict: pass" in stage2_prompt

    @pytest.mark.asyncio
    async def test_stage_ref_interpolation_in_template_prompt(self):
        """Template {stages.review.verdict} is resolved to the actual verdict value."""
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        saga = await executor.create_from_yaml(_STAGE_REF_PIPELINE, auto_start=True)

        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)

        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        # Stage 2 dispatched — prompt should have {stages.review.verdict} resolved
        assert len(volundr.spawned) == 2
        stage2_prompt = volundr.spawned[1].initial_prompt
        assert "pass" in stage2_prompt
        assert "{stages.review.verdict}" not in stage2_prompt

    @pytest.mark.asyncio
    async def test_no_context_injected_for_first_phase(self):
        """First phase dispatched without any context block."""
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor(volundr=volundr)
        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)

        assert len(volundr.spawned) == 1
        # First phase has no prior stages → no context block
        assert "## Previous Stage Outcomes" not in volundr.spawned[0].initial_prompt

    @pytest.mark.asyncio
    async def test_context_injection_e2e(self):
        """E2E: Stage 2 persona gets stage 1 outcomes injected (spec test case)."""
        stage1_outcomes = {
            "reviewer": {"verdict": "pass", "findings_count": 3, "summary": "Minor issues only"},
            "security-auditor": {
                "verdict": "pass",
                "critical_findings": 0,
                "summary": "No security issues",
            },
        }

        context = build_stage_context_from_outcomes("review", stage1_outcomes)
        assert "reviewer" in context
        assert "verdict: pass" in context
        assert "Minor issues only" in context
        assert "security-auditor" in context

        prompt = "Run tests. Review result: {stages.review.verdict}. {stages.review.summary}"
        resolved = interpolate_stage_refs(prompt, {"review": merge_stage_outcomes(stage1_outcomes)})
        assert "pass" in resolved
        assert "Minor issues only" in resolved


# ---------------------------------------------------------------------------
# NIU-644: flock_flow reference + per-stage persona_overrides merge
# ---------------------------------------------------------------------------


def _make_flow(
    name: str = "code-review-flow",
    reviewer_alias: str = "balanced",
) -> FlockFlowConfig:
    """Build a minimal FlockFlowConfig for tests."""
    return FlockFlowConfig(
        name=name,
        personas=[
            FlockPersonaOverride(
                name="reviewer",
                llm={"primary_alias": reviewer_alias},
                system_prompt_extra="Standard review instructions.",
            ),
            FlockPersonaOverride(name="security-auditor"),
        ],
    )


def _make_executor_with_flow(
    flow_provider: StubFlockFlowProvider | None = None,
    volundr: StubVolundrPort | None = None,
) -> tuple[TemplateAwarePipelineExecutor, InMemorySagaRepository, InMemoryEventBus]:
    repo = InMemorySagaRepository()
    bus = InMemoryEventBus()
    factory = StubVolundrFactory(volundr or StubVolundrPort())
    executor = TemplateAwarePipelineExecutor(
        saga_repo=repo,
        volundr_factory=factory,
        event_bus=bus,
        owner_id=_OWNER,
        flow_provider=flow_provider,
    )
    return executor, repo, bus


class TestMergePersonaOverride:
    """Unit tests for the merge_persona_override helper."""

    def test_llm_alias_overridden(self):
        flow_persona = {"name": "reviewer", "llm": {"primary_alias": "balanced"}}
        override = {"llm": {"primary_alias": "powerful"}}
        result = merge_persona_override(flow_persona, override)
        assert result["llm"]["primary_alias"] == "powerful"

    def test_llm_thinking_added(self):
        flow_persona = {"name": "reviewer", "llm": {"primary_alias": "balanced"}}
        override = {"llm": {"thinking_enabled": True}}
        result = merge_persona_override(flow_persona, override)
        assert result["llm"]["thinking_enabled"] is True
        assert result["llm"]["primary_alias"] == "balanced"  # preserved

    def test_system_prompt_extra_concatenated(self):
        flow_persona = {"name": "reviewer", "system_prompt_extra": "Base instructions."}
        override = {"system_prompt_extra": "Extra context."}
        result = merge_persona_override(flow_persona, override)
        assert "Base instructions." in result["system_prompt_extra"]
        assert "Extra context." in result["system_prompt_extra"]

    def test_name_always_preserved(self):
        flow_persona = {"name": "reviewer"}
        override = {"name": "should-be-ignored", "llm": {"primary_alias": "powerful"}}
        result = merge_persona_override(flow_persona, override)
        assert result["name"] == "reviewer"

    def test_empty_override_leaves_persona_unchanged(self):
        flow_persona = {"name": "reviewer", "llm": {"primary_alias": "balanced"}}
        result = merge_persona_override(flow_persona, {})
        assert result == flow_persona

    def test_extra_non_empty_fields_applied(self):
        flow_persona = {"name": "reviewer"}
        override = {"iteration_budget": 5}
        result = merge_persona_override(flow_persona, override)
        assert result["iteration_budget"] == 5

    def test_zero_extra_field_not_applied(self):
        """Zero values are treated as 'inherit' — not applied."""
        flow_persona = {"name": "reviewer", "iteration_budget": 3}
        override = {"iteration_budget": 0}
        result = merge_persona_override(flow_persona, override)
        # 0 is falsy → not overridden
        assert result["iteration_budget"] == 3


class TestBuildFlockWorkloadConfig:
    """Unit tests for build_flock_workload_config."""

    def test_no_flow_name_returns_none(self):
        from tyr.domain.templates import TemplateRaid

        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="do it",
            persona="reviewer",
        )
        assert build_flock_workload_config("", tpl_raid, StubFlockFlowProvider(), "prompt") is None

    def test_no_provider_returns_none(self):
        from tyr.domain.templates import TemplateRaid

        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="do it",
            persona="reviewer",
        )
        assert build_flock_workload_config("my-flow", tpl_raid, None, "prompt") is None

    def test_unknown_flow_returns_none(self):
        from tyr.domain.templates import TemplateRaid

        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="do it",
            persona="reviewer",
        )
        provider = StubFlockFlowProvider()  # empty — no flows
        assert build_flock_workload_config("no-such-flow", tpl_raid, provider, "prompt") is None

    def test_flow_only_returns_flow_personas(self):
        from tyr.domain.templates import TemplateRaid

        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="review it",
            persona="reviewer",
        )
        config = build_flock_workload_config("code-review-flow", tpl_raid, provider, "review it")
        assert config is not None
        assert len(config["personas"]) == 2
        reviewer = next(p for p in config["personas"] if p["name"] == "reviewer")
        assert reviewer["llm"]["primary_alias"] == "balanced"

    def test_initiative_context_set_to_initial_prompt(self):
        from tyr.domain.templates import TemplateRaid

        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="my prompt",
            persona="reviewer",
        )
        config = build_flock_workload_config("code-review-flow", tpl_raid, provider, "my prompt")
        assert config["initiative_context"] == "my prompt"

    def test_stage_override_merged_onto_matching_persona(self):
        from tyr.domain.templates import TemplateRaid

        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="review it",
            persona="reviewer",
            persona_overrides={"llm": {"primary_alias": "powerful", "thinking_enabled": True}},
        )
        config = build_flock_workload_config("code-review-flow", tpl_raid, provider, "review it")
        assert config is not None
        reviewer = next(p for p in config["personas"] if p["name"] == "reviewer")
        assert reviewer["llm"]["primary_alias"] == "powerful"
        assert reviewer["llm"]["thinking_enabled"] is True

    def test_non_matching_persona_unaffected_by_override(self):
        from tyr.domain.templates import TemplateRaid

        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="review it",
            persona="reviewer",
            persona_overrides={"llm": {"primary_alias": "powerful"}},
        )
        config = build_flock_workload_config("code-review-flow", tpl_raid, provider, "review it")
        # security-auditor has no overrides → should be unchanged
        auditor = next(p for p in config["personas"] if p["name"] == "security-auditor")
        assert "llm" not in auditor  # flow didn't set llm for auditor

    def test_mimir_url_included_when_set(self):
        from tyr.domain.templates import TemplateRaid

        flow = FlockFlowConfig(
            name="my-flow",
            mimir_hosted_url="https://mimir.example.com",
            personas=[FlockPersonaOverride(name="reviewer")],
        )
        provider = StubFlockFlowProvider({"my-flow": flow})
        tpl_raid = TemplateRaid(
            name="r",
            description="",
            acceptance_criteria=[],
            declared_files=[],
            estimate_hours=1.0,
            prompt="p",
            persona="reviewer",
        )
        config = build_flock_workload_config("my-flow", tpl_raid, provider, "p")
        assert config["mimir_hosted_url"] == "https://mimir.example.com"


# ---------------------------------------------------------------------------
# NIU-644 E2E: YAML → executor → SpawnRequest
# ---------------------------------------------------------------------------

_FLOCK_FLOW_PIPELINE = textwrap.dedent(
    """
    name: "flock-pipeline"
    feature_branch: "feat/flock"
    base_branch: "main"
    repos:
      - "test/repo"
    flock_flow: code-review-flow
    stages:
      - name: review
        parallel:
          - persona: reviewer
            prompt: "Review this code"
          - persona: security-auditor
            prompt: "Audit this code"
        fan_in: all_must_pass
    """
)

_FLOCK_FLOW_WITH_OVERRIDE_PIPELINE = textwrap.dedent(
    """
    name: "flock-override-pipeline"
    feature_branch: "feat/flock"
    base_branch: "main"
    repos:
      - "test/repo"
    flock_flow: code-review-flow
    stages:
      - name: review
        parallel:
          - persona: reviewer
            prompt: "Review this code"
            persona_overrides:
              llm:
                primary_alias: powerful
                thinking_enabled: true
              system_prompt_extra: |
                Production-critical; be thorough.
          - persona: security-auditor
            prompt: "Audit this code"
        fan_in: all_must_pass
    """
)


class TestFlockFlowPipelineDispatch:
    """E2E tests: YAML with flock_flow → executor → SpawnRequest assertions."""

    @pytest.mark.asyncio
    async def test_flow_only_dispatch_uses_ravn_flock_workload_type(self):
        """With flock_flow set and no overrides, workload_type is ravn_flock."""
        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(provider, volundr)

        await executor.create_from_yaml(_FLOCK_FLOW_PIPELINE, auto_start=True)

        assert len(volundr.spawned) == 2
        for req in volundr.spawned:
            assert req.workload_type == "ravn_flock"

    @pytest.mark.asyncio
    async def test_flow_only_dispatch_carries_flow_personas(self):
        """With flock_flow and no overrides, workload_config.personas equals flow personas."""
        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(provider, volundr)

        await executor.create_from_yaml(_FLOCK_FLOW_PIPELINE, auto_start=True)

        for req in volundr.spawned:
            personas = req.workload_config.get("personas", [])
            names = [p["name"] for p in personas]
            assert "reviewer" in names
            assert "security-auditor" in names

    @pytest.mark.asyncio
    async def test_reviewer_override_applied_security_auditor_unchanged(self):
        """Stage override for reviewer: powerful alias; security-auditor uses flow default."""
        flow = _make_flow(reviewer_alias="balanced")
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(provider, volundr)

        await executor.create_from_yaml(_FLOCK_FLOW_WITH_OVERRIDE_PIPELINE, auto_start=True)

        # 2 raids dispatched (reviewer + security-auditor)
        assert len(volundr.spawned) == 2

        # The reviewer raid dispatch: its workload_config should have reviewer with powerful alias
        reviewer_req = next(r for r in volundr.spawned if r.profile == "reviewer")
        reviewer_personas = reviewer_req.workload_config["personas"]
        reviewer_p = next(p for p in reviewer_personas if p["name"] == "reviewer")
        assert reviewer_p["llm"]["primary_alias"] == "powerful"
        assert reviewer_p["llm"]["thinking_enabled"] is True
        assert "Production-critical" in reviewer_p.get("system_prompt_extra", "")

        # The security-auditor raid dispatch: its workload_config should have auditor unchanged
        auditor_req = next(r for r in volundr.spawned if r.profile == "security-auditor")
        auditor_personas = auditor_req.workload_config["personas"]
        reviewer_in_auditor_dispatch = next(p for p in auditor_personas if p["name"] == "reviewer")
        # In the auditor's dispatch, the reviewer persona uses flow default (no override applied)
        assert reviewer_in_auditor_dispatch["llm"]["primary_alias"] == "balanced"

    @pytest.mark.asyncio
    async def test_no_flock_flow_solo_dispatch(self):
        """Without flock_flow, workload_type is default (solo dispatch)."""
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(None, volundr)

        await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=True)

        assert len(volundr.spawned) == 1
        assert volundr.spawned[0].workload_type == "default"
        assert volundr.spawned[0].workload_config == {}

    @pytest.mark.asyncio
    async def test_unknown_flow_raises_value_error(self):
        """When flow is referenced but not registered, create_from_yaml raises ValueError."""
        provider = StubFlockFlowProvider()  # empty — flow not registered
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(provider, volundr)

        with pytest.raises(ValueError, match="not found"):
            await executor.create_from_yaml(_FLOCK_FLOW_PIPELINE, auto_start=True)

        # Nothing dispatched — error raised before dispatch
        assert len(volundr.spawned) == 0

    @pytest.mark.asyncio
    async def test_flock_flow_stored_in_executor(self):
        """The saga's flock_flow name is stored in _saga_flock_flows."""
        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        executor, _, _ = _make_executor_with_flow(provider)

        saga = await executor.create_from_yaml(_FLOCK_FLOW_PIPELINE, auto_start=False)

        assert executor._saga_flock_flows.get(str(saga.id)) == "code-review-flow"

    @pytest.mark.asyncio
    async def test_no_flock_flow_stored_as_none(self):
        """Without flock_flow in YAML, stored value is None."""
        executor, _, _ = _make_executor_with_flow(None)

        saga = await executor.create_from_yaml(_SINGLE_STAGE_PIPELINE, auto_start=False)

        assert executor._saga_flock_flows.get(str(saga.id)) is None

    @pytest.mark.asyncio
    async def test_flock_flow_used_in_second_stage(self):
        """After phase 1 completes, phase 2 is also dispatched with flock config."""
        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = StubVolundrPort()
        executor, repo, _ = _make_executor_with_flow(provider, volundr)

        two_stage_yaml = textwrap.dedent(
            """
            name: "two-stage-flock"
            feature_branch: "feat/f"
            base_branch: "main"
            repos: ["test/repo"]
            flock_flow: code-review-flow
            stages:
              - name: review
                sequential:
                  - persona: reviewer
                    prompt: "Review it"
              - name: test
                sequential:
                  - persona: security-auditor
                    prompt: "Audit it"
            """
        )
        saga = await executor.create_from_yaml(two_stage_yaml, auto_start=True)

        # Phase 1 dispatched
        assert len(volundr.spawned) == 1
        p1_req = volundr.spawned[0]
        assert p1_req.workload_type == "ravn_flock"

        # Complete phase 1
        phases = await repo.get_phases_by_saga(saga.id)
        raids = await repo.get_raids_by_phase(phases[0].id)
        await executor.receive_outcome(raid_id=raids[0].id, outcome={"verdict": "pass"})

        # Phase 2 dispatched — also flock
        assert len(volundr.spawned) == 2
        p2_req = volundr.spawned[1]
        assert p2_req.workload_type == "ravn_flock"

    @pytest.mark.asyncio
    async def test_flow_only_dispatches_identical_workload_config_for_both_raids(self):
        """Flow-only: both raiders in a parallel stage get identical flow personas."""
        flow = _make_flow()
        provider = StubFlockFlowProvider({"code-review-flow": flow})
        volundr = StubVolundrPort()
        executor, _, _ = _make_executor_with_flow(provider, volundr)

        await executor.create_from_yaml(_FLOCK_FLOW_PIPELINE, auto_start=True)

        # Both requests should carry the same flow personas (no overrides)
        personas_0 = volundr.spawned[0].workload_config["personas"]
        personas_1 = volundr.spawned[1].workload_config["personas"]
        # Both should have reviewer with "balanced" alias (flow default)
        r0 = next(p for p in personas_0 if p["name"] == "reviewer")
        r1 = next(p for p in personas_1 if p["name"] == "reviewer")
        assert r0["llm"]["primary_alias"] == "balanced"
        assert r1["llm"]["primary_alias"] == "balanced"
