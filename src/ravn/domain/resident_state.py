"""Canonical resident state/work boundaries."""

from __future__ import annotations

from typing import Any, Protocol

from ravn.domain.operator_contact import OperatorContactResult
from ravn.domain.physical_device import PhysicalActionResult, PhysicalCapability
from ravn.domain.resident_continuation import (
    ResidentBudgetSnapshot,
    ResidentMemoryEntry,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentTurnRecord,
)
from ravn.domain.resident_expert import (
    ExpertArtifact,
    ResidentDomainModel,
    ResidentWorkstream,
    WorkstreamExecutionResult,
)
from ravn.domain.resident_portfolio import (
    ResidentDelegationRecord,
    ResidentDelegationReview,
    ResidentExecutionResult,
    ResidentObjective,
    ResidentPortfolio,
)
from ravn.domain.resident_review import ResidentArtifactReview
from ravn.domain.wakeful_resident import (
    WakefulPortfolioStewardRecord,
    WakefulResidentCycleRecord,
)


class ResidentStatePort(Protocol):
    """Durable resident state boundary used by resident runtimes.

    This is intentionally storage-neutral; concrete stores belong behind
    adapters, never in the port name or contract.
    """

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]: ...

    async def write_turn(self, record: ResidentTurnRecord) -> str: ...

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str: ...

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str: ...

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]: ...

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str: ...

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str: ...

    async def read_operator_needed(self) -> ResidentMemoryEntry | None: ...

    async def write_operator_answer(self, answer: str) -> str: ...

    async def read_operator_answer(self) -> ResidentMemoryEntry | None: ...

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str: ...

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None: ...

    async def write_domain_model(self, model: ResidentDomainModel) -> str: ...

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]: ...

    async def write_workstream(self, workstream: ResidentWorkstream) -> str: ...

    async def write_artifact(self, artifact: ExpertArtifact, content: str) -> str: ...

    async def write_consolidation(
        self,
        model: ResidentDomainModel,
        result: WorkstreamExecutionResult,
    ) -> str: ...

    async def list_wake_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulResidentCycleRecord]: ...

    async def write_wake_record(self, record: WakefulResidentCycleRecord) -> str: ...

    async def list_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulPortfolioStewardRecord]: ...

    async def write_record(self, record: WakefulPortfolioStewardRecord) -> str: ...

    async def list_reviews(self, review_key: str = "") -> list[ResidentArtifactReview]: ...

    async def write_review(self, review: ResidentArtifactReview) -> str: ...

    async def write_review_audit(self, content: str) -> str: ...

    async def write_capability(self, capability: PhysicalCapability) -> str: ...

    async def write_result(self, result: PhysicalActionResult) -> str: ...

    async def write_physical_audit(self, content: str) -> str: ...

    async def write_reasoning(self, reasoning: Any) -> str: ...

    async def list_refs(self, prefix: str = "") -> list[str]: ...


class ResidentWorkPort(Protocol):
    """Durable resident work boundary used by resident runtimes."""

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None: ...

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str: ...

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]: ...

    async def write_objective(self, objective: ResidentObjective) -> str: ...

    async def append_decision(self, mandate: str, entry: str) -> str: ...

    async def list_refs(self, prefix: str) -> list[str]: ...

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str: ...

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]: ...

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str: ...

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str: ...

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str: ...

    async def write_operator_contact(self, result: OperatorContactResult) -> str: ...
