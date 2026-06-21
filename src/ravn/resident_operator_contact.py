"""Canonical resident operator-contact coordination."""

from __future__ import annotations

from dataclasses import dataclass

from ravn.domain.operator_contact import (
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
