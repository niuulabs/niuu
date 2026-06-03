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
class TingSagaCreatedPayload:
    saga_id: str
    template: str
    trigger_event: str


@dataclass(frozen=True)
class TingSagaCompletedPayload:
    saga_id: str
    outcome: str
    phases_completed: int


@dataclass(frozen=True)
class TingRunNeedsApprovalPayload:
    run_id: str
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


@dataclass(frozen=True)
class EnvironmentStateChangedPayload:
    environment_id: str
    environment_type: str
    previous_state: str
    new_state: str
    severity: str
    confidence: float
    evidence: list[dict]


@dataclass(frozen=True)
class SignalReceivedPayload:
    environment_id: str
    environment_type: str
    signal_source: str
    signal_kind: str
    severity: str
    confidence: float
    data: dict


@dataclass(frozen=True)
class ValkyrieStateChangedPayload:
    environment_id: str
    valkyrie_id: str
    previous_state: str
    new_state: str
    reason: str


@dataclass(frozen=True)
class ValkyrieJudgmentRecordedPayload:
    environment_id: str
    valkyrie_id: str
    attention_tier: str
    recommended_action: str
    authority_boundary: str
    confidence: float
    evidence: list[dict]


@dataclass(frozen=True)
class ValkyrieJudgmentProposedPayload:
    environment_id: str
    valkyrie_id: str
    signal_refs: list[str]
    tier: str
    confidence: float
    operational_state: str
    rationale: str
    evidence: list[dict]
    recommended_action: str
    action_authority: str
    target_surfaces: list[str]
    expires_at: str
    dissent_refs: list[str]
    correlation_ids: dict


@dataclass(frozen=True)
class ValkyrieJudgmentRejectedPayload:
    environment_id: str
    valkyrie_id: str
    canonical_event_type: str
    errors: list[str]
    rejected_fields: dict


@dataclass(frozen=True)
class ValkyrieActionRequestedPayload:
    environment_id: str
    valkyrie_id: str
    action_id: str
    capability: str
    authority_boundary: str
    target: dict
    dry_run: bool


@dataclass(frozen=True)
class ValkyrieActionProposedPayload:
    environment_id: str
    valkyrie_id: str
    action_id: str
    capability: str
    action_authority: str
    target: dict
    rationale: str
    evidence: list[dict]
    target_surfaces: list[str]
    dry_run: bool


@dataclass(frozen=True)
class ValkyrieActionCompletedPayload:
    environment_id: str
    valkyrie_id: str
    action_id: str
    capability: str
    outcome: str
    evidence: list[dict]


@dataclass(frozen=True)
class OdinCourtDecidedPayload:
    environment_id: str
    court_id: str
    decision: str
    authority_boundary: str
    dissent: list[dict]


@dataclass(frozen=True)
class AttentionDecidedPayload:
    environment_id: str
    target_event_id: str
    attention_tier: str
    route: str
    reason: str


@dataclass(frozen=True)
class FeedbackRecordedPayload:
    environment_id: str
    target_event_id: str
    feedback_type: str
    rating: str
    notes: str


@dataclass(frozen=True)
class LearningPromotedPayload:
    environment_id: str
    learning_id: str
    from_scope: str
    to_scope: str
    summary: str
    confidence: float


@dataclass(frozen=True)
class ParticipantJoinedPayload:
    environment_id: str
    participant_id: str
    participant_type: str
    display_name: str
    capabilities: list[str]


@dataclass(frozen=True)
class ParticipantLeftPayload:
    environment_id: str
    participant_id: str
    participant_type: str
    display_name: str
    reason: str


@dataclass(frozen=True)
class ParticipantHeartbeatPayload:
    environment_id: str
    participant_id: str
    participant_type: str
    display_name: str
    status: str
    wakefulness: str
    attention_state: str
    heartbeat_ttl_s: float


@dataclass(frozen=True)
class ParticipantCapabilitiesChangedPayload:
    environment_id: str
    participant_id: str
    participant_type: str
    display_name: str
    capabilities: list[str]
    surfaces: list[str]
    tools: list[str]


@dataclass(frozen=True)
class RoomOpenedPayload:
    environment_id: str
    room_id: str
    purpose: str
    participants: list[str]


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


def ting_saga_created(
    *,
    saga_id: str,
    template: str,
    trigger_event: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a new Ting saga is created."""
    return SleipnirEvent(
        event_type=registry.TING_SAGA_CREATED,
        source=source,
        payload=dataclasses.asdict(
            TingSagaCreatedPayload(
                saga_id=saga_id,
                template=template,
                trigger_event=trigger_event,
            )
        ),
        summary=f"Ting saga created: {saga_id} template={template}",
        urgency=0.3,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or saga_id,
    )


def ting_saga_completed(
    *,
    saga_id: str,
    outcome: str,
    phases_completed: int,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Ting saga finishes all phases."""
    return SleipnirEvent(
        event_type=registry.TING_SAGA_COMPLETED,
        source=source,
        payload=dataclasses.asdict(
            TingSagaCompletedPayload(
                saga_id=saga_id,
                outcome=outcome,
                phases_completed=phases_completed,
            )
        ),
        summary=f"Ting saga completed: {saga_id} outcome={outcome} phases={phases_completed}",
        urgency=0.4,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or saga_id,
    )


def ting_run_needs_approval(
    *,
    run_id: str,
    saga_id: str,
    description: str,
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Ting run requires human approval before proceeding."""
    return SleipnirEvent(
        event_type=registry.TING_RUN_NEEDS_APPROVAL,
        source=source,
        payload=dataclasses.asdict(
            TingRunNeedsApprovalPayload(
                run_id=run_id,
                saga_id=saga_id,
                description=description,
            )
        ),
        summary=f"Run needs approval: {run_id} — {description[:80]}",
        urgency=0.8,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or run_id,
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


def environment_state_changed(
    *,
    environment_id: str,
    environment_type: str,
    previous_state: str,
    new_state: str,
    severity: str,
    source: str,
    confidence: float = 1.0,
    evidence: list[dict] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when an Environment's operational state changes."""
    payload = dataclasses.asdict(
        EnvironmentStateChangedPayload(
            environment_id=environment_id,
            environment_type=environment_type,
            previous_state=previous_state,
            new_state=new_state,
            severity=severity,
            confidence=confidence,
            evidence=evidence or [],
        )
    )
    return SleipnirEvent(
        event_type=registry.ENVIRONMENT_STATE_CHANGED,
        source=source,
        payload=payload,
        summary=f"Environment {environment_id} state {previous_state} -> {new_state}",
        urgency=_severity_urgency(severity),
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def signal_received(
    *,
    environment_id: str,
    environment_type: str,
    signal_source: str,
    signal_kind: str,
    severity: str,
    data: dict,
    source: str,
    confidence: float = 1.0,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a normalized Environment signal is received."""
    event_type_by_kind = {
        "kubernetes": registry.SIGNAL_KUBERNETES_EVENT,
        "inbox": registry.SIGNAL_INBOX_MESSAGE,
        "host": registry.SIGNAL_HOST_EVENT,
        "printer": registry.SIGNAL_PRINTER_EVENT,
    }
    event_type = event_type_by_kind.get(signal_kind, registry.SIGNAL_RECEIVED)
    payload = dataclasses.asdict(
        SignalReceivedPayload(
            environment_id=environment_id,
            environment_type=environment_type,
            signal_source=signal_source,
            signal_kind=signal_kind,
            severity=severity,
            confidence=confidence,
            data=data,
        )
    )
    return SleipnirEvent(
        event_type=event_type,
        source=source,
        payload=payload,
        summary=f"{signal_kind} signal in {environment_id}: {severity}",
        urgency=_severity_urgency(severity),
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or f"{environment_id}:{signal_source}",
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_state_changed(
    *,
    environment_id: str,
    valkyrie_id: str,
    previous_state: str,
    new_state: str,
    reason: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a resident Valkyrie's wake/sleep/dream/health state changes."""
    payload = dataclasses.asdict(
        ValkyrieStateChangedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_STATE_CHANGED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} state {previous_state} -> {new_state}",
        urgency=0.3,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_state_updated(
    *,
    environment_id: str,
    valkyrie_id: str,
    previous_state: str,
    new_state: str,
    reason: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a resident Valkyrie publishes its current operational state."""
    payload = dataclasses.asdict(
        ValkyrieStateChangedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            previous_state=previous_state,
            new_state=new_state,
            reason=reason,
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_STATE_UPDATED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} state updated: {new_state}",
        urgency=0.3,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_judgment_recorded(
    *,
    environment_id: str,
    valkyrie_id: str,
    attention_tier: str,
    recommended_action: str,
    authority_boundary: str,
    confidence: float,
    source: str,
    evidence: list[dict] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Valkyrie records a judgment about Environment state."""
    payload = dataclasses.asdict(
        ValkyrieJudgmentRecordedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            attention_tier=attention_tier,
            recommended_action=recommended_action,
            authority_boundary=authority_boundary,
            confidence=confidence,
            evidence=evidence or [],
        )
    )
    urgency = max(0.2, min(1.0, confidence if attention_tier == "urgent" else confidence * 0.8))
    return SleipnirEvent(
        event_type=registry.VALKYRIE_JUDGMENT_RECORDED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} judgment: {attention_tier}/{recommended_action}",
        urgency=urgency,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_judgment_proposed(
    *,
    environment_id: str,
    valkyrie_id: str,
    attention_tier: str,
    recommended_action: str,
    authority_boundary: str,
    confidence: float,
    source: str,
    operational_state: str = "",
    rationale: str = "",
    signal_refs: list[str] | None = None,
    evidence: list[dict] | None = None,
    target_surfaces: list[str] | None = None,
    expires_at: str = "",
    dissent_refs: list[str] | None = None,
    correlation_ids: dict | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Valkyrie proposes a judgment for ODIN/court policy handling."""
    payload = dataclasses.asdict(
        ValkyrieJudgmentProposedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            signal_refs=signal_refs or [],
            tier=attention_tier,
            confidence=confidence,
            operational_state=operational_state,
            rationale=rationale,
            evidence=evidence or [],
            recommended_action=recommended_action,
            action_authority=authority_boundary,
            target_surfaces=target_surfaces or [],
            expires_at=expires_at,
            dissent_refs=dissent_refs or [],
            correlation_ids=correlation_ids or {},
        )
    )
    urgency = max(0.2, min(1.0, confidence if attention_tier == "urgent" else confidence * 0.8))
    return SleipnirEvent(
        event_type=registry.VALKYRIE_JUDGMENT_PROPOSED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} proposed judgment: {attention_tier}/{recommended_action}",
        urgency=urgency,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_judgment_rejected(
    *,
    environment_id: str,
    valkyrie_id: str,
    canonical_event_type: str,
    errors: list[str],
    rejected_fields: dict,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a resident Valkyrie judgment is rejected before publication."""
    payload = dataclasses.asdict(
        ValkyrieJudgmentRejectedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            canonical_event_type=canonical_event_type,
            errors=errors,
            rejected_fields=rejected_fields,
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_JUDGMENT_REJECTED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} judgment rejected: {len(errors)} errors",
        urgency=0.8,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_action_proposed(
    *,
    environment_id: str,
    valkyrie_id: str,
    action_id: str,
    capability: str,
    action_authority: str,
    target: dict,
    rationale: str,
    source: str,
    evidence: list[dict] | None = None,
    target_surfaces: list[str] | None = None,
    dry_run: bool = True,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Valkyrie proposes a scoped action before policy resolution."""
    payload = dataclasses.asdict(
        ValkyrieActionProposedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            action_id=action_id,
            capability=capability,
            action_authority=action_authority,
            target=target,
            rationale=rationale,
            evidence=evidence or [],
            target_surfaces=target_surfaces or [],
            dry_run=dry_run,
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_ACTION_PROPOSED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} proposed {capability} action {action_id}",
        urgency=0.5 if action_authority == "autonomous" else 0.7,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or action_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_action_requested(
    *,
    environment_id: str,
    valkyrie_id: str,
    action_id: str,
    capability: str,
    authority_boundary: str,
    target: dict,
    source: str,
    dry_run: bool = True,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a Valkyrie requests or proposes a scoped action."""
    payload = dataclasses.asdict(
        ValkyrieActionRequestedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            action_id=action_id,
            capability=capability,
            authority_boundary=authority_boundary,
            target=target,
            dry_run=dry_run,
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_ACTION_REQUESTED,
        source=source,
        payload=payload,
        summary=f"Valkyrie {valkyrie_id} requested {capability} action {action_id}",
        urgency=0.6 if authority_boundary == "autonomous" else 0.4,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or action_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_action_executed(
    *,
    environment_id: str,
    valkyrie_id: str,
    action_id: str,
    capability: str,
    outcome: str,
    source: str,
    evidence: list[dict] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a scoped Valkyrie action is executed."""
    payload = dataclasses.asdict(
        ValkyrieActionCompletedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            action_id=action_id,
            capability=capability,
            outcome=outcome,
            evidence=evidence or [],
        )
    )
    return SleipnirEvent(
        event_type=registry.VALKYRIE_ACTION_EXECUTED,
        source=source,
        payload=payload,
        summary=f"Valkyrie action {action_id} executed: {outcome}",
        urgency=0.4,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or action_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def valkyrie_action_completed(
    *,
    environment_id: str,
    valkyrie_id: str,
    action_id: str,
    capability: str,
    outcome: str,
    source: str,
    evidence: list[dict] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a scoped Valkyrie action completes or fails."""
    payload = dataclasses.asdict(
        ValkyrieActionCompletedPayload(
            environment_id=environment_id,
            valkyrie_id=valkyrie_id,
            action_id=action_id,
            capability=capability,
            outcome=outcome,
            evidence=evidence or [],
        )
    )
    return SleipnirEvent(
        event_type=(
            registry.VALKYRIE_ACTION_COMPLETED
            if outcome in {"success", "completed", "accepted"}
            else registry.VALKYRIE_ACTION_FAILED
        ),
        source=source,
        payload=payload,
        summary=f"Valkyrie action {action_id} {outcome}",
        urgency=0.4 if outcome in {"success", "completed", "accepted"} else 0.8,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or action_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def odin_court_decided(
    *,
    environment_id: str,
    court_id: str,
    decision: str,
    authority_boundary: str,
    source: str,
    dissent: list[dict] | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when ODIN court resolves a judgment/action/escalation."""
    payload = dataclasses.asdict(
        OdinCourtDecidedPayload(
            environment_id=environment_id,
            court_id=court_id,
            decision=decision,
            authority_boundary=authority_boundary,
            dissent=dissent or [],
        )
    )
    return SleipnirEvent(
        event_type=registry.ODIN_COURT_DECIDED,
        source=source,
        payload=payload,
        summary=f"ODIN court {court_id} decided: {decision}",
        urgency=0.6,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or court_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def attention_decided(
    *,
    environment_id: str,
    target_event_id: str,
    attention_tier: str,
    route: str,
    reason: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when attention routing/escalation is decided."""
    payload = dataclasses.asdict(
        AttentionDecidedPayload(
            environment_id=environment_id,
            target_event_id=target_event_id,
            attention_tier=attention_tier,
            route=route,
            reason=reason,
        )
    )
    event_type = registry.ATTENTION_DECIDED
    if attention_tier == "suppress":
        event_type = registry.ATTENTION_SUPPRESSED
    if attention_tier in {"urgent", "review"}:
        event_type = registry.ATTENTION_ESCALATED
    return SleipnirEvent(
        event_type=event_type,
        source=source,
        payload=payload,
        summary=f"Attention {attention_tier} via {route}: {reason[:80]}",
        urgency=_attention_urgency(attention_tier),
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or target_event_id,
        causation_id=causation_id or target_event_id,
        tenant_id=tenant_id,
    )


def feedback_recorded(
    *,
    environment_id: str,
    target_event_id: str,
    feedback_type: str,
    rating: str,
    notes: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when feedback is recorded for future resident learning."""
    payload = dataclasses.asdict(
        FeedbackRecordedPayload(
            environment_id=environment_id,
            target_event_id=target_event_id,
            feedback_type=feedback_type,
            rating=rating,
            notes=notes,
        )
    )
    return SleipnirEvent(
        event_type=registry.FEEDBACK_RECORDED,
        source=source,
        payload=payload,
        summary=f"Feedback {rating} on {target_event_id}: {feedback_type}",
        urgency=0.3,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or target_event_id,
        causation_id=causation_id or target_event_id,
        tenant_id=tenant_id,
    )


def learning_promoted(
    *,
    environment_id: str,
    learning_id: str,
    from_scope: str,
    to_scope: str,
    summary: str,
    source: str,
    confidence: float,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a learning moves between private, Environment, Flock, or shared scopes."""
    payload = dataclasses.asdict(
        LearningPromotedPayload(
            environment_id=environment_id,
            learning_id=learning_id,
            from_scope=from_scope,
            to_scope=to_scope,
            summary=summary,
            confidence=confidence,
        )
    )
    return SleipnirEvent(
        event_type=registry.LEARNING_PROMOTED,
        source=source,
        payload=payload,
        summary=f"Learning {learning_id} promoted {from_scope} -> {to_scope}: {summary[:80]}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or learning_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def participant_joined(
    *,
    environment_id: str,
    participant_id: str,
    participant_type: str,
    display_name: str,
    capabilities: list[str],
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a human, agent, tool, or surface joins an Environment."""
    payload = dataclasses.asdict(
        ParticipantJoinedPayload(
            environment_id=environment_id,
            participant_id=participant_id,
            participant_type=participant_type,
            display_name=display_name,
            capabilities=capabilities,
        )
    )
    return SleipnirEvent(
        event_type=registry.PARTICIPANT_JOINED,
        source=source,
        payload=payload,
        summary=f"{participant_type} participant joined {environment_id}: {display_name}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def participant_left(
    *,
    environment_id: str,
    participant_id: str,
    participant_type: str,
    display_name: str,
    reason: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a participant leaves an Environment or room."""
    payload = dataclasses.asdict(
        ParticipantLeftPayload(
            environment_id=environment_id,
            participant_id=participant_id,
            participant_type=participant_type,
            display_name=display_name,
            reason=reason,
        )
    )
    return SleipnirEvent(
        event_type=registry.PARTICIPANT_LEFT,
        source=source,
        payload=payload,
        summary=f"{participant_type} participant left {environment_id}: {display_name}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def participant_heartbeat(
    *,
    environment_id: str,
    participant_id: str,
    participant_type: str,
    display_name: str,
    status: str,
    wakefulness: str,
    attention_state: str,
    heartbeat_ttl_s: float,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit a participant heartbeat/presence update."""
    payload = dataclasses.asdict(
        ParticipantHeartbeatPayload(
            environment_id=environment_id,
            participant_id=participant_id,
            participant_type=participant_type,
            display_name=display_name,
            status=status,
            wakefulness=wakefulness,
            attention_state=attention_state,
            heartbeat_ttl_s=heartbeat_ttl_s,
        )
    )
    return SleipnirEvent(
        event_type=registry.PARTICIPANT_HEARTBEAT,
        source=source,
        payload=payload,
        summary=f"{participant_type} participant heartbeat {environment_id}: {display_name}",
        urgency=0.1 if status != "expired" else 0.5,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def participant_capabilities_changed(
    *,
    environment_id: str,
    participant_id: str,
    participant_type: str,
    display_name: str,
    capabilities: list[str],
    surfaces: list[str],
    tools: list[str],
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a participant's tools/capabilities/surfaces change."""
    payload = dataclasses.asdict(
        ParticipantCapabilitiesChangedPayload(
            environment_id=environment_id,
            participant_id=participant_id,
            participant_type=participant_type,
            display_name=display_name,
            capabilities=capabilities,
            surfaces=surfaces,
            tools=tools,
        )
    )
    return SleipnirEvent(
        event_type=registry.PARTICIPANT_CAPABILITIES_CHANGED,
        source=source,
        payload=payload,
        summary=f"{participant_type} participant capabilities changed: {display_name}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or environment_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def room_opened(
    *,
    environment_id: str,
    room_id: str,
    purpose: str,
    participants: list[str],
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a replayable Environment huddle/room is opened."""
    payload = dataclasses.asdict(
        RoomOpenedPayload(
            environment_id=environment_id,
            room_id=room_id,
            purpose=purpose,
            participants=participants,
        )
    )
    return SleipnirEvent(
        event_type=registry.ROOM_OPENED,
        source=source,
        payload=payload,
        summary=f"Room {room_id} opened in {environment_id}: {purpose[:80]}",
        urgency=0.5,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or room_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def _severity_urgency(severity: str) -> float:
    match severity:
        case "critical":
            return 0.95
        case "error":
            return 0.8
        case "warning":
            return 0.6
        case "info":
            return 0.3
        case _:
            return 0.4


def _attention_urgency(attention_tier: str) -> float:
    match attention_tier:
        case "urgent":
            return 0.95
        case "review":
            return 0.75
        case "watch":
            return 0.5
        case "record":
            return 0.25
        case "suppress":
            return 0.1
        case _:
            return 0.4
