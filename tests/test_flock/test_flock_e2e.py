"""NIU-617 — End-to-end raiding party integration tests.

Validates the full loop:
    Tyr dispatches raid
    → SkuldMeshAdapter receives work_request
    → MockCLITransport returns outcome block
    → ravn.task.completed published on InProcessBus
    → RavnOutcomeHandler routes to ReviewEngine
    → Raid state transitions correctly

Test scenarios
--------------
1. Happy path: approve verdict + CI pass  →  MERGED
2. Retry path: first attempt returns retry, second returns approve  →  MERGED
3. Escalation path: verdict=escalate  →  ESCALATED
4. Mimir persistence: coordinator writes to project/ mount → hosted survives
   local teardown (clear()) while local is empty

All scenarios run in-process with no K8s, no real CLI, no real Anthropic API.
"""

from __future__ import annotations

import pytest

from tests.test_flock.harness import (
    OUTCOME_APPROVE,
    OUTCOME_ESCALATE,
    OUTCOME_RETRY,
    FlockTestHarness,
)
from tests.test_tyr.stubs import make_raid
from tyr.domain.models import Raid, RaidStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_running_raid(
    tracker_id: str = "raid-001",
    session_id: str = "sess-001",
    retry_count: int = 0,
) -> Raid:
    return make_raid(
        status=RaidStatus.RUNNING,
        confidence=0.5,
        session_id=session_id,
        retry_count=retry_count,
        tracker_id=tracker_id,
    )


# ---------------------------------------------------------------------------
# Scenario 1: Happy path — approve verdict + CI pass → MERGED
# ---------------------------------------------------------------------------


async def test_happy_path_approve_to_merged() -> None:
    """Raid with approve verdict and tests_passing=true reaches MERGED state."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.MERGED)


async def test_happy_path_outcome_handler_subscribed() -> None:
    """RavnOutcomeHandler is running after start()."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        assert h.outcome_handler.is_running


async def test_happy_path_skuld_receives_prompt() -> None:
    """Skuld feeds the work_request prompt to the CLI transport."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        assert len(h.cli.received_prompts) == 1
        assert "Implement:" in h.cli.received_prompts[0]


async def test_happy_path_confidence_event_recorded() -> None:
    """CI_PASS confidence event is recorded in the tracker after approve."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        event_types = [e.event_type for e in events]
        assert any(et.value == "ci_pass" for et in event_types), (
            f"Expected ci_pass confidence event; got {event_types}"
        )


# ---------------------------------------------------------------------------
# Scenario 2: Retry path — first attempt retry, second attempt approve
# ---------------------------------------------------------------------------


async def test_retry_path_first_attempt_sets_pending() -> None:
    """First attempt with retry verdict transitions raid to PENDING."""
    async with FlockTestHarness(cli_responses=[OUTCOME_RETRY, OUTCOME_APPROVE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.PENDING)


async def test_retry_path_second_attempt_merges() -> None:
    """Two dispatches: retry then approve → final state is MERGED."""
    async with FlockTestHarness(cli_responses=[OUTCOME_RETRY, OUTCOME_APPROVE]) as h:
        # First attempt
        raid = _make_running_raid(session_id="sess-001")
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.PENDING)

        # Simulate re-dispatch: raid PENDING → RUNNING with new session_id
        retry_raid = await h.tracker.update_raid_progress(
            raid.tracker_id,
            status=RaidStatus.RUNNING,
            session_id="sess-002",
        )
        await h.dispatch_raid(retry_raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.MERGED)


async def test_retry_path_cli_called_twice() -> None:
    """CLI transport is invoked once per dispatch_raid call."""
    async with FlockTestHarness(cli_responses=[OUTCOME_RETRY, OUTCOME_APPROVE]) as h:
        raid = _make_running_raid(session_id="sess-001")
        await h.dispatch_raid(raid)

        retry_raid = await h.tracker.update_raid_progress(
            raid.tracker_id,
            status=RaidStatus.RUNNING,
            session_id="sess-002",
        )
        await h.dispatch_raid(retry_raid)

        assert h.cli._call_index == 2


# ---------------------------------------------------------------------------
# Scenario 3: Escalation path — verdict=escalate → ESCALATED
# ---------------------------------------------------------------------------


async def test_escalation_path_escalated_state() -> None:
    """Raid with escalate verdict and tests_passing=false reaches ESCALATED."""
    async with FlockTestHarness(cli_responses=[OUTCOME_ESCALATE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.ESCALATED)


async def test_escalation_path_confidence_reduced() -> None:
    """Escalation path: CI_FAIL confidence event recorded."""
    async with FlockTestHarness(cli_responses=[OUTCOME_ESCALATE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        events = await h.tracker.get_confidence_events(raid.tracker_id)
        event_types = [e.event_type.value for e in events]
        assert "ci_fail" in event_types, f"Expected ci_fail confidence event; got {event_types}"


async def test_escalation_path_raid_not_merged() -> None:
    """Escalated raid is NOT in MERGED state."""
    async with FlockTestHarness(cli_responses=[OUTCOME_ESCALATE]) as h:
        raid = _make_running_raid()
        await h.dispatch_raid(raid)
        current = await h.get_raid(raid.tracker_id)
        assert current.status != RaidStatus.MERGED


# ---------------------------------------------------------------------------
# Scenario 4: Mimir persistence
# ---------------------------------------------------------------------------


async def test_mimir_project_writes_to_hosted_mount() -> None:
    """Writes to project/ path route to hosted mount, not local."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        await h.mimir.upsert_page("project/decisions/approach.md", "# Approach\nDecision made.")

        # Hosted mount has the page
        content = await h.hosted_mimir.read_page("project/decisions/approach.md")
        assert "Decision made" in content

        # Local mount does NOT have the project/ page
        with pytest.raises(FileNotFoundError):
            await h.local_mimir.read_page("project/decisions/approach.md")


async def test_mimir_default_writes_to_local_mount() -> None:
    """Writes without matching prefix route to local mount by default."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        await h.mimir.upsert_page("self/notes.md", "# Personal notes")

        content = await h.local_mimir.read_page("self/notes.md")
        assert "Personal notes" in content

        with pytest.raises(FileNotFoundError):
            await h.hosted_mimir.read_page("self/notes.md")


async def test_mimir_hosted_survives_local_teardown() -> None:
    """Hosted pages survive after local mount is cleared.

    Simulates flock pod teardown: local emptyDir is deleted but hosted
    Mímir (separate service) retains all pages written to project/.
    """
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        # Coordinator writes project-level decision during execution
        await h.mimir.upsert_page(
            "project/decisions/approach.md",
            "# Approach\nWe chose X over Y because ...",
        )
        # Also write a local page
        await h.mimir.upsert_page("self/scratch.md", "scratch notes")

        # Simulate local mount teardown (pod emptyDir deleted)
        h.local_mimir.clear()

        # Local page gone
        with pytest.raises(FileNotFoundError):
            await h.local_mimir.read_page("self/scratch.md")

        # Hosted page survived — composite reads from hosted as fallback
        content = await h.mimir.read_page("project/decisions/approach.md")
        assert "chose X over Y" in content


async def test_mimir_read_merges_both_mounts() -> None:
    """CompositeMimirAdapter search merges results from both mounts."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        await h.mimir.upsert_page("self/local.md", "local content")
        await h.mimir.upsert_page("project/shared.md", "shared content")

        all_pages = await h.mimir.list_pages()
        paths = {m.path for m in all_pages}
        assert "self/local.md" in paths
        assert "project/shared.md" in paths


# ---------------------------------------------------------------------------
# Scenario validation: correct Sleipnir event type fired
# ---------------------------------------------------------------------------


async def test_event_type_ravn_task_completed_consumed() -> None:
    """RavnOutcomeHandler consumes ravn.task.completed events (not other types)."""
    from datetime import UTC, datetime

    from sleipnir.domain.events import SleipnirEvent

    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        raid = _make_running_raid()
        await h.tracker.create_raid(raid)

        # Publish an irrelevant event — outcome handler should ignore it
        irrelevant = SleipnirEvent(
            event_type="tyr.saga.created",
            source="tyr",
            payload={},
            summary="irrelevant",
            urgency=0.1,
            domain="business",
            timestamp=datetime.now(UTC),
            correlation_id=raid.session_id,
        )
        await h.bus.publish(irrelevant)
        await h.bus.flush()

        # Raid should still be RUNNING (outcome handler didn't act on it)
        await h.assert_raid_state(raid.tracker_id, RaidStatus.RUNNING)


async def test_outcome_handler_ignores_missing_correlation_id() -> None:
    """Events without correlation_id are silently dropped."""
    from datetime import UTC, datetime

    from sleipnir.domain.events import SleipnirEvent

    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        orphan = SleipnirEvent(
            event_type="ravn.task.completed",
            source="ravn:coordinator",
            payload={"verdict": "approve", "tests_passing": True},
            summary="no correlation",
            urgency=0.8,
            domain="code",
            timestamp=datetime.now(UTC),
            correlation_id=None,
        )
        await h.bus.publish(orphan)
        await h.bus.flush()
        # No raid → no crash. Harness teardown should succeed cleanly.


async def test_outcome_handler_ignores_unknown_session() -> None:
    """Events with unknown correlation_id (no matching raid) are dropped."""
    from datetime import UTC, datetime

    from sleipnir.domain.events import SleipnirEvent

    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        unknown = SleipnirEvent(
            event_type="ravn.task.completed",
            source="ravn:coordinator",
            payload={"verdict": "approve"},
            summary="unknown session",
            urgency=0.8,
            domain="code",
            timestamp=datetime.now(UTC),
            correlation_id="nonexistent-session-xyz",
        )
        await h.bus.publish(unknown)
        await h.bus.flush()
        # No crash — handler logs a warning and drops the event.


# ---------------------------------------------------------------------------
# FlockTestHarness reusability
# ---------------------------------------------------------------------------


async def test_harness_reusable_across_raids() -> None:
    """A single harness instance can process multiple sequential raids."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        for i in range(3):
            raid = _make_running_raid(
                tracker_id=f"raid-{i:03d}",
                session_id=f"sess-{i:03d}",
            )
            await h.dispatch_raid(raid)
            await h.assert_raid_state(raid.tracker_id, RaidStatus.MERGED)


async def test_harness_cleanup_stops_all_components() -> None:
    """Harness stop() cleanly shuts down all components."""
    h = FlockTestHarness(cli_responses=[OUTCOME_APPROVE])
    await h.start()
    assert h.outcome_handler.is_running
    assert h.skuld.is_running
    await h.stop()
    assert not h.outcome_handler.is_running
    assert not h.skuld.is_running


async def test_harness_as_context_manager() -> None:
    """FlockTestHarness can be used as an async context manager."""
    async with FlockTestHarness(cli_responses=[OUTCOME_APPROVE]) as h:
        assert h.outcome_handler.is_running
    assert not h.outcome_handler.is_running


# ---------------------------------------------------------------------------
# Component unit tests — increase coverage of harness helpers
# ---------------------------------------------------------------------------


async def test_mock_cli_transport_empty_responses_raises() -> None:
    """MockCLITransport raises ValueError when constructed with empty list."""
    from tests.test_flock.harness import MockCLITransport

    with pytest.raises(ValueError, match="at least one response"):
        MockCLITransport([])


async def test_mock_cli_transport_start_stop() -> None:
    """MockCLITransport start/stop are no-ops."""
    from tests.test_flock.harness import MockCLITransport

    t = MockCLITransport(["hello"])
    await t.start()
    await t.stop()


async def test_mock_cli_transport_properties() -> None:
    """MockCLITransport properties return expected values."""
    from tests.test_flock.harness import MockCLITransport

    t = MockCLITransport(["response"])
    assert t.session_id == "mock-cli-session"
    assert t.last_result is None
    assert t.is_alive is True
    assert t.capabilities.send_message is True


async def test_inprocess_mesh_send_without_handler_raises() -> None:
    """InProcessMesh.send raises RuntimeError when no RPC handler is registered."""
    from tests.test_flock.harness import InProcessMesh

    mesh = InProcessMesh()
    with pytest.raises(RuntimeError, match="no RPC handler registered"):
        await mesh.send("some-peer", {"type": "work_request"})


async def test_inmemory_mimir_get_page() -> None:
    """InMemoryMimirPort.get_page returns a MimirPage with correct content."""
    from tests.test_flock.harness import InMemoryMimirPort

    m = InMemoryMimirPort()
    await m.upsert_page("tech/note.md", "hello world")
    page = await m.get_page("tech/note.md")
    assert page.content == "hello world"
    assert page.meta.path == "tech/note.md"


async def test_inmemory_mimir_get_page_not_found() -> None:
    """InMemoryMimirPort.get_page raises FileNotFoundError for missing path."""
    from tests.test_flock.harness import InMemoryMimirPort

    m = InMemoryMimirPort()
    with pytest.raises(FileNotFoundError):
        await m.get_page("nonexistent.md")


async def test_inmemory_mimir_stub_methods() -> None:
    """InMemoryMimirPort stub methods return empty results without error."""
    from datetime import UTC, datetime

    from niuu.domain.mimir import MimirSource, compute_content_hash
    from tests.test_flock.harness import InMemoryMimirPort

    m = InMemoryMimirPort()
    await m.upsert_page("p.md", "data")

    src = MimirSource(
        source_id="s1",
        content="x",
        title="t",
        source_type="text",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("x"),
    )
    assert await m.ingest(src) == []
    result = await m.query("what?")
    assert result.answer == ""
    assert await m.search("q") == []
    lint = await m.lint()
    assert lint.pages_checked == 1
    assert await m.read_source("x") is None
    assert await m.list_sources() == []
    assert await m.list_threads() == []
    assert await m.get_thread_queue() == []
    # state mutation stubs — should not raise
    from niuu.domain.mimir import ThreadState

    await m.update_thread_state("p.md", ThreadState.open)
    await m.assign_thread_owner("p.md", "owner-1")
    await m.update_thread_weight("p.md", 0.5)


async def test_stub_git_all_methods() -> None:
    """StubGit methods run without error and return expected stubs."""
    from tests.test_flock.harness import StubGit

    g = StubGit()
    await g.create_branch("repo", "branch", "main")
    await g.merge_branch("repo", "feature", "main")
    await g.delete_branch("repo", "feature")
    pr_id = await g.create_pr("repo", "feature", "main", "title")
    assert pr_id == "pr-stub-001"
    status = await g.get_pr_status("42")
    assert status.mergeable is True
    files = await g.get_pr_changed_files("42")
    assert files == []


async def test_dispatch_raid_not_started_raises() -> None:
    """dispatch_raid raises RuntimeError when harness is not started."""
    h = FlockTestHarness(cli_responses=[OUTCOME_APPROVE])
    raid = _make_running_raid()
    with pytest.raises(RuntimeError, match="start"):
        await h.dispatch_raid(raid)
