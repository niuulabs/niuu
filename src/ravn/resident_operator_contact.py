"""Canonical resident operator-contact coordination."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ravn.domain.operator_contact import (
    OperatorContactKind,
    OperatorContactPort,
    OperatorContactPurpose,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
)
from ravn.domain.resident_continuation import (
    ResidentMemoryEntry,
    ResidentMemoryPort,
    ResidentPolicyObservation,
    ResidentTurnRecord,
)
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)

_APPROVAL_MARKER_PREFIX = "operator approved risk boundary:"
_DENIAL_MARKER_PREFIX = "operator denied risk boundary:"


@dataclass(frozen=True)
class ResidentOperatorContactConfig:
    """Bounds for one resident operator-contact pass."""

    suppress_duplicate_pending: bool = True
    persist_operator_feedback: bool = True
    max_policy_observations_per_answer: int = 4


@dataclass(frozen=True)
class ResidentOperatorContactReport:
    """Auditable outcome of one operator contact attempt."""

    request: OperatorContactRequest
    result: OperatorContactResult
    pending_ref: str = ""
    answer_ref: str = ""
    suppressed_existing_pending: ResidentMemoryEntry | None = None
    policy_observations: tuple[ResidentPolicyObservation, ...] = ()


@dataclass(frozen=True)
class ResidentOperatorReplyIngestionReport:
    """Result of ingesting a headless directed operator reply."""

    handled: bool
    contact_ref: str = ""
    objective_ref: str = ""
    portfolio_ref: str = ""
    decision_ref: str = ""
    source_objective_id: str = ""
    approved: bool | None = None
    policy_observations: tuple[ResidentPolicyObservation, ...] = ()
    reason: str = ""


class ResidentOperatorContactCoordinator:
    """Single resident path for asks, approvals, answers, and duplicate suppression."""

    def __init__(
        self,
        *,
        memory: ResidentMemoryPort,
        contact: OperatorContactPort,
        config: ResidentOperatorContactConfig | None = None,
    ) -> None:
        self._memory = memory
        self._contact = contact
        self._config = config or ResidentOperatorContactConfig()

    async def contact_operator(
        self,
        request: OperatorContactRequest,
        *,
        turn: ResidentTurnRecord,
    ) -> ResidentOperatorContactReport:
        pending = await self._memory.read_operator_needed()
        if pending is not None and self._config.suppress_duplicate_pending:
            suppressed = OperatorContactResult(
                request=request,
                status=OperatorContactStatus.SUPPRESSED.value,
                emitted_ref=pending.path,
            )
            return ResidentOperatorContactReport(
                request=request,
                result=suppressed,
                suppressed_existing_pending=pending,
            )

        request = _with_inferred_purpose(request)
        pending_ref = await self._memory.write_operator_needed(
            question=request.question,
            reason=_reason_with_purpose(request),
            turn=turn,
        )
        result = await self._contact.ask(request)
        answer_ref = ""
        observations: tuple[ResidentPolicyObservation, ...] = ()
        if result.status == OperatorContactStatus.ANSWERED.value:
            answer_ref = await self._memory.write_operator_answer(result.answer)
            observations = await self._persist_feedback_observations(request, result)
        return ResidentOperatorContactReport(
            request=request,
            result=result,
            pending_ref=pending_ref,
            answer_ref=answer_ref,
            policy_observations=observations,
        )

    async def _persist_feedback_observations(
        self,
        request: OperatorContactRequest,
        result: OperatorContactResult,
    ) -> tuple[ResidentPolicyObservation, ...]:
        if not self._config.persist_operator_feedback:
            return ()
        observations = (
            _answer_observation(request, result),
            *_policy_observations_from_operator_text(result.answer),
        )
        persisted: list[ResidentPolicyObservation] = []
        for observation in observations[: self._config.max_policy_observations_per_answer]:
            await self._memory.write_policy_observation(observation)
            persisted.append(observation)
        return tuple(persisted)


async def ingest_resident_operator_reply(
    *,
    backend: ResidentWorkItemBackend,
    mandate: str,
    content: str,
    metadata: dict[str, Any] | None,
    memory: ResidentMemoryPort | None = None,
) -> ResidentOperatorReplyIngestionReport:
    """Persist a directed operator reply for a resident pending approval."""
    context = _operator_reply_context(metadata)
    contact_id = str(context.get("operator_contact_id") or "").strip()
    source_objective_id = str(context.get("source_objective_id") or "").strip()
    if not contact_id and not source_objective_id:
        return ResidentOperatorReplyIngestionReport(
            handled=False,
            reason="directed message does not target a resident operator contact",
        )

    objectives = tuple(await backend.list_objectives(mandate))
    objective = _objective_for_operator_reply(objectives, source_objective_id, contact_id)
    risk_boundaries = _string_tuple(context.get("risk_boundaries"))
    request = OperatorContactRequest(
        id=contact_id or f"operator-contact-{source_objective_id}",
        question=_operator_reply_question(metadata, context, objective),
        reason=str(context.get("reason") or "operator replied to resident help_needed").strip(),
        impact=str(context.get("impact") or "").strip(),
        kind=OperatorContactKind.HELP_NEEDED,
        purpose=_operator_contact_purpose(context),
        source_objective_id=source_objective_id or (objective.id if objective else ""),
        risk_boundaries=risk_boundaries,
        help_needed_outcome=dict(context),
    )
    approved = _operator_reply_approval(content)
    result = OperatorContactResult(
        request=request,
        status=OperatorContactStatus.ANSWERED.value,
        answer=str(content or "").strip(),
        approved=approved,
        responded_at=datetime.now(UTC),
    )
    contact_ref = await backend.write_operator_contact(result)
    objective_ref = ""
    portfolio_ref = ""
    if objective is not None:
        updated = _objective_with_operator_reply(objective, result)
        objective_ref = await backend.write_objective(updated)
        portfolio_ref = await _rewrite_portfolio_with_objective(
            backend,
            mandate,
            updated,
            decision_entry=(
                f"operator replied to {result.request.id}; "
                f"approved={_approval_label(approved)}"
            ),
        )
    policy_observations = await _persist_operator_reply_policy_observations(
        memory,
        result,
    )
    decision_ref = await backend.append_decision(
        mandate,
        (
            f"{datetime.now(UTC).isoformat()} [operator_reply] "
            f"{result.request.id} source_objective_id={result.request.source_objective_id} "
            f"approved={_approval_label(approved)}"
        ),
    )
    return ResidentOperatorReplyIngestionReport(
        handled=True,
        contact_ref=contact_ref,
        objective_ref=objective_ref,
        portfolio_ref=portfolio_ref,
        decision_ref=decision_ref,
        source_objective_id=result.request.source_objective_id,
        approved=approved,
        policy_observations=policy_observations,
        reason="operator reply persisted",
    )


def approved_risk_objective_ids_from_objectives(
    objectives: tuple[ResidentObjective, ...] | list[ResidentObjective],
) -> tuple[str, ...]:
    """Return objective IDs with durable operator approval markers."""
    approved: list[str] = []
    for objective in objectives:
        if any(item.startswith(_APPROVAL_MARKER_PREFIX) for item in objective.proof_progress):
            approved.append(objective.id)
    return tuple(sorted(set(approved)))


async def _persist_operator_reply_policy_observations(
    memory: ResidentMemoryPort | None,
    result: OperatorContactResult,
) -> tuple[ResidentPolicyObservation, ...]:
    if memory is None:
        return ()
    observations = (
        _answer_observation(result.request, result),
        *_policy_observations_from_operator_text(result.answer),
    )
    limit = ResidentOperatorContactConfig().max_policy_observations_per_answer
    persisted: list[ResidentPolicyObservation] = []
    for observation in observations[:limit]:
        await memory.write_policy_observation(observation)
        persisted.append(observation)
    return tuple(persisted)


def _with_inferred_purpose(request: OperatorContactRequest) -> OperatorContactRequest:
    if request.purpose != OperatorContactPurpose.CLARIFICATION:
        return request
    if request.risk_boundaries or _looks_like_approval(request.question):
        return OperatorContactRequest(
            question=request.question,
            reason=request.reason,
            impact=request.impact,
            kind=request.kind,
            purpose=OperatorContactPurpose.APPROVAL,
            id=request.id,
            source_objective_id=request.source_objective_id,
            risk_boundaries=request.risk_boundaries,
            tool_name=request.tool_name,
            tool_input=request.tool_input,
            help_needed_outcome=request.help_needed_outcome,
            created_at=request.created_at,
        )
    return request


def _reason_with_purpose(request: OperatorContactRequest) -> str:
    return f"{request.purpose.value}: {request.reason}".strip(": ")


def _answer_observation(
    request: OperatorContactRequest,
    result: OperatorContactResult,
) -> ResidentPolicyObservation:
    return ResidentPolicyObservation(
        subject=f"operator-contact:{request.purpose.value}",
        observation=result.answer,
        source="operator_answer",
        status="candidate",
    )


def _policy_observations_from_operator_text(text: str) -> tuple[ResidentPolicyObservation, ...]:
    observations: list[ResidentPolicyObservation] = []
    for line in str(text or "").splitlines():
        lowered = line.strip().casefold()
        if lowered.startswith("policy:"):
            observations.append(
                ResidentPolicyObservation(
                    subject="operator-feedback:policy",
                    observation=line.strip(),
                    source="operator_answer",
                    status="candidate",
                )
            )
        elif lowered.startswith("preference:"):
            observations.append(
                ResidentPolicyObservation(
                    subject="operator-feedback:preference",
                    observation=line.strip(),
                    source="operator_answer",
                    status="candidate",
                )
            )
    return tuple(observations)


def _looks_like_approval(question: str) -> bool:
    lowered = question.casefold()
    return any(
        needle in lowered
        for needle in (
            "approve",
            "approval",
            "allowed",
            "may i",
            "can i",
            "spend",
            "operate",
            "physical",
            "external",
        )
    )


def _operator_reply_context(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        return {}
    context = metadata.get("help_context")
    if isinstance(context, dict):
        return dict(context)
    context = metadata.get("context")
    if isinstance(context, dict):
        return dict(context)
    return dict(metadata)


def _operator_reply_question(
    metadata: dict[str, Any] | None,
    context: dict[str, Any],
    objective: ResidentObjective | None,
) -> str:
    if isinstance(metadata, dict):
        summary = str(metadata.get("help_summary") or "").strip()
        if summary:
            return summary
    question = str(context.get("question") or "").strip()
    if question:
        return question
    if objective is not None and objective.pending_question:
        return objective.pending_question
    if objective is not None:
        return objective.title
    return "Operator replied to resident help_needed."


def _operator_contact_purpose(context: dict[str, Any]) -> OperatorContactPurpose:
    raw = str(context.get("operator_contact_purpose") or "").strip()
    try:
        return OperatorContactPurpose(raw)
    except ValueError:
        return OperatorContactPurpose.CLARIFICATION


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _objective_for_operator_reply(
    objectives: tuple[ResidentObjective, ...],
    source_objective_id: str,
    contact_id: str,
) -> ResidentObjective | None:
    if source_objective_id:
        for objective in objectives:
            if objective.id == source_objective_id:
                return objective
    if contact_id:
        suffix = contact_id.removeprefix("operator-contact-")
        for objective in objectives:
            if objective.id == suffix:
                return objective
    pending = [
        item
        for item in objectives
        if item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
    ]
    return pending[0] if len(pending) == 1 else None


def _operator_reply_approval(answer: str) -> bool | None:
    normalized = f" {answer.casefold().strip()} "
    approval_tokens = (" approve", " approved", " yes", " proceed", " allow", " go ahead")
    if any(token in normalized for token in approval_tokens):
        return True
    denial_tokens = (" deny", " denied", " no", " reject", " stop", " do not")
    if any(token in normalized for token in denial_tokens):
        return False
    return None


def _objective_with_operator_reply(
    objective: ResidentObjective,
    result: OperatorContactResult,
) -> ResidentObjective:
    if result.approved is True:
        marker = (
            f"{_APPROVAL_MARKER_PREFIX} {result.request.id} "
            f"at {result.responded_at.isoformat() if result.responded_at else ''}"
        )
        return objective.with_updates(
            status=ResidentObjectiveStatus.CANDIDATE.value,
            pending_question="",
            proof_progress=_append_unique(objective.proof_progress, marker),
            last_reviewed_at=datetime.now(UTC),
        )
    if result.approved is False:
        marker = (
            f"{_DENIAL_MARKER_PREFIX} {result.request.id} "
            f"at {result.responded_at.isoformat() if result.responded_at else ''}"
        )
        return objective.with_updates(
            status=ResidentObjectiveStatus.BLOCKED.value,
            pending_question="",
            proof_progress=_append_unique(objective.proof_progress, marker),
            last_reviewed_at=datetime.now(UTC),
        )
    return objective.with_updates(
        status=ResidentObjectiveStatus.NEEDS_OPERATOR.value,
        pending_question=(
            "Operator replied, but the approval decision was unclear. Ask a sharper follow-up."
        ),
        proof_progress=_append_unique(
            objective.proof_progress,
            f"operator reply recorded without clear approval: {result.request.id}",
        ),
        last_reviewed_at=datetime.now(UTC),
    )


async def _rewrite_portfolio_with_objective(
    backend: ResidentWorkItemBackend,
    mandate: str,
    objective: ResidentObjective,
    *,
    decision_entry: str,
) -> str:
    objectives = tuple(await backend.list_objectives(mandate))
    merged = tuple(item if item.id != objective.id else objective for item in objectives)
    portfolio = await backend.read_portfolio(mandate)
    if portfolio is None:
        portfolio = ResidentPortfolio(mandate=mandate)
    portfolio = portfolio.with_objectives(
        merged,
        decision_history=_append_unique(portfolio.decision_history, decision_entry),
    )
    return await backend.write_portfolio(portfolio)


def _append_unique(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    item = item.strip()
    if not item or item in items:
        return items
    return (*items, item)


def _approval_label(approved: bool | None) -> str:
    if approved is True:
        return "true"
    if approved is False:
        return "false"
    return "unknown"
