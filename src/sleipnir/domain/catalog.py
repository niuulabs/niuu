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
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

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
class VolundrSessionForgeMcpGrantsChangedPayload:
    session_id: str
    owner_id: str
    actor_user_id: str
    actor_token_use: str
    previous_grants: list[str]
    grants: list[str]


@dataclass(frozen=True)
class VolundrSessionNeedsInputPayload:
    session_id: str
    session_name: str
    owner_id: str
    #: What the session is blocked on: "question" | "confirmation" | "permission".
    kind: str
    #: Human-readable prompt to show the user (best-effort; may be empty).
    prompt: str = ""
    #: Opaque correlation id for the pending request (matches the chat WS frame).
    request_id: str = ""


@dataclass(frozen=True)
class VolundrSessionNotificationPayload:
    """The owner-scoped Forge notification, as served by ``GET /notifications``."""

    id: str
    seq: int
    owner_id: str
    kind: str
    severity: str
    source: str
    title: str
    body: str = ""
    session_id: str | None = None
    session_seq: int | None = None
    session_name: str | None = None
    tenant_id: str | None = None
    project_id: str | None = None
    links: list[dict[str, Any]] = field(default_factory=list)
    engine: str | None = None
    model: str | None = None
    correlation_id: str | None = None
    created_at: str = ""


#: Bus urgency by notification severity. ``critical`` outranks a needs-input
#: request; ``warning`` matches it; routine milestones stay at the default.
NOTIFICATION_SEVERITY_URGENCY: dict[str, float] = {
    "info": 0.5,
    "success": 0.5,
    "warning": 0.9,
    "critical": 0.95,
}


def notification_urgency(severity: str) -> float:
    """Sleipnir urgency for a notification severity (unknown severities rank lowest)."""
    return NOTIFICATION_SEVERITY_URGENCY.get(severity, NOTIFICATION_SEVERITY_URGENCY["info"])


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


@dataclass(frozen=True)
class RoomMessageRecordedPayload:
    environment_id: str
    room_id: str
    message_id: str
    participant_id: str
    role: str
    content: str
    visibility: str
    thread_id: str
    metadata: dict


@dataclass(frozen=True)
class RoomContextSnapshotRecordedPayload:
    environment_id: str
    room_id: str
    root_correlation_id: str
    active_state: dict
    signal_refs: list[str]
    judgment_refs: list[str]
    action_refs: list[str]
    participant_ids: list[str]


@dataclass(frozen=True)
class RoomTranscriptRecordedPayload:
    environment_id: str
    room_id: str
    transcript_ref: str
    message_refs: list[str]
    summary: str


@dataclass(frozen=True)
class RoomClosedPayload:
    environment_id: str
    room_id: str
    reason: str
    transcript_ref: str


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


def volundr_session_forge_mcp_grants_changed(
    *,
    session_id: str,
    owner_id: str,
    actor_user_id: str,
    actor_token_use: str,
    previous_grants: list[str],
    grants: list[str],
    source: str,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when an operator replaces a session's stored Forge MCP grants."""
    return SleipnirEvent(
        event_type=registry.VOLUNDR_SESSION_FORGE_MCP_GRANTS_CHANGED,
        source=source,
        payload=dataclasses.asdict(
            VolundrSessionForgeMcpGrantsChangedPayload(
                session_id=session_id,
                owner_id=owner_id,
                actor_user_id=actor_user_id,
                actor_token_use=actor_token_use,
                previous_grants=list(previous_grants),
                grants=list(grants),
            )
        ),
        summary=(
            f"Forge MCP grants for session {session_id}: "
            f"[{', '.join(previous_grants)}] -> [{', '.join(grants)}] by {actor_user_id}"
        ),
        urgency=0.5,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=session_id,
        tenant_id=tenant_id,
    )


def volundr_session_needs_input(
    *,
    session_id: str,
    session_name: str,
    owner_id: str,
    kind: str,
    prompt: str = "",
    request_id: str = "",
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a session blocks waiting on the user.

    This is the platform-wide "needs attention" signal — a notification /
    push fan-out subscribes to it to alert the owner that the session cannot
    progress until they respond. High urgency so it outranks routine activity.
    """
    return SleipnirEvent(
        event_type=registry.VOLUNDR_SESSION_NEEDS_INPUT,
        source=source,
        payload=dataclasses.asdict(
            VolundrSessionNeedsInputPayload(
                session_id=session_id,
                session_name=session_name,
                owner_id=owner_id,
                kind=kind,
                prompt=prompt,
                request_id=request_id,
            )
        ),
        summary=f"Session needs input ({kind}): {session_name or session_id}",
        urgency=0.9,
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or session_id,
    )


def volundr_session_notification(
    *,
    notification: Mapping[str, Any],
    source: str,
    correlation_id: str | None = None,
) -> SleipnirEvent:
    """Emit when Forge records a new session notification.

    ``notification`` is the notification JSON (``NotificationResponse`` without
    ``read``). Consumers must honour ``owner_id``: the event is not a broadcast to
    every user.
    """
    known = {item.name for item in dataclasses.fields(VolundrSessionNotificationPayload)}
    payload = VolundrSessionNotificationPayload(
        **{key: value for key, value in notification.items() if key in known}
    )
    label = payload.session_name or payload.session_id or payload.owner_id
    return SleipnirEvent(
        event_type=registry.VOLUNDR_SESSION_NOTIFICATION,
        source=source,
        payload=dataclasses.asdict(payload),
        summary=f"Notification ({payload.kind}/{payload.severity}) {label}: {payload.title[:80]}",
        urgency=notification_urgency(payload.severity),
        domain="code",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or payload.correlation_id or payload.session_id,
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


def room_message_recorded(
    *,
    environment_id: str,
    room_id: str,
    message_id: str,
    participant_id: str,
    role: str,
    content: str,
    source: str,
    visibility: str = "public",
    thread_id: str = "",
    metadata: dict | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a huddle/room message is appended to the replay timeline."""
    payload = dataclasses.asdict(
        RoomMessageRecordedPayload(
            environment_id=environment_id,
            room_id=room_id,
            message_id=message_id,
            participant_id=participant_id,
            role=role,
            content=content,
            visibility=visibility,
            thread_id=thread_id,
            metadata=metadata or {},
        )
    )
    return SleipnirEvent(
        event_type=registry.ROOM_MESSAGE_RECORDED,
        source=source,
        payload=payload,
        summary=f"Room {room_id} message from {participant_id}: {content[:80]}",
        urgency=0.3,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or room_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def room_context_snapshot_recorded(
    *,
    environment_id: str,
    room_id: str,
    root_correlation_id: str,
    active_state: dict,
    signal_refs: list[str],
    judgment_refs: list[str],
    action_refs: list[str],
    participant_ids: list[str],
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit a replayable snapshot for late huddle joiners."""
    payload = dataclasses.asdict(
        RoomContextSnapshotRecordedPayload(
            environment_id=environment_id,
            room_id=room_id,
            root_correlation_id=root_correlation_id,
            active_state=active_state,
            signal_refs=signal_refs,
            judgment_refs=judgment_refs,
            action_refs=action_refs,
            participant_ids=participant_ids,
        )
    )
    return SleipnirEvent(
        event_type=registry.ROOM_CONTEXT_SNAPSHOT_RECORDED,
        source=source,
        payload=payload,
        summary=f"Room {room_id} context snapshot in {environment_id}",
        urgency=0.25,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or root_correlation_id or room_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def room_transcript_recorded(
    *,
    environment_id: str,
    room_id: str,
    transcript_ref: str,
    message_refs: list[str],
    summary: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a huddle transcript has been written for audit/learning."""
    payload = dataclasses.asdict(
        RoomTranscriptRecordedPayload(
            environment_id=environment_id,
            room_id=room_id,
            transcript_ref=transcript_ref,
            message_refs=message_refs,
            summary=summary,
        )
    )
    return SleipnirEvent(
        event_type=registry.ROOM_TRANSCRIPT_RECORDED,
        source=source,
        payload=payload,
        summary=f"Room {room_id} transcript recorded: {summary[:80]}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or room_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def room_closed(
    *,
    environment_id: str,
    room_id: str,
    reason: str,
    transcript_ref: str,
    source: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    tenant_id: str | None = None,
) -> SleipnirEvent:
    """Emit when a replayable Environment huddle is closed."""
    payload = dataclasses.asdict(
        RoomClosedPayload(
            environment_id=environment_id,
            room_id=room_id,
            reason=reason,
            transcript_ref=transcript_ref,
        )
    )
    return SleipnirEvent(
        event_type=registry.ROOM_CLOSED,
        source=source,
        payload=payload,
        summary=f"Room {room_id} closed: {reason[:80]}",
        urgency=0.2,
        domain="infrastructure",
        timestamp=datetime.now(UTC),
        correlation_id=correlation_id or room_id,
        causation_id=causation_id,
        tenant_id=tenant_id,
    )


def _severity_urgency(severity_name: str) -> float:
    return {
        "critical": 0.95,
        "error": 0.8,
        "warning": 0.6,
        "info": 0.3,
    }.get(severity_name, 0.4)
