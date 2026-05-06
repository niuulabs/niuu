"""Tests for the Sleipnir event catalog — NIU-582.

Covers:
- All factory functions return valid SleipnirEvent objects with correct fields.
- Integration: emit a catalog event via InProcessBus and verify the subscriber
  receives the expected payload.
"""

from __future__ import annotations

import pytest

from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    bifrost_budget_degraded,
    github_issue_opened,
    github_pr_merged,
    github_pr_opened,
    github_push_main,
    mimir_dream_completed,
    mimir_page_written,
    ravn_session_ended,
    ravn_session_started,
    ravn_task_completed,
    tyr_raid_needs_approval,
    tyr_saga_completed,
    tyr_saga_created,
    volundr_session_failed,
    volundr_session_started,
)
from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# ravn.session.started
# ---------------------------------------------------------------------------


def test_ravn_session_started_event_type():
    evt = ravn_session_started(
        session_id="sess-1",
        persona="ravn",
        repo_slug="niuulabs/niuu",
        source="ravn:agent-1",
    )
    assert isinstance(evt, SleipnirEvent)
    assert evt.event_type == registry.RAVN_SESSION_STARTED
    assert evt.payload["session_id"] == "sess-1"
    assert evt.payload["persona"] == "ravn"
    assert evt.payload["repo_slug"] == "niuulabs/niuu"
    assert evt.domain == "code"
    assert evt.urgency == pytest.approx(0.2)


def test_ravn_session_started_correlation_id_defaults_to_session_id():
    evt = ravn_session_started(
        session_id="sess-xyz",
        persona="ravn",
        repo_slug="repo",
        source="ravn",
    )
    assert evt.correlation_id == "sess-xyz"


def test_ravn_session_started_explicit_correlation_id():
    evt = ravn_session_started(
        session_id="sess-xyz",
        persona="ravn",
        repo_slug="repo",
        source="ravn",
        correlation_id="corr-override",
    )
    assert evt.correlation_id == "corr-override"


# ---------------------------------------------------------------------------
# ravn.session.ended
# ---------------------------------------------------------------------------


def test_ravn_session_ended_event_type():
    evt = ravn_session_ended(
        session_id="sess-2",
        persona="ravn",
        outcome="success",
        token_count=1000,
        duration_s=42.0,
        source="ravn:agent-2",
    )
    assert evt.event_type == registry.RAVN_SESSION_ENDED
    assert evt.payload["outcome"] == "success"
    assert evt.payload["token_count"] == 1000
    assert evt.payload["duration_s"] == pytest.approx(42.0)
    assert evt.domain == "code"


# ---------------------------------------------------------------------------
# ravn.task.completed
# ---------------------------------------------------------------------------


def test_ravn_task_completed_success_has_low_urgency():
    evt = ravn_task_completed(
        task_id="task-1",
        persona="ravn",
        outcome="success",
        source="ravn",
    )
    assert evt.event_type == registry.RAVN_TASK_COMPLETED
    assert evt.urgency == pytest.approx(0.2)


def test_ravn_task_completed_failure_has_higher_urgency():
    evt = ravn_task_completed(
        task_id="task-2",
        persona="ravn",
        outcome="failure",
        source="ravn",
    )
    assert evt.urgency == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# volundr.session.started
# ---------------------------------------------------------------------------


def test_volundr_session_started_fields():
    evt = volundr_session_started(
        session_id="vsess-1",
        user_id="user-abc",
        repo="niuulabs/niuu",
        branch="main",
        source="volundr",
    )
    assert evt.event_type == registry.VOLUNDR_SESSION_STARTED
    assert evt.payload["user_id"] == "user-abc"
    assert evt.payload["branch"] == "main"
    assert evt.domain == "code"


# ---------------------------------------------------------------------------
# volundr.session.failed
# ---------------------------------------------------------------------------


def test_volundr_session_failed_has_high_urgency():
    evt = volundr_session_failed(
        session_id="vsess-2",
        error="pod crash",
        user_id="user-xyz",
        source="volundr",
    )
    assert evt.event_type == registry.VOLUNDR_SESSION_FAILED
    assert evt.urgency >= 0.7
    assert evt.domain == "infrastructure"
    assert "pod crash" in evt.summary


# ---------------------------------------------------------------------------
# tyr.saga.created
# ---------------------------------------------------------------------------


def test_tyr_saga_created_fields():
    evt = tyr_saga_created(
        saga_id="saga-1",
        template="default",
        trigger_event="api.commit_saga",
        source="tyr",
    )
    assert evt.event_type == registry.TYR_SAGA_CREATED
    assert evt.payload["saga_id"] == "saga-1"
    assert evt.payload["template"] == "default"
    assert evt.domain == "code"


# ---------------------------------------------------------------------------
# tyr.saga.completed
# ---------------------------------------------------------------------------


def test_tyr_saga_completed_fields():
    evt = tyr_saga_completed(
        saga_id="saga-2",
        outcome="success",
        phases_completed=3,
        source="tyr",
    )
    assert evt.event_type == registry.TYR_SAGA_COMPLETED
    assert evt.payload["phases_completed"] == 3
    assert "saga-2" in evt.summary


# ---------------------------------------------------------------------------
# tyr.raid.needs_approval
# ---------------------------------------------------------------------------


def test_tyr_raid_needs_approval_high_urgency():
    evt = tyr_raid_needs_approval(
        raid_id="raid-1",
        saga_id="saga-1",
        description="PR ready for review",
        source="tyr",
    )
    assert evt.event_type == registry.TYR_RAID_NEEDS_APPROVAL
    assert evt.urgency >= 0.7
    assert evt.payload["raid_id"] == "raid-1"
    assert "PR ready" in evt.summary


# ---------------------------------------------------------------------------
# bifrost.budget.degraded
# ---------------------------------------------------------------------------


def test_bifrost_budget_degraded_summary_contains_pct():
    evt = bifrost_budget_degraded(
        tenant_id="tenant-abc",
        current_spend=0.80,
        cap=1.00,
        downgraded_to="claude-haiku-4-5",
        source="bifrost",
    )
    assert evt.event_type == registry.BIFROST_BUDGET_DEGRADED
    assert evt.domain == "infrastructure"
    assert "80.0%" in evt.summary
    assert evt.payload["pct_consumed"] == pytest.approx(80.0)


def test_bifrost_budget_degraded_zero_cap_returns_zero_pct():
    evt = bifrost_budget_degraded(
        tenant_id="t",
        current_spend=5.0,
        cap=0.0,
        downgraded_to="haiku",
        source="bifrost",
    )
    assert evt.payload["pct_consumed"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# mimir.page.written
# ---------------------------------------------------------------------------


def test_mimir_page_written_fields():
    evt = mimir_page_written(
        page_path="technical/ravn/agent.md",
        category="technical",
        author="ravn",
        source="mimir:markdown",
    )
    assert evt.event_type == registry.MIMIR_PAGE_WRITTEN
    assert evt.urgency == pytest.approx(0.1)
    assert "technical/ravn/agent.md" in evt.summary


# ---------------------------------------------------------------------------
# mimir.dream.completed
# ---------------------------------------------------------------------------


def test_mimir_dream_completed_fields():
    evt = mimir_dream_completed(
        pages_updated=5,
        entities_created=2,
        lint_fixes=1,
        source="mimir",
    )
    assert evt.event_type == registry.MIMIR_DREAM_COMPLETED
    assert evt.payload["pages_updated"] == 5
    assert evt.urgency == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Integration: emit catalog event → InProcessBus → subscriber receives payload
# ---------------------------------------------------------------------------


async def test_integration_ravn_session_started_via_in_process_bus():
    """Emit ravn.session.started via InProcessBus and verify the subscriber
    receives the event with the correct payload fields."""
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["ravn.*"], handler)

    event = ravn_session_started(
        session_id="sess-integration",
        persona="ravn",
        repo_slug="niuulabs/niuu",
        source="ravn:test",
    )
    await bus.publish(event)
    await bus.flush()

    assert len(received) == 1
    assert received[0].event_type == registry.RAVN_SESSION_STARTED
    assert received[0].payload["session_id"] == "sess-integration"
    assert received[0].payload["repo_slug"] == "niuulabs/niuu"
    assert received[0].correlation_id == "sess-integration"


async def test_integration_all_catalog_events_subscribed_via_wildcard():
    """Subscribe to '*' and verify all 11 catalog factory events are received."""
    bus = InProcessBus()
    received_types: list[str] = []

    async def handler(evt: SleipnirEvent) -> None:
        received_types.append(evt.event_type)

    await bus.subscribe(["*"], handler)

    events = [
        ravn_session_started(session_id="s", persona="p", repo_slug="r", source="t"),
        ravn_session_ended(
            session_id="s", persona="p", outcome="ok", token_count=0, duration_s=0.0, source="t"
        ),
        ravn_task_completed(task_id="t", persona="p", outcome="ok", source="t"),
        volundr_session_started(session_id="s", user_id="u", repo="r", branch="b", source="t"),
        volundr_session_failed(session_id="s", error="e", user_id="u", source="t"),
        tyr_saga_created(saga_id="s", template="tmpl", trigger_event="ev", source="t"),
        tyr_saga_completed(saga_id="s", outcome="ok", phases_completed=1, source="t"),
        tyr_raid_needs_approval(raid_id="r", saga_id="s", description="d", source="t"),
        bifrost_budget_degraded(
            tenant_id="t", current_spend=0.8, cap=1.0, downgraded_to="h", source="t"
        ),
        mimir_page_written(page_path="p.md", category="c", author="a", source="t"),
        mimir_dream_completed(pages_updated=1, entities_created=0, lint_fixes=0, source="t"),
    ]

    for evt in events:
        await bus.publish(evt)
    await bus.flush()

    assert len(received_types) == 11
    expected_types = {
        registry.RAVN_SESSION_STARTED,
        registry.RAVN_SESSION_ENDED,
        registry.RAVN_TASK_COMPLETED,
        registry.VOLUNDR_SESSION_STARTED,
        registry.VOLUNDR_SESSION_FAILED,
        registry.TYR_SAGA_CREATED,
        registry.TYR_SAGA_COMPLETED,
        registry.TYR_RAID_NEEDS_APPROVAL,
        registry.BIFROST_BUDGET_DEGRADED,
        registry.MIMIR_PAGE_WRITTEN,
        registry.MIMIR_DREAM_COMPLETED,
    }
    assert set(received_types) == expected_types


# ---------------------------------------------------------------------------
# github.pr.opened
# ---------------------------------------------------------------------------


def test_github_pr_opened_fields():
    evt = github_pr_opened(
        repo="org/repo",
        branch="feature/foo",
        author="alice",
        pr_url="https://github.com/org/repo/pull/1",
        title="Add feature foo",
        source="volundr:github-webhook",
    )
    assert evt.event_type == registry.GITHUB_PR_OPENED
    assert evt.payload["repo"] == "org/repo"
    assert evt.payload["branch"] == "feature/foo"
    assert evt.payload["author"] == "alice"
    assert evt.payload["pr_url"] == "https://github.com/org/repo/pull/1"
    assert evt.payload["title"] == "Add feature foo"
    assert evt.domain == "code"
    assert evt.urgency == pytest.approx(0.4)
    assert "alice" in evt.summary
    assert "org/repo" in evt.summary


def test_github_pr_opened_correlation_id_explicit():
    evt = github_pr_opened(
        repo="org/repo",
        branch="main",
        author="bob",
        pr_url="...",
        title="T",
        source="s",
        correlation_id="delivery-abc",
    )
    assert evt.correlation_id == "delivery-abc"


# ---------------------------------------------------------------------------
# github.pr.merged
# ---------------------------------------------------------------------------


def test_github_pr_merged_fields():
    evt = github_pr_merged(
        repo="org/repo",
        branch="main",
        merge_sha="abc1234567890",
        source="volundr:github-webhook",
        correlation_id="delivery-xyz",
    )
    assert evt.event_type == registry.GITHUB_PR_MERGED
    assert evt.payload["repo"] == "org/repo"
    assert evt.payload["branch"] == "main"
    assert evt.payload["merge_sha"] == "abc1234567890"
    assert evt.domain == "code"
    assert evt.urgency == pytest.approx(0.5)
    assert "abc1234" in evt.summary
    assert evt.correlation_id == "delivery-xyz"


# ---------------------------------------------------------------------------
# github.push.main
# ---------------------------------------------------------------------------


def test_github_push_main_fields():
    commits = [{"id": "abc", "message": "fix something"}]
    evt = github_push_main(
        repo="org/repo",
        commits=commits,
        pusher="carol",
        source="volundr:github-webhook",
    )
    assert evt.event_type == registry.GITHUB_PUSH_MAIN
    assert evt.payload["repo"] == "org/repo"
    assert evt.payload["pusher"] == "carol"
    assert evt.payload["commits"] == commits
    assert evt.domain == "code"
    assert evt.urgency == pytest.approx(0.5)
    assert "carol" in evt.summary
    assert "1 commit" in evt.summary


# ---------------------------------------------------------------------------
# github.issue.opened
# ---------------------------------------------------------------------------


def test_github_issue_opened_fields():
    evt = github_issue_opened(
        repo="org/repo",
        title="Something broke",
        body="It broke like this...",
        labels=["bug", "help wanted"],
        author="dave",
        source="volundr:github-webhook",
    )
    assert evt.event_type == registry.GITHUB_ISSUE_OPENED
    assert evt.payload["repo"] == "org/repo"
    assert evt.payload["title"] == "Something broke"
    assert evt.payload["body"] == "It broke like this..."
    assert evt.payload["labels"] == ["bug", "help wanted"]
    assert evt.payload["author"] == "dave"
    assert evt.domain == "code"
    assert evt.urgency == pytest.approx(0.3)
    assert "dave" in evt.summary


def test_github_issue_opened_empty_body():
    evt = github_issue_opened(
        repo="org/repo",
        title="No body",
        body="",
        labels=[],
        author="eve",
        source="s",
    )
    assert evt.payload["body"] == ""
    assert evt.payload["labels"] == []


# ---------------------------------------------------------------------------
# Integration: GitHub events via InProcessBus
# ---------------------------------------------------------------------------


async def test_integration_github_pr_opened_via_bus():
    """Emit github.pr.opened via InProcessBus and verify subscriber receives it."""
    bus = InProcessBus()
    received: list[SleipnirEvent] = []

    async def handler(evt: SleipnirEvent) -> None:
        received.append(evt)

    await bus.subscribe(["github.*"], handler)

    event = github_pr_opened(
        repo="org/repo",
        branch="feature/x",
        author="frank",
        pr_url="https://github.com/org/repo/pull/5",
        title="New feature",
        source="volundr:github-webhook",
        correlation_id="delivery-001",
    )
    await bus.publish(event)
    await bus.flush()

    assert len(received) == 1
    assert received[0].event_type == registry.GITHUB_PR_OPENED
    assert received[0].payload["author"] == "frank"
    assert received[0].correlation_id == "delivery-001"
