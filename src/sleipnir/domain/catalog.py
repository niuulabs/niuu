"""Standardised Sleipnir event catalog — NIU-582.

Typed payload dataclasses and factory functions for all platform events.
Each factory returns a fully-populated :class:`SleipnirEvent` with the
correct ``event_type``, ``domain``, ``urgency``, and ``summary`` so that
callers never have to worry about magic strings or forgotten fields.

Usage::

    from sleipnir.domain.catalog import ravn_session_ended

    event = ravn_session_ended(
        session_id="sess-abc",
        persona="ravn",
        outcome="success",
        token_count=12_345,
        duration_s=42.7,
        source="ravn:agent-abc123",
    )
    await publisher.publish(event)
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime

from sleipnir.domain import registry
from sleipnir.domain.events import SleipnirEvent

# ---------------------------------------------------------------------------
# Payload dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RavnSessionStartedPayload:
    session_id: str
    persona: str
    repo_slug: str


@dataclass(frozen=True)
class RavnSessionEndedPayload:
    session_id: str
    persona: str
    outcome: str
    token_count: int
    duration_s: float
    repo_slug: str = ""


@dataclass(frozen=True)
class RavnTaskCompletedPayload:
    task_id: str
    persona: str
    outcome: str


@dataclass(frozen=True)
class VolundrSessionStartedPayload:
    session_id: str
    user_id: str
    repo: str
    branch: str


@dataclass(frozen=True)
class VolundrSessionFailedPayload:
    session_id: str
    error: str
    user_id: str


@dataclass(frozen=True)
class TyrSagaCreatedPayload:
    saga_id: str
    template: str
    trigger_event: str


@dataclass(frozen=True)
class TyrSagaCompletedPayload:
    saga_id: str
    outcome: str
    phases_completed: int


@dataclass(frozen=True)
class TyrRaidNeedsApprovalPayload:
    raid_id: str
    saga_id: str
    description: str


@dataclass(frozen=True)
class BifrostBudgetDegradedPayload:
    tenant_id: str
    current_spend: float
    cap: float
    downgraded_to: str


@dataclass(frozen=True)
class MimirPageWrittenPayload:
    page_path: str
    category: str
    author: str


@dataclass(frozen=True)
class MimirDreamCompletedPayload:
    pages_updated: int
    entities_created: int
    lint_fixes: int


@dataclass(frozen=True)
class GitHubPrOpenedPayload:
    repo: str
    branch: str
    author: str
    pr_url: str
    title: str


@dataclass(frozen=True)
class GitHubPrMergedPayload:
    repo: str
    branch: str
    merge_sha: str


@dataclass(frozen=True)
class GitHubPushMainPayload:
    repo: str
    commits: list[dict]
    pusher: str


@dataclass(frozen=True)
class GitHubIssueOpenedPayload:
    repo: str
    title: str
    body: str
    labels: list[str]
    author: str


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------


def ravn_session_started(
    *,
    session_id: str,
    persona: str,
    repo_slug: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Ravn agent session receives its first turn."""
    return SleipnirEvent(
        event_type=registry.RAVN_SESSION_STARTED,
        source=source,
        payload=dataclasses.asdict(
            RavnSessionStartedPayload(
                session_id=session_id,
                persona=persona,
                repo_slug=repo_slug,
            )
        ),
        summary=f"Ravn session started: {session_id} persona={persona}",
        urgency=0.2,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or session_id,
    )


def ravn_session_ended(
    *,
    session_id: str,
    persona: str,
    outcome: str,
    token_count: int,
    duration_s: float,
    source: str,
    repo_slug: str = "",
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Ravn agent session exits (normal, interrupt, or error)."""
    return SleipnirEvent(
        event_type=registry.RAVN_SESSION_ENDED,
        source=source,
        payload=dataclasses.asdict(
            RavnSessionEndedPayload(
                session_id=session_id,
                persona=persona,
                outcome=outcome,
                token_count=token_count,
                duration_s=duration_s,
                repo_slug=repo_slug,
            )
        ),
        summary=f"Ravn session ended: {session_id} outcome={outcome} tokens={token_count}",
        urgency=0.3,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or session_id,
    )


def ravn_task_completed(
    *,
    task_id: str,
    persona: str,
    outcome: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Ravn DriveLoop task completes (success or failure)."""
    return SleipnirEvent(
        event_type=registry.RAVN_TASK_COMPLETED,
        source=source,
        payload=dataclasses.asdict(
            RavnTaskCompletedPayload(
                task_id=task_id,
                persona=persona,
                outcome=outcome,
            )
        ),
        summary=f"Ravn task completed: {task_id} outcome={outcome}",
        urgency=0.4 if outcome != "success" else 0.2,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or task_id,
    )


def volundr_session_started(
    *,
    session_id: str,
    user_id: str,
    repo: str,
    branch: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Volundr session transitions to RUNNING."""
    return SleipnirEvent(
        event_type=registry.VOLUNDR_SESSION_STARTED,
        source=source,
        payload=dataclasses.asdict(
            VolundrSessionStartedPayload(
                session_id=session_id,
                user_id=user_id,
                repo=repo,
                branch=branch,
            )
        ),
        summary=f"Volundr session started: {session_id}",
        urgency=0.2,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or session_id,
    )


def volundr_session_failed(
    *,
    session_id: str,
    error: str,
    user_id: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Volundr session pod provisioning fails."""
    return SleipnirEvent(
        event_type=registry.VOLUNDR_SESSION_FAILED,
        source=source,
        payload=dataclasses.asdict(
            VolundrSessionFailedPayload(
                session_id=session_id,
                error=error,
                user_id=user_id,
            )
        ),
        summary=f"Volundr session failed: {session_id} — {error[:80]}",
        urgency=0.8,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or session_id,
    )


def tyr_saga_created(
    *,
    saga_id: str,
    template: str,
    trigger_event: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a new Tyr saga is created."""
    return SleipnirEvent(
        event_type=registry.TYR_SAGA_CREATED,
        source=source,
        payload=dataclasses.asdict(
            TyrSagaCreatedPayload(
                saga_id=saga_id,
                template=template,
                trigger_event=trigger_event,
            )
        ),
        summary=f"Tyr saga created: {saga_id} template={template}",
        urgency=0.3,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or saga_id,
    )


def tyr_saga_completed(
    *,
    saga_id: str,
    outcome: str,
    phases_completed: int,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Tyr saga finishes all phases."""
    return SleipnirEvent(
        event_type=registry.TYR_SAGA_COMPLETED,
        source=source,
        payload=dataclasses.asdict(
            TyrSagaCompletedPayload(
                saga_id=saga_id,
                outcome=outcome,
                phases_completed=phases_completed,
            )
        ),
        summary=f"Tyr saga completed: {saga_id} outcome={outcome} phases={phases_completed}",
        urgency=0.4,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or saga_id,
    )


def tyr_raid_needs_approval(
    *,
    raid_id: str,
    saga_id: str,
    description: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Tyr raid requires human approval before proceeding."""
    return SleipnirEvent(
        event_type=registry.TYR_RAID_NEEDS_APPROVAL,
        source=source,
        payload=dataclasses.asdict(
            TyrRaidNeedsApprovalPayload(
                raid_id=raid_id,
                saga_id=saga_id,
                description=description,
            )
        ),
        summary=f"Raid needs approval: {raid_id} — {description[:80]}",
        urgency=0.8,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or raid_id,
    )


def bifrost_budget_degraded(
    *,
    tenant_id: str,
    current_spend: float,
    cap: float,
    downgraded_to: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a tenant's daily spend crosses 80 % of their cap."""
    pct = (current_spend / cap * 100) if cap > 0 else 0.0
    base = dataclasses.asdict(
        BifrostBudgetDegradedPayload(
            tenant_id=tenant_id,
            current_spend=current_spend,
            cap=cap,
            downgraded_to=downgraded_to,
        )
    )
    payload = {**base, "pct_consumed": round(pct, 2)}
    return SleipnirEvent(
        event_type=registry.BIFROST_BUDGET_DEGRADED,
        source=source,
        payload=payload,
        summary=(
            f"Budget degraded for tenant {tenant_id}: "
            f"${current_spend:.4f}/${cap:.4f} ({pct:.1f}%) → {downgraded_to}"
        ),
        urgency=0.7,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or tenant_id,
    )


def mimir_page_written(
    *,
    page_path: str,
    category: str,
    author: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when the Mimir adapter writes (creates or updates) a wiki page."""
    return SleipnirEvent(
        event_type=registry.MIMIR_PAGE_WRITTEN,
        source=source,
        payload=dataclasses.asdict(
            MimirPageWrittenPayload(
                page_path=page_path,
                category=category,
                author=author,
            )
        ),
        summary=f"Mimir page written: {page_path} by {author}",
        urgency=0.1,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )


def mimir_dream_completed(
    *,
    pages_updated: int,
    entities_created: int,
    lint_fixes: int,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Mimir dream cycle completes."""
    return SleipnirEvent(
        event_type=registry.MIMIR_DREAM_COMPLETED,
        source=source,
        payload=dataclasses.asdict(
            MimirDreamCompletedPayload(
                pages_updated=pages_updated,
                entities_created=entities_created,
                lint_fixes=lint_fixes,
            )
        ),
        summary=(
            f"Mimir dream completed: {pages_updated} pages updated, "
            f"{entities_created} entities, {lint_fixes} lint fixes"
        ),
        urgency=0.1,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )


def github_pr_opened(
    *,
    repo: str,
    branch: str,
    author: str,
    pr_url: str,
    title: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a GitHub pull request is opened."""
    return SleipnirEvent(
        event_type=registry.GITHUB_PR_OPENED,
        source=source,
        payload=dataclasses.asdict(
            GitHubPrOpenedPayload(
                repo=repo,
                branch=branch,
                author=author,
                pr_url=pr_url,
                title=title,
            )
        ),
        summary=f"GitHub PR opened: {title} by {author} on {repo}",
        urgency=0.4,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )


def github_pr_merged(
    *,
    repo: str,
    branch: str,
    merge_sha: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a GitHub pull request is merged."""
    return SleipnirEvent(
        event_type=registry.GITHUB_PR_MERGED,
        source=source,
        payload=dataclasses.asdict(
            GitHubPrMergedPayload(
                repo=repo,
                branch=branch,
                merge_sha=merge_sha,
            )
        ),
        summary=f"GitHub PR merged into {branch} on {repo} ({merge_sha[:7]})",
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )


def github_push_main(
    *,
    repo: str,
    commits: list[dict],
    pusher: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a push is made to the main branch on GitHub."""
    return SleipnirEvent(
        event_type=registry.GITHUB_PUSH_MAIN,
        source=source,
        payload=dataclasses.asdict(
            GitHubPushMainPayload(
                repo=repo,
                commits=commits,
                pusher=pusher,
            )
        ),
        summary=f"GitHub push to main on {repo} by {pusher} ({len(commits)} commit(s))",
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )


def github_issue_opened(
    *,
    repo: str,
    title: str,
    body: str,
    labels: list[str],
    author: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a GitHub issue is opened."""
    return SleipnirEvent(
        event_type=registry.GITHUB_ISSUE_OPENED,
        source=source,
        payload=dataclasses.asdict(
            GitHubIssueOpenedPayload(
                repo=repo,
                title=title,
                body=body,
                labels=labels,
                author=author,
            )
        ),
        summary=f"GitHub issue opened: {title} by {author} on {repo}",
        urgency=0.3,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id,
    )
