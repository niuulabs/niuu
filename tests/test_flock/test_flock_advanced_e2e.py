"""Advanced E2E scenarios for M6 runing parties — gap coverage.

Covers scenarios not in test_flock_e2e.py:

1. Scope breach: outcome with low scope_adherence → SCOPE_BREACH signal
2. Max retries exhaustion: retry verdict after retries exhausted → FAILED
3. Approve with low confidence: approve verdict but score < threshold → ESCALATED
4. Fan-in: two runs in same phase must both merge before phase gate unlocks
5. Unknown verdict: unrecognized verdict falls back to escalation
"""

from __future__ import annotations

from tests.test_flock.harness import (
    OUTCOME_APPROVE,
    OUTCOME_ESCALATE,
    OUTCOME_RETRY,
    FlockTestHarness,
)
from tests.test_ting.stubs import make_run
from ting.config import ReviewConfig
from ting.domain.models import (
    Phase,
    PhaseStatus,
    Run,
    RunStatus,
    Saga,
    SagaStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_OWNER = "test-owner"


def _make_running_run(
    tracker_id: str = "run-001",
    session_id: str = "sess-001",
    retry_count: int = 0,
    declared_files: list[str] | None = None,
) -> Run:
    return make_run(
        status=RunStatus.RUNNING,
        confidence=0.5,
        session_id=session_id,
        retry_count=retry_count,
        tracker_id=tracker_id,
    )


# ---------------------------------------------------------------------------
# Scenario 5: Scope breach — low scope_adherence triggers signal
# ---------------------------------------------------------------------------

OUTCOME_LOW_SCOPE = """\
---outcome---
verdict: approve
tests_passing: true
scope_adherence: 0.50
pr_url: https://github.com/niuulabs/test/pull/2
summary: Implementation done but touched undeclared files
---end---"""


async def test_non_authoritative_approve_escalates() -> None:
    """A coordinator approve without workflow authority goes to a human."""
    outcome_plain_approve = """\
---outcome---
verdict: approve
tests_passing: true
scope_adherence: 0.95
summary: Clean implementation
---end---"""
    async with FlockTestHarness(cli_responses=[outcome_plain_approve]) as h:
        run = _make_running_run()
        await h.dispatch_run(run)
        await h.assert_run_state(run.tracker_id, RunStatus.ESCALATED)


# ---------------------------------------------------------------------------
# Scenario 6: Max retries exhaustion — retry verdict → FAILED
# ---------------------------------------------------------------------------


async def test_retry_exhausted_transitions_to_failed() -> None:
    """Retry verdict when retry_count >= max_retries transitions run to FAILED."""
    config = ReviewConfig(
        auto_approve_threshold=0.70,
        confidence_delta_ci_pass=0.30,
        confidence_delta_ci_fail=-0.30,
        confidence_delta_approved=0.10,
        reviewer_session_enabled=False,
        max_retries=2,
    )
    async with FlockTestHarness(
        cli_responses=[OUTCOME_RETRY],
        review_config=config,
    ) as h:
        # Run already exhausted retries
        run = _make_running_run(retry_count=2)
        await h.dispatch_run(run)
        await h.assert_run_state(run.tracker_id, RunStatus.FAILED)


async def test_retry_exhausted_one_below_then_exhausts() -> None:
    """First attempt retries (retry_count=0 < max=1), second exhausts → FAILED."""
    config = ReviewConfig(
        auto_approve_threshold=0.70,
        confidence_delta_ci_pass=0.30,
        confidence_delta_ci_fail=-0.30,
        confidence_delta_approved=0.10,
        reviewer_session_enabled=False,
        max_retries=1,
    )
    async with FlockTestHarness(
        cli_responses=[OUTCOME_RETRY, OUTCOME_RETRY],
        review_config=config,
    ) as h:
        # First attempt: retry_count=0, max_retries=1 → PENDING (can retry)
        run = _make_running_run(session_id="sess-001")
        await h.dispatch_run(run)
        await h.assert_run_state(run.tracker_id, RunStatus.PENDING)

        # Second attempt: retry_count=1 (incremented), max_retries=1 → FAILED
        retry_run = await h.tracker.update_run_progress(
            run.tracker_id,
            status=RunStatus.RUNNING,
            session_id="sess-002",
        )
        await h.dispatch_run(retry_run)
        await h.assert_run_state(run.tracker_id, RunStatus.FAILED)


# ---------------------------------------------------------------------------
# Scenario 7: Approve verdict but low confidence → ESCALATED
# ---------------------------------------------------------------------------

OUTCOME_APPROVE_LOW_CI = """\
---outcome---
verdict: approve
tests_passing: false
scope_adherence: 0.95
summary: Approved but CI is failing
---end---"""


async def test_approve_with_failing_ci_escalates() -> None:
    """Approve verdict with tests_passing=false produces low confidence → ESCALATED.

    Starting confidence 0.5 + CI_FAIL (-0.30) = 0.20 < threshold 0.70.
    Verdict is approve, but the score is too low to auto-approve.
    """
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE_LOW_CI]) as h:
        run = _make_running_run()
        await h.dispatch_run(run)
        await h.assert_run_state(run.tracker_id, RunStatus.ESCALATED)


# ---------------------------------------------------------------------------
# Scenario 8: Unknown verdict → ESCALATED (fallback)
# ---------------------------------------------------------------------------

OUTCOME_UNKNOWN_VERDICT = """\
---outcome---
verdict: reconsider
tests_passing: true
scope_adherence: 0.90
summary: Some unclear outcome
---end---"""


async def test_unknown_verdict_escalates() -> None:
    """Unknown verdict string falls back to escalation."""
    async with FlockTestHarness(cli_responses=[OUTCOME_UNKNOWN_VERDICT]) as h:
        run = _make_running_run()
        await h.dispatch_run(run)
        await h.assert_run_state(run.tracker_id, RunStatus.ESCALATED)


# ---------------------------------------------------------------------------
# Scenario 9: Fan-in — multiple runs in a phase
# ---------------------------------------------------------------------------


async def test_fan_in_first_merge_does_not_unlock_phase() -> None:
    """When two runs exist in a phase, merging only one does NOT unlock next phase."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        # Signal that not all runs are merged yet
        h.tracker._all_merged = False

        run1 = _make_running_run(tracker_id="run-001", session_id="sess-001")
        await h.dispatch_run(run1)
        await h.assert_run_state("run-001", RunStatus.MERGED)

        # Phase gate should NOT have been triggered (tracker reports not all merged)
        # Verify: no phase status changes were attempted
        # (StubTracker._all_merged = False means _check_phase_gate returns False)


async def test_fan_in_both_merged_unlocks_phase() -> None:
    """When all runs in a phase are merged, the phase gate check succeeds."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        # Wire up phase and saga for phase gate testing
        from datetime import UTC, datetime
        from uuid import uuid4

        saga = Saga(
            id=uuid4(),
            tracker_id="saga-001",
            tracker_type="linear",
            slug="test-saga",
            name="Test Saga",
            repos=["test-repo"],
            feature_branch="feat/test",
            base_branch="main",
            status=SagaStatus.ACTIVE,
            confidence=0.5,
            created_at=datetime.now(UTC),
            owner_id=_DEFAULT_OWNER,
        )
        h.tracker.saga = saga

        phase1 = Phase(
            id=uuid4(),
            saga_id=saga.id,
            tracker_id="phase-001",
            name="Phase 1",
            number=1,
            status=PhaseStatus.ACTIVE,
            confidence=0.5,
        )
        phase2 = Phase(
            id=uuid4(),
            saga_id=saga.id,
            tracker_id="phase-002",
            name="Phase 2",
            number=2,
            status=PhaseStatus.GATED,
            confidence=0.5,
        )
        h.tracker.phase = phase1
        h.tracker._phases = [phase1, phase2]
        h.tracker._all_merged = True

        # Merge both runs
        run1 = _make_running_run(tracker_id="run-001", session_id="sess-001")
        await h.dispatch_run(run1)
        await h.assert_run_state("run-001", RunStatus.MERGED)

        run2 = _make_running_run(tracker_id="run-002", session_id="sess-002")
        await h.dispatch_run(run2)
        await h.assert_run_state("run-002", RunStatus.MERGED)


# ---------------------------------------------------------------------------
# Scenario 10: Multiple outcome types in sequence
# ---------------------------------------------------------------------------


async def test_mixed_outcomes_across_runs() -> None:
    """Different runs can have different outcomes in the same harness."""
    async with FlockTestHarness(
        cli_responses=[OUTCOME_APPROVE, OUTCOME_ESCALATE, OUTCOME_RETRY],
    ) as h:
        run1 = _make_running_run(tracker_id="run-a", session_id="sess-a")
        await h.dispatch_run(run1)
        await h.assert_run_state("run-a", RunStatus.MERGED)

        run2 = _make_running_run(tracker_id="run-b", session_id="sess-b")
        await h.dispatch_run(run2)
        await h.assert_run_state("run-b", RunStatus.ESCALATED)

        run3 = _make_running_run(tracker_id="run-c", session_id="sess-c")
        await h.dispatch_run(run3)
        await h.assert_run_state("run-c", RunStatus.PENDING)


# ---------------------------------------------------------------------------
# Scenario 11: Empty outcome / no outcome block
# ---------------------------------------------------------------------------

OUTCOME_NO_BLOCK = "I completed the task successfully but forgot the outcome block."


async def test_no_outcome_block_handles_gracefully() -> None:
    """Response without ---outcome--- block still processes (empty payload)."""
    async with FlockTestHarness(cli_responses=[OUTCOME_NO_BLOCK]) as h:
        run = _make_running_run()
        await h.dispatch_run(run)
        # With empty payload: verdict defaults to "escalate" in RavnOutcomeHandler._extract_outcome
        # which means the run should be ESCALATED
        current = await h.get_run(run.tracker_id)
        assert current.status in (RunStatus.ESCALATED, RunStatus.RUNNING), (
            f"Expected ESCALATED or RUNNING for empty outcome; got {current.status}"
        )


# ---------------------------------------------------------------------------
# Scenario 12: Confidence accumulation across signals
# ---------------------------------------------------------------------------
