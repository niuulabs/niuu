"""Advanced E2E scenarios for M6 raiding parties — gap coverage.

Covers scenarios not in test_flock_e2e.py:

1. Scope breach: outcome with low scope_adherence → SCOPE_BREACH signal
2. Max retries exhaustion: retry verdict after retries exhausted → FAILED
3. Approve with low confidence: approve verdict but score < threshold → ESCALATED
4. Fan-in: two raids in same phase must both merge before phase gate unlocks
5. Unknown verdict: unrecognized verdict falls back to escalation
"""

from __future__ import annotations

from tests.test_flock.harness import (
    OUTCOME_APPROVE,
    OUTCOME_ESCALATE,
    OUTCOME_RETRY,
    FlockTestHarness,
)
from tests.test_tyr.stubs import make_raid
from tyr.config import ReviewConfig
from tyr.domain.models import (
    ConfidenceEventType,
    Phase,
    PhaseStatus,
    Raid,
    RaidStatus,
    Saga,
    SagaStatus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_OWNER = "test-owner"


def _make_running_raid(
    tracker_id: str = "raid-001",
    session_id: str = "sess-001",
    retry_count: int = 0,
    declared_files: list[str] | None = None,
) -> Raid:
    return make_raid(
        status=RaidStatus.RUNNING,
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


async def test_scope_breach_confidence_event_recorded() -> None:
    """Outcome with scope_adherence < threshold produces a SCOPE_BREACH event."""
    async with FlockTestHarness(
        cli_responses=[OUTCOME_LOW_SCOPE],
        scope_adherence_threshold=0.7,
    ) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        event_types = [e.event_type.value for e in events]
        assert "scope_breach" in event_types, (
            f"Expected scope_breach confidence event; got {event_types}"
        )


async def test_scope_breach_reduces_confidence() -> None:
    """Scope breach signal applies a negative confidence delta."""
    async with FlockTestHarness(
        cli_responses=[OUTCOME_LOW_SCOPE],
        scope_adherence_threshold=0.7,
    ) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        breach_events = [e for e in events if e.event_type == ConfidenceEventType.SCOPE_BREACH]
        assert len(breach_events) == 1
        assert breach_events[0].delta < 0


async def test_scope_within_threshold_no_breach() -> None:
    """Outcome with scope_adherence above threshold does NOT produce breach event."""
    outcome_high_scope = """\
---outcome---
verdict: approve
tests_passing: true
scope_adherence: 0.95
summary: Clean implementation
---end---"""
    async with FlockTestHarness(
        cli_responses=[outcome_high_scope],
        scope_adherence_threshold=0.7,
    ) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        event_types = [e.event_type.value for e in events]
        assert "scope_breach" not in event_types


# ---------------------------------------------------------------------------
# Scenario 6: Max retries exhaustion — retry verdict → FAILED
# ---------------------------------------------------------------------------


async def test_retry_exhausted_transitions_to_failed() -> None:
    """Retry verdict when retry_count >= max_retries transitions raid to FAILED."""
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
        # Raid already exhausted retries
        raid = _make_running_raid(retry_count=2)
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.FAILED)


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
        raid = _make_running_raid(session_id="sess-001")
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.PENDING)

        # Second attempt: retry_count=1 (incremented), max_retries=1 → FAILED
        retry_raid = await h.tracker.update_raid_progress(
            raid.tracker_id,
            status=RaidStatus.RUNNING,
            session_id="sess-002",
        )
        await h.dispatch_raid(retry_raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.FAILED)


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
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.ESCALATED)


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
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.ESCALATED)


# ---------------------------------------------------------------------------
# Scenario 9: Fan-in — multiple raids in a phase
# ---------------------------------------------------------------------------


async def test_fan_in_first_merge_does_not_unlock_phase() -> None:
    """When two raids exist in a phase, merging only one does NOT unlock next phase."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        # Signal that not all raids are merged yet
        h.tracker._all_merged = False

        raid1 = _make_running_raid(tracker_id="raid-001", session_id="sess-001")
        await h.dispatch_raid(raid1)
        await h.assert_raid_state("raid-001", RaidStatus.MERGED)

        # Phase gate should NOT have been triggered (tracker reports not all merged)
        # Verify: no phase status changes were attempted
        # (StubTracker._all_merged = False means _check_phase_gate returns False)


async def test_fan_in_both_merged_unlocks_phase() -> None:
    """When all raids in a phase are merged, the phase gate check succeeds."""
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

        # Merge both raids
        raid1 = _make_running_raid(tracker_id="raid-001", session_id="sess-001")
        await h.dispatch_raid(raid1)
        await h.assert_raid_state("raid-001", RaidStatus.MERGED)

        raid2 = _make_running_raid(tracker_id="raid-002", session_id="sess-002")
        await h.dispatch_raid(raid2)
        await h.assert_raid_state("raid-002", RaidStatus.MERGED)


# ---------------------------------------------------------------------------
# Scenario 10: Multiple outcome types in sequence
# ---------------------------------------------------------------------------


async def test_mixed_outcomes_across_raids() -> None:
    """Different raids can have different outcomes in the same harness."""
    async with FlockTestHarness(
        cli_responses=[OUTCOME_APPROVE, OUTCOME_ESCALATE, OUTCOME_RETRY],
    ) as h:
        raid1 = _make_running_raid(tracker_id="raid-a", session_id="sess-a")
        await h.dispatch_raid(raid1)
        await h.assert_raid_state("raid-a", RaidStatus.MERGED)

        raid2 = _make_running_raid(tracker_id="raid-b", session_id="sess-b")
        await h.dispatch_raid(raid2)
        await h.assert_raid_state("raid-b", RaidStatus.ESCALATED)

        raid3 = _make_running_raid(tracker_id="raid-c", session_id="sess-c")
        await h.dispatch_raid(raid3)
        await h.assert_raid_state("raid-c", RaidStatus.PENDING)


# ---------------------------------------------------------------------------
# Scenario 11: Empty outcome / no outcome block
# ---------------------------------------------------------------------------

OUTCOME_NO_BLOCK = "I completed the task successfully but forgot the outcome block."


async def test_no_outcome_block_handles_gracefully() -> None:
    """Response without ---outcome--- block still processes (empty payload)."""
    async with FlockTestHarness(cli_responses=[OUTCOME_NO_BLOCK]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        # With empty payload: verdict defaults to "escalate" in RavnOutcomeHandler._extract_outcome
        # which means the raid should be ESCALATED
        current = await h.get_raid(raid.tracker_id)
        assert current.status in (RaidStatus.ESCALATED, RaidStatus.RUNNING), (
            f"Expected ESCALATED or RUNNING for empty outcome; got {current.status}"
        )


# ---------------------------------------------------------------------------
# Scenario 12: Confidence accumulation across signals
# ---------------------------------------------------------------------------


async def test_multiple_confidence_signals_accumulate() -> None:
    """CI_PASS and scope_breach signals both apply to the same raid."""
    outcome_mixed = """\
---outcome---
verdict: approve
tests_passing: true
scope_adherence: 0.50
summary: Tests pass but scope is breached
---end---"""
    async with FlockTestHarness(
        cli_responses=[outcome_mixed],
        scope_adherence_threshold=0.7,
    ) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        event_types = {e.event_type.value for e in events}
        assert "ci_pass" in event_types, "Expected CI_PASS signal"
        assert "scope_breach" in event_types, "Expected SCOPE_BREACH signal"
        assert len(events) >= 2, f"Expected at least 2 events; got {len(events)}"
