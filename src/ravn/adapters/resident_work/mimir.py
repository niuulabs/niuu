"""Mimir resident work adapter."""

from __future__ import annotations

from datetime import UTC, datetime

from ravn.domain.operator_contact import OperatorContactResult
from ravn.domain.resident_portfolio import (
    ResidentDelegationRecord,
    ResidentDelegationReview,
    ResidentExecutionResult,
    ResidentObjective,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import _slug
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


class MimirResidentWorkAdapter(ResidentWorkItemBackend):
    """Mimir-backed resident work adapter."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

    async def read_portfolio(self, mandate: str) -> ResidentPortfolio | None:
        try:
            content = await self._mimir.read_page(_PORTFOLIO_PATH)
        except FileNotFoundError:
            return None
        portfolio = _parse_portfolio(content, mandate=mandate)
        objectives = tuple(await self.list_objectives(mandate))
        return portfolio.with_objectives(objectives) if objectives else portfolio

    async def write_portfolio(self, portfolio: ResidentPortfolio) -> str:
        await self._mimir.upsert_page(_PORTFOLIO_PATH, _render_portfolio(portfolio))
        return _PORTFOLIO_PATH

    async def list_objectives(self, mandate: str) -> list[ResidentObjective]:
        pages = await self._mimir.list_pages(prefix=_OBJECTIVE_PREFIX)
        objectives: list[ResidentObjective] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_objective(content)
            if parsed is not None:
                objectives.append(parsed)
        return objectives

    async def write_objective(self, objective: ResidentObjective) -> str:
        path = f"{_OBJECTIVE_PREFIX}/{objective.id}.md"
        await self._mimir.upsert_page(path, _render_objective(objective))
        return path

    async def append_decision(self, mandate: str, entry: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = f"{_DECISION_PREFIX}/{stamp}.md"
        await self._mimir.upsert_page(path, f"# Resident Portfolio Decision\n\n{entry}\n")
        return path

    async def list_refs(self, prefix: str) -> list[str]:
        pages = await self._mimir.list_pages(prefix=prefix)
        return sorted(getattr(page, "path", "") for page in pages if getattr(page, "path", ""))

    async def write_capability_discovery(self, discovery_id: str, content: str) -> str:
        path = f"{_CAPABILITY_DISCOVERY_PREFIX}/{_slug(discovery_id)}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def list_delegations(self, mandate: str) -> list[ResidentDelegationRecord]:
        pages = await self._mimir.list_pages(prefix=_DELEGATION_PREFIX)
        delegations: list[ResidentDelegationRecord] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_delegation(content)
            if parsed is not None:
                delegations.append(parsed)
        return delegations

    async def write_delegation(self, delegation: ResidentDelegationRecord) -> str:
        path = f"{_DELEGATION_PREFIX}/{delegation.id}.md"
        await self._mimir.upsert_page(path, _render_delegation(delegation))
        return path

    async def write_delegation_result(
        self,
        delegation_id: str,
        result: ResidentExecutionResult,
        content: str,
    ) -> str:
        filename = f"{_slug(delegation_id)}-{_slug(result.session_id)}.md"
        path = f"{_DELEGATION_RESULT_PREFIX}/{filename}"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_delegation_review(
        self,
        review: ResidentDelegationReview,
        content: str,
    ) -> str:
        path = f"{_DELEGATION_REVIEW_PREFIX}/{review.id}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_operator_contact(self, result: OperatorContactResult) -> str:
        path = f"{_OPERATOR_CONTACT_PREFIX}/{result.request.id}.md"
        await self._mimir.upsert_page(path, _render_operator_contact(result))
        return path

