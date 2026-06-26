"""Shared operator contact primitives.

These models describe the need for human judgment. They are deliberately
transport-neutral: interactive sessions can answer them directly, while daemon
sessions can emit existing ``help_needed`` events through the normal channel
stack.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from inspect import isawaitable
from typing import Protocol

from ravn.domain.help_needed import build_help_needed_event
from ravn.ports.channel import ChannelPort


class OperatorContactKind(StrEnum):
    ASK_USER = "ask_user"
    HELP_NEEDED = "help_needed"


class OperatorContactPurpose(StrEnum):
    CLARIFICATION = "clarification"
    APPROVAL = "approval"
    SUMMARY = "summary"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    INTERESTING_FINDING = "interesting_finding"
    RECOMMENDATION = "recommendation"


class OperatorContactStatus(StrEnum):
    PENDING = "pending"
    ANSWERED = "answered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class OperatorContactRequest:
    question: str
    reason: str
    impact: str
    kind: OperatorContactKind = OperatorContactKind.HELP_NEEDED
    purpose: OperatorContactPurpose = OperatorContactPurpose.CLARIFICATION
    id: str = ""
    source_objective_id: str = ""
    risk_boundaries: tuple[str, ...] = ()
    tool_name: str = ""
    tool_input: dict[str, str] = field(default_factory=dict)
    help_needed_outcome: dict[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class OperatorContactResult:
    request: OperatorContactRequest
    status: str
    answer: str = ""
    approved: bool | None = None
    emitted_ref: str = ""
    responded_at: datetime | None = None


class OperatorContactPort(Protocol):
    """Boundary for asking the operator and optionally waiting for an answer."""

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        """Emit an operator request and return an answer when available."""


@dataclass(frozen=True)
class ChannelOperatorContact(OperatorContactPort):
    """Operator contact over the existing Ravn/Skuld channel stack.

    This is the canonical headless/daemon path: emit an existing help_needed
    event and let the configured channel, such as Telegram, carry it outward.
    """

    channel: ChannelPort
    source: str
    persona: str
    session_id: str = ""

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        return await emit_help_needed_operator_contact(
            self.channel,
            request,
            source=self.source,
            persona=self.persona,
            session_id=self.session_id,
        )


@dataclass(frozen=True)
class CallbackOperatorContact(OperatorContactPort):
    """Operator contact through an interactive ask-user style callback."""

    ask_operator: Callable[[str], str | Awaitable[str]]
    approval_decider: Callable[[str], bool | None] | None = None

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        return await answer_operator_contact(
            request,
            self.ask_operator,
            approval_decider=self.approval_decider,
        )


@dataclass(frozen=True)
class BroadcastThenCallbackOperatorContact(OperatorContactPort):
    """Emit help_needed for audit/visibility, then await an interactive answer."""

    broadcast: ChannelOperatorContact
    callback: CallbackOperatorContact

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        await self.broadcast.ask(request)
        return await self.callback.ask(request)


@dataclass(frozen=True)
class PendingOperatorContact(OperatorContactPort):
    """Fallback contact port when no outbound channel or callback is configured."""

    async def ask(self, request: OperatorContactRequest) -> OperatorContactResult:
        return OperatorContactResult(
            request=request,
            status=OperatorContactStatus.PENDING.value,
        )


async def answer_operator_contact(
    request: OperatorContactRequest,
    ask_operator: Callable[[str], str | Awaitable[str]],
    *,
    approval_decider: Callable[[str], bool | None] | None = None,
) -> OperatorContactResult:
    """Ask through the existing interactive ask-operator/ask-user callback shape."""
    answer_or_awaitable = ask_operator(request.question)
    answer = await answer_or_awaitable if isawaitable(answer_or_awaitable) else answer_or_awaitable
    answer_text = str(answer or "").strip()
    approved = approval_decider(answer_text) if approval_decider is not None else None
    return OperatorContactResult(
        request=request,
        status=OperatorContactStatus.ANSWERED.value,
        answer=answer_text,
        approved=approved,
        responded_at=datetime.now(UTC),
    )


async def emit_help_needed_operator_contact(
    channel: ChannelPort,
    request: OperatorContactRequest,
    *,
    source: str,
    persona: str,
    session_id: str = "",
) -> OperatorContactResult:
    """Emit an existing Ravn ``help_needed`` event for headless operator contact."""
    event = _operator_help_needed_event(
        request,
        source=source,
        persona=persona,
        session_id=session_id,
    )
    await channel.emit(event)
    return OperatorContactResult(
        request=request,
        status=OperatorContactStatus.PENDING.value,
        emitted_ref=event.correlation_id,
    )


def _operator_help_needed_event(
    request: OperatorContactRequest,
    *,
    source: str,
    persona: str,
    session_id: str = "",
):
    contact_id = request.id or request.tool_input.get("id") or "operator-contact"
    if request.purpose == OperatorContactPurpose.APPROVAL:
        reason = "operator_approval_required"
        recommendation = "Reply with whether this specific action is approved."
    else:
        reason = f"operator_{request.purpose.value}_needed"
        recommendation = "Reply with the requested context or guidance."
    return build_help_needed_event(
        source=source,
        persona=persona,
        reason=reason,
        summary=request.question,
        attempted=[
            "identified work that needs operator judgment",
            "paused execution until operator guidance is available",
        ],
        recommendation=recommendation,
        correlation_id=contact_id,
        session_id=session_id,
        task_id=contact_id,
        context={
            "operator_contact_id": contact_id,
            "operator_contact_purpose": request.purpose.value,
            "source_objective_id": request.source_objective_id,
            "risk_boundaries": list(request.risk_boundaries),
            "impact": request.impact,
        },
    )
