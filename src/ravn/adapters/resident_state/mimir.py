"""Mimir/local resident state adapters."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

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
from ravn.domain.resident_review import ResidentArtifactReview
from ravn.domain.resident_state import ResidentStatePort
from ravn.domain.wakeful_resident import WakefulPortfolioStewardRecord, WakefulResidentCycleRecord
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import (
    LocalResidentMemory,
    _compact_line,
    _first_heading_or_line,
    _operator_answer_is_consumed,
    _operator_marker_is_pending,
    _OPERATOR_ANSWER_PATH,
    _OPERATOR_NEEDED_PATH,
    _parse_policy_observation,
    _render_answered_operator_needed,
    _render_budget_snapshot,
    _render_consumed_operator_answer,
    _render_operator_answer,
    _render_operator_needed,
    _render_policy_decision,
    _render_policy_observation,
    _render_turn_record,
    _slug,
    _timestamp_slug,
)
from ravn.resident_expert import (
    LocalResidentDomainExpertMemory,
    _DOMAIN_MODEL_PATH,
    _parse_domain_model_page,
    _parse_workstream_page,
    _render_consolidation,
    _render_domain_model,
    _render_workstream,
)
from ravn.resident_physical import (
    LocalResidentPhysicalMemory,
    _AUDIT_PREFIX as _PHYSICAL_AUDIT_PREFIX,
    _CAPABILITY_PREFIX,
    _PHYSICAL_PREFIX,
    _REASONING_PREFIX,
    _RESULT_PREFIX,
    _render_capability,
    _render_reasoning,
    _render_result,
    _stamp as _physical_stamp,
)
from ravn.resident_review import (
    LocalResidentReviewMemory,
    _parse_review,
    _REVIEW_AUDIT_PREFIX,
    _REVIEW_PREFIX,
    _render_review,
    _stamp as _review_stamp,
)
from ravn.wakeful_resident import (
    LocalWakefulPortfolioStewardMemory,
    LocalWakefulResidentMemory,
    _parse_portfolio_steward_record,
    _parse_wake_record,
    _render_portfolio_steward_record,
    _render_wake_record,
    _WAKE_PREFIX,
)


class MimirResidentState(ResidentStatePort):
    """One Mimir-backed resident state adapter."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        self._mimir = mimir
        self._prefix = continuation_prefix.strip("/").strip() or "resident/continuation"

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        query = _compact_line(mandate) or "resident continuation"
        pages = await self._mimir.search(query)
        entries: list[ResidentMemoryEntry] = []
        for page in pages:
            path = getattr(page.meta, "path", "")
            if not path.startswith(self._prefix):
                continue
            summary = getattr(page.meta, "summary", "") or _first_heading_or_line(page.content)
            entries.append(ResidentMemoryEntry(path=path, summary=summary, content=page.content))
            if len(entries) >= limit:
                break
        return entries

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = f"{self._prefix}/turns/{stamp}-{record.turn_index}.md"
        await self._mimir.upsert_page(path, _render_turn_record(record))
        return path

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        path = f"{self._prefix}/budget/latest.md"
        await self._mimir.upsert_page(
            path,
            _render_budget_snapshot(snapshot, updated_at=datetime.now(UTC)),
        )
        return path

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        slug = _slug(observation.subject) or "policy-observation"
        path = f"{self._prefix}/policy/{slug}.md"
        await self._mimir.upsert_page(path, _render_policy_observation(observation))
        return path

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        observations: list[ResidentPolicyObservation] = []
        pages = await self._mimir.list_pages(prefix=f"{self._prefix}/policy")
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            path = str(getattr(meta, "path", "") or "")
            if not path:
                continue
            try:
                parsed = _parse_policy_observation(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if parsed is not None:
                observations.append(parsed)
        return observations

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        stamp = decision.created_at.strftime("%Y%m%dT%H%M%SZ")
        slug = _slug(decision.action_title) or "policy-decision"
        path = f"{self._prefix}/policy-decisions/{stamp}-{decision.turn_index}-{slug}.md"
        await self._mimir.upsert_page(path, _render_policy_decision(decision))
        return path

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str:
        path = f"{self._prefix}/{_OPERATOR_NEEDED_PATH}"
        await self._mimir.upsert_page(
            path,
            _render_operator_needed(
                question=question,
                reason=reason,
                turn=turn,
                status="pending",
            ),
        )
        return path

    async def read_operator_needed(self) -> ResidentMemoryEntry | None:
        path = f"{self._prefix}/{_OPERATOR_NEEDED_PATH}"
        try:
            content = await self._mimir.read_page(path)
        except FileNotFoundError:
            return None
        if not _operator_marker_is_pending(content):
            return None
        return ResidentMemoryEntry(path=path, summary=_first_heading_or_line(content), content=content)

    async def write_operator_answer(self, answer: str) -> str:
        now = datetime.now(UTC)
        answer_path = f"{self._prefix}/{_OPERATOR_ANSWER_PATH}"
        await self._mimir.upsert_page(answer_path, _render_operator_answer(answer, answered_at=now))
        history_path = f"{self._prefix}/operator-answers/{_timestamp_slug(now)}.md"
        await self._mimir.upsert_page(history_path, _render_operator_answer(answer, answered_at=now))
        marker_path = f"{self._prefix}/{_OPERATOR_NEEDED_PATH}"
        try:
            prior = await self._mimir.read_page(marker_path)
        except FileNotFoundError:
            prior = ""
        await self._mimir.upsert_page(
            marker_path,
            _render_answered_operator_needed(prior, answer_path=answer_path, answered_at=now),
        )
        return answer_path

    async def read_operator_answer(self) -> ResidentMemoryEntry | None:
        path = f"{self._prefix}/{_OPERATOR_ANSWER_PATH}"
        try:
            content = await self._mimir.read_page(path)
        except FileNotFoundError:
            return None
        if _operator_answer_is_consumed(content):
            return None
        return ResidentMemoryEntry(path=path, summary=_first_heading_or_line(content), content=content)

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        path = answer.path or f"{self._prefix}/{_OPERATOR_ANSWER_PATH}"
        try:
            prior = await self._mimir.read_page(path)
        except FileNotFoundError:
            prior = answer.content
        await self._mimir.upsert_page(
            path,
            _render_consumed_operator_answer(prior, consumed_at=datetime.now(UTC)),
        )
        return path

    async def read_domain_model(self, mandate: str) -> ResidentDomainModel | None:
        try:
            content = await self._mimir.read_page(_DOMAIN_MODEL_PATH)
        except FileNotFoundError:
            return None
        return _parse_domain_model_page(content, mandate=mandate)

    async def write_domain_model(self, model: ResidentDomainModel) -> str:
        await self._mimir.upsert_page(_DOMAIN_MODEL_PATH, _render_domain_model(model))
        return _DOMAIN_MODEL_PATH

    async def list_workstreams(self, domain_model_ref: str) -> list[ResidentWorkstream]:
        pages = await self._mimir.list_pages(prefix="resident/domain-expert/workstreams")
        workstreams: list[ResidentWorkstream] = []
        for meta in pages:
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_workstream_page(content)
            if parsed is not None:
                workstreams.append(parsed)
        return workstreams

    async def write_workstream(self, workstream: ResidentWorkstream) -> str:
        path = f"resident/domain-expert/workstreams/{workstream.id}.md"
        await self._mimir.upsert_page(path, _render_workstream(workstream))
        return path

    async def write_artifact(self, artifact: ExpertArtifact, content: str) -> str:
        path = f"resident/domain-expert/artifacts/{_slug(artifact.title)}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_consolidation(
        self,
        model: ResidentDomainModel,
        result: WorkstreamExecutionResult,
    ) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = f"resident/domain-expert/consolidations/{stamp}-{result.workstream_id}.md"
        await self._mimir.upsert_page(path, _render_consolidation(model, result))
        return path

    async def list_wake_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulResidentCycleRecord]:
        pages = await self._mimir.list_pages(prefix=f"{_WAKE_PREFIX}/cycles")
        records: list[WakefulResidentCycleRecord] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", ""), reverse=True):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_wake_record(content, mandate=mandate)
            if parsed is not None:
                records.append(parsed)
            if len(records) >= limit:
                break
        return records

    async def write_wake_record(self, record: WakefulResidentCycleRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = f"{_WAKE_PREFIX}/cycles/{stamp}-{record.cycle_number}.md"
        await self._mimir.upsert_page(path, _render_wake_record(record))
        return path

    async def list_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulPortfolioStewardRecord]:
        pages = await self._mimir.list_pages(prefix=f"{_WAKE_PREFIX}/portfolio-steward")
        records: list[WakefulPortfolioStewardRecord] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", ""), reverse=True):
            try:
                content = await self._mimir.read_page(meta.path)
            except FileNotFoundError:
                continue
            parsed = _parse_portfolio_steward_record(content, mandate=mandate)
            if parsed is not None:
                records.append(parsed)
            if len(records) >= limit:
                break
        return records

    async def write_record(self, record: WakefulPortfolioStewardRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%S%fZ")
        path = f"{_WAKE_PREFIX}/portfolio-steward/{stamp}-{record.wake_number}.md"
        await self._mimir.upsert_page(path, _render_portfolio_steward_record(record))
        return path

    async def list_reviews(self, review_key: str = "") -> list[ResidentArtifactReview]:
        pages = await self._mimir.list_pages(prefix=_REVIEW_PREFIX)
        reviews: list[ResidentArtifactReview] = []
        for meta in sorted(pages, key=lambda page: getattr(page, "path", "")):
            path = str(getattr(meta, "path", "") or "")
            if "/audits/" in path:
                continue
            try:
                content = await self._mimir.read_page(path)
            except FileNotFoundError:
                continue
            parsed = _parse_review(content)
            if parsed is None:
                continue
            if review_key and parsed.review_key != review_key:
                continue
            reviews.append(parsed)
        return reviews

    async def write_review(self, review: ResidentArtifactReview) -> str:
        path = f"{_REVIEW_PREFIX}/{review.id}.md"
        await self._mimir.upsert_page(path, _render_review(review))
        return path

    async def write_review_audit(self, content: str) -> str:
        path = f"{_REVIEW_AUDIT_PREFIX}/{_review_stamp(datetime.now(UTC))}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_capability(self, capability: PhysicalCapability) -> str:
        path = f"{_CAPABILITY_PREFIX}/{_slug(capability.id)}.md"
        await self._mimir.upsert_page(path, _render_capability(capability))
        return path

    async def write_result(self, result: PhysicalActionResult) -> str:
        stamp = result.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = f"{_RESULT_PREFIX}/{stamp}-{_slug(result.capability_id)}-{_slug(result.kind)}.md"
        await self._mimir.upsert_page(path, _render_result(result))
        return path

    async def write_physical_audit(self, content: str) -> str:
        path = f"{_PHYSICAL_AUDIT_PREFIX}/{_physical_stamp(datetime.now(UTC))}.md"
        await self._mimir.upsert_page(path, content)
        return path

    async def write_reasoning(self, reasoning) -> str:
        path = f"{_REASONING_PREFIX}/{_physical_stamp(reasoning.created_at)}.md"
        await self._mimir.upsert_page(path, _render_reasoning(reasoning))
        return path

    async def list_refs(self, prefix: str = _PHYSICAL_PREFIX) -> list[str]:
        pages = await self._mimir.list_pages(prefix=prefix)
        return sorted(str(getattr(page, "path", "")) for page in pages if getattr(page, "path", ""))


class LocalResidentState(
    LocalResidentMemory,
    LocalResidentDomainExpertMemory,
    LocalWakefulResidentMemory,
    LocalWakefulPortfolioStewardMemory,
    LocalResidentReviewMemory,
    LocalResidentPhysicalMemory,
    ResidentStatePort,
):
    """Filesystem-backed resident state adapter for local development/tests."""

    def __init__(
        self,
        root: Path,
        *,
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        LocalResidentMemory.__init__(self, root, prefix=continuation_prefix)
