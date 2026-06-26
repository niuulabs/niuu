"""Filesystem-backed resident work item and capability discovery backends."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from ravn.domain.operator_contact import OperatorContactResult
from ravn.domain.resident_portfolio import (
    CapabilityDiscoveryPort,
    ResidentCapabilityDiscoveryResult,
    ResidentCapabilityGap,
    ResidentCapabilityOption,
    ResidentDelegationRecord,
    ResidentDelegationReview,
    ResidentExecutionResult,
    ResidentObjective,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_portfolio import (
    _CAPABILITY_DISCOVERY_PREFIX,
    _DECISION_PREFIX,
    _DELEGATION_PREFIX,
    _DELEGATION_RESULT_PREFIX,
    _DELEGATION_REVIEW_PREFIX,
    _OBJECTIVE_PREFIX,
    _OPERATOR_CONTACT_PREFIX,
    _PORTFOLIO_PATH,
    _parse_delegation,
    _parse_objective,
    _parse_portfolio,
    _render_delegation,
    _render_objective,
    _render_operator_contact,
    _render_portfolio,
)
from ravn.resident_text import timestamp_slug


class LocalResidentWorkItemBackend(ResidentWorkItemBackend):
    """Filesystem-backed resident work item backend."""

    # TODO: Add a Ting-backed ResidentWorkItemBackend adapter once Ting can use
    # Mimir as a lightweight ticket backend.

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        path = self._root / _PORTFOLIO_PATH
        if not path.exists():
            return None
        portfolio = _parse_portfolio(path.read_text(encoding="utf-8"), mandate=mandate)
        objectives = tuple(await self.list_objectives(mandate))
        return portfolio.with_objectives(objectives) if objectives else portfolio

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        return self._write(Path(_PORTFOLIO_PATH), _render_portfolio(portfolio))

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        base = self._root / _OBJECTIVE_PREFIX
        if not base.exists():
            return []
        objectives: list[ResidentObjective] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_objective(path.read_text(encoding="utf-8"))
            if parsed is not None:
                objectives.append(parsed)
        return objectives

    async def write_objective(self, objective: ResidentObjective) -> str:
        rel = Path(_OBJECTIVE_PREFIX) / f"{objective.id}.md"
        return self._write(rel, _render_objective(objective))

    async def append_decision(self, mandate: str, entry: str) -> str:
        stamp = timestamp_slug(datetime.now(UTC))
        rel = Path(_DECISION_PREFIX) / f"{stamp}.md"
        return self._write(rel, f"# Resident Portfolio Decision\n\n{entry}\n")

    async def list_refs(self, prefix: str) -> list[str]:
        base = self._root / prefix
        if not base.exists():
            return []
        return sorted(str(path.relative_to(self._root)) for path in base.glob("*.md"))

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str:
        rel = Path(_CAPABILITY_DISCOVERY_PREFIX) / f"{_slug(discovery_id)}.md"
        return self._write(rel, content)

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        base = self._root / _DELEGATION_PREFIX
        if not base.exists():
            return []
        delegations: list[ResidentDelegationRecord] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_delegation(path.read_text(encoding="utf-8"))
            if parsed is not None:
                delegations.append(parsed)
        return delegations

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        rel = Path(_DELEGATION_PREFIX) / f"{delegation.id}.md"
        return self._write(rel, _render_delegation(delegation))

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        filename = f"{_slug(delegation_id)}-{_slug(result.session_id)}.md"
        rel = Path(_DELEGATION_RESULT_PREFIX) / filename
        return self._write(rel, content)

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        rel = Path(_DELEGATION_REVIEW_PREFIX) / f"{review.id}.md"
        return self._write(rel, content)

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        rel = Path(_OPERATOR_CONTACT_PREFIX) / f"{result.request.id}.md"
        return self._write(rel, _render_operator_contact(result))

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


class LocalCapabilityDiscoveryBackend(CapabilityDiscoveryPort):
    """Deterministic bounded capability discovery backend for local proof/tests."""

    async def discover(
        self,
        mandate: str,
        gap: ResidentCapabilityGap,
    ) -> ResidentCapabilityDiscoveryResult:
        capability = _compact_line(gap.capability or gap.summary, limit=120)
        safe_option = ResidentCapabilityOption(
            id=f"evaluate-{_slug(capability)}",
            title=f"Evaluate available capability path for {capability}",
            summary=(
                "Inspect existing tools, workflows, and documentation before building anything new."
            ),
            required_tools=("read_only_catalog_inspection",),
            required_workflows=("local_dry_run",),
            safe_next_experiment=(
                "Run a read-only catalog/workspace inspection and record whether a path exists."
            ),
            evidence=gap.source_evidence,
        )
        adapter_option = ResidentCapabilityOption(
            id=f"build-adapter-{_slug(capability)}",
            title=f"Build or wrap a capability adapter for {capability}",
            summary="Create the smallest adapter/workflow after existing paths are ruled out.",
            required_tools=("code", "tests"),
            required_workflows=("adapter_prototype",),
            required_adapters=("resident_capability_adapter",),
            risks=gap.risk_boundaries,
            approval_required=bool(gap.risk_boundaries),
            safe_next_experiment="Draft an adapter contract and dry-run it without side effects.",
            evidence=gap.source_evidence,
        )
        options = [safe_option, adapter_option]
        if gap.risk_boundaries:
            options.append(
                ResidentCapabilityOption(
                    id=f"approve-{_slug(capability)}",
                    title=f"Ask operator about risk boundaries for {capability}",
                    summary="Clarify human approval before touching bounded or external effects.",
                    risks=gap.risk_boundaries,
                    approval_required=True,
                    safe_next_experiment="Ask the operator for explicit approval boundaries.",
                    evidence=gap.source_evidence,
                )
            )
        return ResidentCapabilityDiscoveryResult(
            gap=gap,
            capability_summary=f"Capability gap: {capability}",
            why_it_matters=(
                "The resident cannot safely advance related objectives until this capability "
                "has a known path, adapter, or approval boundary."
            ),
            known_constraints=tuple(
                item
                for item in (
                    *gap.risk_boundaries,
                    *gap.blocked_dependencies,
                    *gap.required_capabilities,
                )
                if item
            ),
            candidate_options=tuple(options),
            recommended_option_id=safe_option.id,
            recommended_safe_next_experiment=safe_option.safe_next_experiment,
            unresolved_questions=(
                ("What approval boundary applies?",) if gap.risk_boundaries else ()
            ),
        )
