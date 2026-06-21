"""Wakeful resident runtime V0.

This layer coordinates multiple bounded resident expert passes.  It does not
schedule time and it does not contain a domain playbook; it records why the
resident woke, what changed, and why the runtime continued, asked, slept, or
stopped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ravn.domain.models import TokenUsage
from ravn.domain.resident_continuation import ResidentBudgetLimits, ResidentBudgetSnapshot
from ravn.domain.resident_expert import (
    ExpertLoopDecisionKind,
    ResidentDomainExpertMemoryPort,
    ResidentDomainExpertRun,
    ResidentDomainModel,
    ResidentWorkstream,
    ResidentWorkstreamStatus,
)
from ravn.domain.resident_portfolio import ResidentObjectiveDryRunPreview
from ravn.domain.wakeful_resident import (
    ResidentExpertLoopPort,
    ResidentPortfolioStewardPort,
    WakefulPortfolioStewardActionKind,
    WakefulPortfolioStewardMemoryPort,
    WakefulPortfolioStewardRecord,
    WakefulPortfolioStewardRun,
    WakefulResidentCycleRecord,
    WakefulResidentDecisionKind,
    WakefulResidentMemoryPort,
    WakefulResidentRun,
)
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import ResidentRunBudget, _compact_line
from ravn.resident_portfolio import ResidentPortfolioValidator

_DOMAIN_MODEL_REF = "resident/domain-expert/domain-model.md"
_WAKE_PREFIX = "resident/wakeful"
_CANONICAL_WAKEFUL_RUNTIME = "ravn.wakeful_resident.WakefulResidentRuntime"


@dataclass(frozen=True)
class WakefulResidentConfig:
    """Bounds and idle behavior for one wakeful runtime invocation."""

    max_wake_cycles: int = 3
    max_wall_clock_seconds: float = 1800.0
    max_tokens: int = 0
    sleep_when_idle: bool = False
    recent_wake_record_limit: int = 5


@dataclass(frozen=True)
class WakefulPortfolioStewardConfig:
    """Bounds for wakeful portfolio stewardship integration."""

    max_wake_passes: int = 3
    max_wall_clock_seconds: float = 1800.0
    max_tokens: int = 0


class LocalWakefulResidentMemory(WakefulResidentMemoryPort):
    """Filesystem-backed wake cycle memory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def list_wake_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulResidentCycleRecord]:
        base = self._root / _WAKE_PREFIX / "cycles"
        if not base.exists():
            return []
        paths = sorted(base.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        records: list[WakefulResidentCycleRecord] = []
        for path in paths:
            parsed = _parse_wake_record(path.read_text(encoding="utf-8"), mandate=mandate)
            if parsed is not None:
                records.append(parsed)
            if len(records) >= limit:
                break
        return records

    async def write_wake_record(self, record: WakefulResidentCycleRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        rel = Path(_WAKE_PREFIX) / "cycles" / f"{stamp}-{record.cycle_number}.md"
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_wake_record(record), encoding="utf-8")
        return str(rel)


class MimirWakefulResidentMemory(WakefulResidentMemoryPort):
    """Mimir-backed wake cycle memory."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

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


class LocalWakefulPortfolioStewardMemory(WakefulPortfolioStewardMemoryPort):
    """Filesystem-backed wakeful portfolio stewardship memory."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    async def list_records(
        self,
        mandate: str,
        *,
        limit: int = 5,
    ) -> list[WakefulPortfolioStewardRecord]:
        base = self._root / _WAKE_PREFIX / "portfolio-steward"
        if not base.exists():
            return []
        paths = sorted(base.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        records: list[WakefulPortfolioStewardRecord] = []
        for path in paths:
            parsed = _parse_portfolio_steward_record(
                path.read_text(encoding="utf-8"),
                mandate=mandate,
            )
            if parsed is not None:
                records.append(parsed)
            if len(records) >= limit:
                break
        return records

    async def write_record(self, record: WakefulPortfolioStewardRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%S%fZ")
        rel = Path(_WAKE_PREFIX) / "portfolio-steward" / f"{stamp}-{record.wake_number}.md"
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_portfolio_steward_record(record), encoding="utf-8")
        return str(rel)


class MimirWakefulPortfolioStewardMemory(WakefulPortfolioStewardMemoryPort):
    """Mimir-backed wakeful portfolio stewardship memory."""

    def __init__(self, mimir: MimirPort) -> None:
        self._mimir = mimir

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


class WakefulResidentRuntime:
    """Runs multiple bounded wake cycles from a mandate."""

    def __init__(
        self,
        *,
        expert_loop: ResidentExpertLoopPort,
        expert_memory: ResidentDomainExpertMemoryPort,
        wake_memory: WakefulResidentMemoryPort,
        config: WakefulResidentConfig | None = None,
    ) -> None:
        self._expert_loop = expert_loop
        self._expert_memory = expert_memory
        self._wake_memory = wake_memory
        self._config = config or WakefulResidentConfig()

    async def run(self, mandate: str) -> WakefulResidentRun:
        budget = ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=self._config.max_wake_cycles,
                max_wall_clock_seconds=self._config.max_wall_clock_seconds,
                max_tokens=self._config.max_tokens,
            )
        )
        cycles: list[WakefulResidentCycleRecord] = []
        final_decision = WakefulResidentDecisionKind.STOP
        final_reason = "no wake cycles ran"

        for cycle_number in range(1, self._config.max_wake_cycles + 1):
            budget_decision = budget.can_continue()
            if not budget_decision.allowed:
                final_decision = WakefulResidentDecisionKind.STOP
                final_reason = budget_decision.reason
                break

            prior_model = await self._expert_memory.read_domain_model(mandate)
            prior_ref = _DOMAIN_MODEL_REF if prior_model is not None else ""
            prior_workstreams = await _list_workstreams(self._expert_memory, prior_model)
            prior_wake_records = await self._wake_memory.list_wake_records(
                mandate,
                limit=self._config.recent_wake_record_limit,
            )
            runtime_audit = derive_runtime_duplication_audit(
                recent_wake_records=tuple(prior_wake_records),
            )
            attention_reason = derive_attention_reason(
                mandate=mandate,
                domain_model=prior_model,
                workstreams=tuple(prior_workstreams),
                recent_wake_records=tuple(prior_wake_records),
                budget=budget.snapshot(),
            )

            operator_workstream = _operator_gated_workstream(prior_workstreams)
            if operator_workstream is not None:
                record = _build_operator_needed_record(
                    cycle_number=cycle_number,
                    mandate=mandate,
                    prior_ref=prior_ref,
                    attention_reason=attention_reason,
                    workstream=operator_workstream,
                    budget=budget.record_usage(TokenUsage(input_tokens=0, output_tokens=0)),
                    runtime_audit=runtime_audit,
                )
                await self._wake_memory.write_wake_record(record)
                cycles.append(record)
                final_decision = record.decision
                final_reason = record.decision_reason
                break

            expert_run = await self._expert_loop.run(mandate)
            snapshot = budget.record_usage(expert_run.budget.usage)
            decision, reason = _runtime_decision_after_expert_run(
                expert_run,
                snapshot=snapshot,
                budget=budget,
                config=self._config,
            )
            record = _build_wake_record(
                cycle_number=cycle_number,
                mandate=mandate,
                prior_ref=prior_ref,
                attention_reason=attention_reason,
                prior_model=prior_model,
                prior_workstreams=tuple(prior_workstreams),
                expert_run=expert_run,
                decision=decision,
                decision_reason=reason,
                budget=snapshot,
                runtime_audit=runtime_audit,
            )
            await self._wake_memory.write_wake_record(record)
            cycles.append(record)
            final_decision = decision
            final_reason = reason

            if decision != WakefulResidentDecisionKind.CONTINUE:
                break

        return WakefulResidentRun(
            mandate=mandate,
            cycles=tuple(cycles),
            final_decision=final_decision,
            final_reason=final_reason,
            budget=budget.snapshot(),
        )


class WakefulPortfolioStewardRuntime:
    """Runs bounded wake passes that decide whether portfolio stewardship matters."""

    def __init__(
        self,
        *,
        backend: Any,
        steward: ResidentPortfolioStewardPort,
        memory: WakefulPortfolioStewardMemoryPort,
        config: WakefulPortfolioStewardConfig | None = None,
    ) -> None:
        self._backend = backend
        self._steward = steward
        self._memory = memory
        self._config = config or WakefulPortfolioStewardConfig()

    async def run(self, mandate: str) -> WakefulPortfolioStewardRun:
        budget = ResidentRunBudget(
            ResidentBudgetLimits(
                max_turns=self._config.max_wake_passes,
                max_wall_clock_seconds=self._config.max_wall_clock_seconds,
                max_tokens=self._config.max_tokens,
            )
        )
        records: list[WakefulPortfolioStewardRecord] = []
        final_action = WakefulPortfolioStewardActionKind.STOP
        final_reason = "no wakeful portfolio steward passes ran"

        for wake_number in range(1, self._config.max_wake_passes + 1):
            budget_decision = budget.can_continue()
            if not budget_decision.allowed:
                final_action = WakefulPortfolioStewardActionKind.STOP
                final_reason = budget_decision.reason
                break

            record = await self._run_wake_pass(
                mandate,
                wake_number=wake_number,
                budget=budget,
            )
            await self._memory.write_record(record)
            records.append(record)
            final_action, final_reason = _final_wakeful_steward_action(record)

            if record.action_taken != WakefulPortfolioStewardActionKind.RUN_STEWARD:
                break
            if final_action in {
                WakefulPortfolioStewardActionKind.ASK_OPERATOR,
                WakefulPortfolioStewardActionKind.SLEEP,
                WakefulPortfolioStewardActionKind.STOP,
            }:
                break

        if (
            records
            and len(records) >= self._config.max_wake_passes
            and final_action == WakefulPortfolioStewardActionKind.RUN_STEWARD
        ):
            final_action = WakefulPortfolioStewardActionKind.STOP
            final_reason = (
                f"Stopped after configured wakeful portfolio steward pass limit: "
                f"{self._config.max_wake_passes}."
            )

        return WakefulPortfolioStewardRun(
            mandate=mandate,
            records=tuple(records),
            final_action=final_action,
            final_reason=final_reason,
            budget=budget.snapshot(),
        )

    async def _run_wake_pass(
        self,
        mandate: str,
        *,
        wake_number: int,
        budget: ResidentRunBudget,
    ) -> WakefulPortfolioStewardRecord:
        recent_records = tuple(await self._memory.list_records(mandate, limit=5))
        validation = await ResidentPortfolioValidator(backend=self._backend).validate(mandate)
        attention_reason = derive_portfolio_steward_attention_reason(
            mandate=mandate,
            validation=validation,
            recent_records=recent_records,
        )
        direct_action = _direct_portfolio_wake_action(validation)
        if direct_action is not None:
            snapshot = budget.record_usage(TokenUsage(input_tokens=0, output_tokens=0))
            return WakefulPortfolioStewardRecord(
                wake_number=wake_number,
                mandate=mandate,
                attention_reason=attention_reason,
                validation_verdict=validation.verdict,
                selected_objective=validation.selected_objective,
                action_taken=direct_action,
                operator_questions=_operator_questions_from_validation(validation),
                budget=snapshot,
                final_suggested_next_action=_direct_portfolio_wake_reason(
                    direct_action,
                    validation,
                ),
            )

        steward_run = await self._steward.run(mandate)
        snapshot = budget.record_usage(steward_run.budget.usage)
        operator_questions = tuple(
            question for report in steward_run.passes for question in report.operator_questions
        )
        persisted_refs = tuple(
            ref for report in steward_run.passes for ref in report.persisted_refs
        )
        return WakefulPortfolioStewardRecord(
            wake_number=wake_number,
            mandate=mandate,
            attention_reason=attention_reason,
            validation_verdict=validation.verdict,
            selected_objective=validation.selected_objective,
            action_taken=WakefulPortfolioStewardActionKind.RUN_STEWARD,
            steward_pass_count=len(steward_run.passes),
            steward_final_action=steward_run.final_action.value,
            steward_summary=_steward_summary(steward_run),
            operator_questions=operator_questions,
            persisted_refs=persisted_refs,
            budget=snapshot,
            final_suggested_next_action=steward_run.final_suggested_next_action,
        )


def derive_attention_reason(
    *,
    mandate: str,
    domain_model: ResidentDomainModel | None,
    workstreams: tuple[ResidentWorkstream, ...],
    recent_wake_records: tuple[WakefulResidentCycleRecord, ...] = (),
    budget: ResidentBudgetSnapshot | None = None,
) -> str:
    """Derive why a resident wake cycle deserves attention from state."""

    if domain_model is None:
        return "no domain model exists; orient from the resident mandate"

    operator_needed = _operator_gated_workstream(workstreams)
    if operator_needed is not None:
        return f"operator input is needed for workstream: {operator_needed.title}"

    actionable = _actionable_workstreams(workstreams)
    if actionable:
        return f"actionable resident workstream exists: {actionable[0].title}"

    if domain_model.open_questions:
        return f"open domain question needs attention: {domain_model.open_questions[0]}"
    if domain_model.capability_gaps:
        return f"capability gap may deserve work: {domain_model.capability_gaps[0]}"
    if domain_model.opportunities:
        return f"domain opportunity may deserve work: {domain_model.opportunities[0]}"
    if domain_model.hypotheses:
        return f"domain hypothesis may deserve work: {domain_model.hypotheses[0]}"
    if recent_wake_records:
        return f"recent wake cycle ended with {recent_wake_records[0].decision.value}"
    if budget is not None:
        return f"resident budget remains after {budget.turns_used} wake cycles"
    if mandate.strip():
        return "resident mandate remains active"
    return "resident state is empty"


def derive_runtime_duplication_audit(
    *,
    recent_wake_records: tuple[WakefulResidentCycleRecord, ...],
) -> tuple[str, ...]:
    """Summarize canonical wakeful runtime continuity from persisted records."""

    audit = [
        f"canonical_runtime: {_CANONICAL_WAKEFUL_RUNTIME}",
        f"recent_wake_records_considered: {len(recent_wake_records)}",
    ]
    if not recent_wake_records:
        audit.append("duplication_check: no prior wake records found")
        return tuple(audit)

    canonical_marker = f"canonical_runtime: {_CANONICAL_WAKEFUL_RUNTIME}"
    canonical_records = sum(
        1 for record in recent_wake_records if canonical_marker in record.runtime_audit
    )
    unaudited_records = len(recent_wake_records) - canonical_records
    decisions = ", ".join(record.decision.value for record in recent_wake_records)
    audit.extend(
        (
            f"prior_canonical_runtime_records: {canonical_records}",
            f"prior_records_without_runtime_audit: {unaudited_records}",
            f"prior_decisions_considered: {decisions}",
        )
    )
    if unaudited_records:
        audit.append("duplication_check: prior wake records need canonical audit backfill")
        return tuple(audit)

    audit.append("duplication_check: resident wake memory shows canonical runtime continuity")
    return tuple(audit)


def derive_portfolio_steward_attention_reason(
    *,
    mandate: str,
    validation: Any,
    recent_records: tuple[WakefulPortfolioStewardRecord, ...] = (),
) -> str:
    """Derive why portfolio stewardship deserves wakeful attention."""

    if any(issue.code == "missing_portfolio" for issue in validation.issues):
        return "no resident portfolio exists; initialize stewardship from the mandate"
    if validation.issues:
        issue = validation.issues[0]
        return f"portfolio validation issue needs stewardship: {issue.code}"
    if validation.warnings:
        warning = validation.warnings[0]
        return f"portfolio validation warning needs stewardship: {warning.code}"
    if validation.operator_needed_objectives and not validation.eligible_objectives:
        return (
            f"portfolio operator input is needed: {validation.operator_needed_objectives[0].title}"
        )
    if validation.selected_objective is not None:
        return (
            "portfolio selected objective deserves stewardship: "
            f"{validation.selected_objective.title}"
        )
    if validation.blocked_objectives:
        return f"portfolio blocked objective needs review: {validation.blocked_objectives[0].title}"
    if any(
        "objectives may need review" in hint for hint in validation.stale_duplicate_superseded_hints
    ):
        return (
            "portfolio stale objective hint needs review: "
            f"{validation.stale_duplicate_superseded_hints[0]}"
        )
    if recent_records and any("follow-up" in item for item in recent_records[0].steward_summary):
        return "recent stewardship created follow-up work; inspect portfolio again"
    if mandate.strip():
        return "no meaningful portfolio work selected; sleep"
    return "resident portfolio state is empty"


def _direct_portfolio_wake_action(validation: Any) -> WakefulPortfolioStewardActionKind | None:
    if validation.issues or validation.warnings:
        return None
    if validation.operator_needed_objectives and not validation.eligible_objectives:
        return WakefulPortfolioStewardActionKind.ASK_OPERATOR
    if validation.selected_objective is not None:
        return None
    if validation.blocked_objectives:
        return None
    return WakefulPortfolioStewardActionKind.SLEEP


def _direct_portfolio_wake_reason(
    action: WakefulPortfolioStewardActionKind,
    validation: Any,
) -> str:
    if action == WakefulPortfolioStewardActionKind.ASK_OPERATOR:
        questions = _operator_questions_from_validation(validation)
        return questions[0] if questions else "operator input is needed before stewardship"
    if action == WakefulPortfolioStewardActionKind.SLEEP:
        return "no meaningful portfolio work selected"
    return "wakeful portfolio stewardship stopped"


def _operator_questions_from_validation(validation: Any) -> tuple[str, ...]:
    return tuple(
        item.title for item in validation.operator_needed_objectives if str(item.title).strip()
    )


def _final_wakeful_steward_action(
    record: WakefulPortfolioStewardRecord,
) -> tuple[WakefulPortfolioStewardActionKind, str]:
    if record.action_taken != WakefulPortfolioStewardActionKind.RUN_STEWARD:
        return record.action_taken, record.final_suggested_next_action
    if record.steward_final_action == "ask_operator":
        return WakefulPortfolioStewardActionKind.ASK_OPERATOR, record.final_suggested_next_action
    if record.steward_final_action == "sleep":
        return WakefulPortfolioStewardActionKind.SLEEP, record.final_suggested_next_action
    if record.steward_final_action == "stop":
        if any("repair" in item or "advance" in item for item in record.steward_summary):
            return WakefulPortfolioStewardActionKind.RUN_STEWARD, record.final_suggested_next_action
        return WakefulPortfolioStewardActionKind.STOP, record.final_suggested_next_action
    return WakefulPortfolioStewardActionKind.RUN_STEWARD, record.final_suggested_next_action


def _steward_summary(steward_run: Any) -> tuple[str, ...]:
    summary: list[str] = []
    for report in steward_run.passes:
        summary.append(
            "pass "
            f"{report.pass_number}: {report.action_taken.value}; "
            f"before={report.validation_before.verdict}; after={report.validation_after.verdict}; "
            f"repairs={len(report.repairs_attempted)}; skipped={len(report.repairs_skipped)}; "
            f"follow-ups={len(report.new_follow_up_objectives)}"
        )
    return tuple(summary)


def _runtime_decision_after_expert_run(
    expert_run: ResidentDomainExpertRun,
    *,
    snapshot: ResidentBudgetSnapshot,
    budget: ResidentRunBudget,
    config: WakefulResidentConfig,
) -> tuple[WakefulResidentDecisionKind, str]:
    final = expert_run.final_decision
    if final is not None and final.kind == ExpertLoopDecisionKind.ASK_OPERATOR:
        return WakefulResidentDecisionKind.ASK_OPERATOR, final.reason

    next_budget = budget.can_continue()
    if not next_budget.allowed:
        return WakefulResidentDecisionKind.STOP, next_budget.reason

    if _expert_run_is_idle(expert_run) and config.sleep_when_idle:
        return WakefulResidentDecisionKind.SLEEP, "no useful resident work selected this cycle"

    return (
        WakefulResidentDecisionKind.CONTINUE,
        f"cycle {snapshot.turns_used} completed and wake budget remains",
    )


def _expert_run_is_idle(expert_run: ResidentDomainExpertRun) -> bool:
    final = expert_run.final_decision
    no_execution = not expert_run.execution_results
    no_artifacts = not expert_run.artifacts
    slept = final is not None and final.kind == ExpertLoopDecisionKind.SLEEP
    return no_execution and no_artifacts and slept


async def _list_workstreams(
    expert_memory: ResidentDomainExpertMemoryPort,
    model: ResidentDomainModel | None,
) -> list[ResidentWorkstream]:
    if model is None:
        return []
    return await expert_memory.list_workstreams(_DOMAIN_MODEL_REF)


def _operator_gated_workstream(
    workstreams: list[ResidentWorkstream] | tuple[ResidentWorkstream, ...],
) -> ResidentWorkstream | None:
    for workstream in workstreams:
        if workstream.status == ResidentWorkstreamStatus.NEEDS_OPERATOR.value:
            return workstream
    return None


def _actionable_workstreams(
    workstreams: tuple[ResidentWorkstream, ...],
) -> tuple[ResidentWorkstream, ...]:
    statuses = {
        ResidentWorkstreamStatus.PROPOSED.value,
        ResidentWorkstreamStatus.ACTIVE.value,
        ResidentWorkstreamStatus.PAUSED.value,
    }
    return tuple(workstream for workstream in workstreams if workstream.status in statuses)


def _build_operator_needed_record(
    *,
    cycle_number: int,
    mandate: str,
    prior_ref: str,
    attention_reason: str,
    workstream: ResidentWorkstream,
    budget: ResidentBudgetSnapshot,
    runtime_audit: tuple[str, ...],
) -> WakefulResidentCycleRecord:
    return WakefulResidentCycleRecord(
        cycle_number=cycle_number,
        mandate=mandate,
        prior_domain_model_ref=prior_ref,
        attention_reason=attention_reason,
        selected_action=workstream.title,
        work_created_or_advanced=(f"{workstream.id}: {workstream.status}",),
        artifact_refs=(),
        finding_summaries=(),
        decision=WakefulResidentDecisionKind.ASK_OPERATOR,
        decision_reason=f"operator input required for workstream: {workstream.title}",
        budget=budget,
        runtime_audit=runtime_audit,
    )


def _build_wake_record(
    *,
    cycle_number: int,
    mandate: str,
    prior_ref: str,
    attention_reason: str,
    prior_model: ResidentDomainModel | None,
    prior_workstreams: tuple[ResidentWorkstream, ...],
    expert_run: ResidentDomainExpertRun,
    decision: WakefulResidentDecisionKind,
    decision_reason: str,
    budget: ResidentBudgetSnapshot,
    runtime_audit: tuple[str, ...],
) -> WakefulResidentCycleRecord:
    changed_work = _changed_workstreams(prior_workstreams, expert_run.workstreams)
    new_artifacts = _new_artifact_refs(prior_model, expert_run)
    findings = _finding_summaries(expert_run)
    selected_action = ""
    if expert_run.final_decision is not None and expert_run.final_decision.workstream is not None:
        selected_action = expert_run.final_decision.workstream.title
    elif changed_work:
        selected_action = changed_work[0]

    return WakefulResidentCycleRecord(
        cycle_number=cycle_number,
        mandate=mandate,
        prior_domain_model_ref=prior_ref,
        attention_reason=attention_reason,
        selected_action=selected_action or "none",
        work_created_or_advanced=changed_work,
        artifact_refs=new_artifacts,
        finding_summaries=findings,
        decision=decision,
        decision_reason=decision_reason,
        budget=budget,
        runtime_audit=runtime_audit,
    )


def _changed_workstreams(
    prior: tuple[ResidentWorkstream, ...],
    current: tuple[ResidentWorkstream, ...],
) -> tuple[str, ...]:
    prior_status = {workstream.id: workstream.status for workstream in prior}
    changed: list[str] = []
    for workstream in current:
        previous = prior_status.get(workstream.id)
        if previous is None:
            changed.append(f"{workstream.id}: created [{workstream.status}]")
        elif previous != workstream.status:
            changed.append(f"{workstream.id}: {previous} -> {workstream.status}")
    return tuple(changed)


def _new_artifact_refs(
    prior_model: ResidentDomainModel | None,
    expert_run: ResidentDomainExpertRun,
) -> tuple[str, ...]:
    prior_paths = {artifact.path for artifact in prior_model.artifacts} if prior_model else set()
    return tuple(
        artifact.path
        for artifact in expert_run.artifacts
        if artifact.path and artifact.path not in prior_paths
    )


def _finding_summaries(expert_run: ResidentDomainExpertRun) -> tuple[str, ...]:
    findings: list[str] = []
    for result in expert_run.execution_results:
        if result.summary:
            findings.append(_compact_line(result.summary, limit=220))
    if not findings:
        findings.extend(expert_run.domain_model.recent_outcomes[-3:])
    return tuple(findings[:5])


def _render_wake_record(record: WakefulResidentCycleRecord) -> str:
    return (
        f"# Wake Cycle {record.cycle_number}\n\n"
        f"- cycle_number: {record.cycle_number}\n"
        f"- created_at: {record.created_at.isoformat()}\n"
        f"- decision: {record.decision.value}\n"
        f"- decision_reason: {record.decision_reason}\n"
        f"- prior_domain_model_ref: {record.prior_domain_model_ref}\n"
        f"- selected_action: {record.selected_action}\n"
        f"- budget_turns_used: {record.budget.turns_used}\n"
        f"- budget_input_tokens: {record.budget.usage.input_tokens}\n"
        f"- budget_output_tokens: {record.budget.usage.output_tokens}\n"
        f"- budget_total_tokens: {record.budget.total_tokens}\n\n"
        "## Mandate\n\n"
        f"{record.mandate}\n\n"
        "## Attention Reason\n\n"
        f"{record.attention_reason}\n\n"
        f"## Work Created Or Advanced\n\n{_render_list(record.work_created_or_advanced)}\n\n"
        f"## Artifact References\n\n{_render_list(record.artifact_refs)}\n\n"
        f"## Findings\n\n{_render_list(record.finding_summaries)}\n\n"
        f"## Runtime Audit\n\n{_render_list(record.runtime_audit)}\n"
    )


def _parse_wake_record(content: str, *, mandate: str) -> WakefulResidentCycleRecord | None:
    metadata = _metadata(content)
    cycle = _int_value(metadata.get("cycle_number"))
    if cycle <= 0:
        return None
    decision = _decision_value(metadata.get("decision"))
    usage = TokenUsage(
        input_tokens=_int_value(metadata.get("budget_input_tokens")),
        output_tokens=_int_value(metadata.get("budget_output_tokens")),
    )
    return WakefulResidentCycleRecord(
        cycle_number=cycle,
        mandate=_section(content, "Mandate") or mandate,
        prior_domain_model_ref=metadata.get("prior_domain_model_ref", ""),
        attention_reason=_section(content, "Attention Reason"),
        selected_action=metadata.get("selected_action", ""),
        work_created_or_advanced=tuple(_section_items(content, "Work Created Or Advanced")),
        artifact_refs=tuple(_section_items(content, "Artifact References")),
        finding_summaries=tuple(_section_items(content, "Findings")),
        decision=decision,
        decision_reason=metadata.get("decision_reason", ""),
        budget=ResidentBudgetSnapshot(
            turns_used=_int_value(metadata.get("budget_turns_used")),
            usage=usage,
        ),
        runtime_audit=tuple(_section_items(content, "Runtime Audit")),
    )


def _render_portfolio_steward_record(record: WakefulPortfolioStewardRecord) -> str:
    selected = _selected_preview_text(record.selected_objective)
    return (
        f"# Wakeful Portfolio Steward {record.wake_number}\n\n"
        f"- wake_number: {record.wake_number}\n"
        f"- created_at: {record.created_at.isoformat()}\n"
        f"- action_taken: {record.action_taken.value}\n"
        f"- validation_verdict: {record.validation_verdict}\n"
        f"- selected_objective_id: {selected[0]}\n"
        f"- selected_objective_title: {selected[1]}\n"
        f"- selected_objective_status: {selected[2]}\n"
        f"- selected_objective_priority_score: {selected[3]}\n"
        f"- steward_pass_count: {record.steward_pass_count}\n"
        f"- steward_final_action: {record.steward_final_action}\n"
        f"- budget_turns_used: {record.budget.turns_used}\n"
        f"- budget_input_tokens: {record.budget.usage.input_tokens}\n"
        f"- budget_output_tokens: {record.budget.usage.output_tokens}\n"
        f"- budget_total_tokens: {record.budget.total_tokens}\n\n"
        "## Mandate\n\n"
        f"{record.mandate}\n\n"
        "## Attention Reason\n\n"
        f"{record.attention_reason}\n\n"
        f"## Steward Summary\n\n{_render_list(record.steward_summary)}\n\n"
        f"## Operator Questions\n\n{_render_list(record.operator_questions)}\n\n"
        f"## Persisted Refs\n\n{_render_list(record.persisted_refs)}\n\n"
        "## Final Suggested Next Action\n\n"
        f"{record.final_suggested_next_action}\n"
    )


def _parse_portfolio_steward_record(
    content: str,
    *,
    mandate: str,
) -> WakefulPortfolioStewardRecord | None:
    metadata = _metadata(content)
    wake_number = _int_value(metadata.get("wake_number"))
    if wake_number <= 0:
        return None
    usage = TokenUsage(
        input_tokens=_int_value(metadata.get("budget_input_tokens")),
        output_tokens=_int_value(metadata.get("budget_output_tokens")),
    )
    return WakefulPortfolioStewardRecord(
        wake_number=wake_number,
        mandate=_section(content, "Mandate") or mandate,
        attention_reason=_section(content, "Attention Reason"),
        validation_verdict=metadata.get("validation_verdict", ""),
        selected_objective=_parse_selected_preview(metadata),
        action_taken=_portfolio_steward_action_value(metadata.get("action_taken")),
        steward_pass_count=_int_value(metadata.get("steward_pass_count")),
        steward_final_action=metadata.get("steward_final_action", ""),
        steward_summary=tuple(_section_items(content, "Steward Summary")),
        operator_questions=tuple(_section_items(content, "Operator Questions")),
        persisted_refs=tuple(_section_items(content, "Persisted Refs")),
        budget=ResidentBudgetSnapshot(
            turns_used=_int_value(metadata.get("budget_turns_used")),
            usage=usage,
        ),
        final_suggested_next_action=_section(content, "Final Suggested Next Action"),
    )


def render_wakeful_portfolio_steward_report(run: WakefulPortfolioStewardRun) -> str:
    blocks = []
    for record in run.records:
        selected = (
            f"{record.selected_objective.objective_id}: {record.selected_objective.title}"
            if record.selected_objective is not None
            else "none"
        )
        blocks.append(
            f"## Wake {record.wake_number}\n\n"
            f"- attention_reason: {record.attention_reason}\n"
            f"- validation_verdict: {record.validation_verdict}\n"
            f"- selected_objective: {selected}\n"
            f"- action_taken: {record.action_taken.value}\n"
            f"- steward_pass_count: {record.steward_pass_count}\n"
            f"- steward_final_action: {record.steward_final_action or 'none'}\n"
            f"- budget_turns: {record.budget.turns_used}\n"
            f"- final_suggested_next_action: {record.final_suggested_next_action}\n\n"
            f"### Steward Summary\n\n{_render_list(record.steward_summary)}\n\n"
            f"### Operator Questions\n\n{_render_list(record.operator_questions)}\n\n"
            f"### Persisted Refs\n\n{_render_list(record.persisted_refs)}"
        )
    return (
        "# Wakeful Portfolio Steward Report\n\n"
        f"- final_action: {run.final_action.value}\n"
        f"- final_reason: {run.final_reason}\n"
        f"- total_wake_passes: {len(run.records)}\n"
        f"- budget_turns: {run.budget.turns_used}\n\n" + "\n\n".join(blocks)
    )


def _selected_preview_text(
    preview: ResidentObjectiveDryRunPreview | None,
) -> tuple[str, str, str, int]:
    if preview is None:
        return "", "", "", 0
    return (
        preview.objective_id,
        preview.title,
        preview.status,
        preview.priority_score,
    )


def _parse_selected_preview(metadata: dict[str, str]) -> ResidentObjectiveDryRunPreview | None:
    objective_id = metadata.get("selected_objective_id", "")
    if not objective_id:
        return None
    return ResidentObjectiveDryRunPreview(
        objective_id=objective_id,
        title=metadata.get("selected_objective_title", ""),
        status=metadata.get("selected_objective_status", ""),
        priority_score=_int_value(metadata.get("selected_objective_priority_score")),
        priority_rationale="",
        dependency_readiness="unknown",
        budget_notes="unknown",
        risk_notes="unknown",
    )


def _portfolio_steward_action_value(value: str | None) -> WakefulPortfolioStewardActionKind:
    try:
        return WakefulPortfolioStewardActionKind(str(value or "stop"))
    except ValueError:
        return WakefulPortfolioStewardActionKind.STOP


def _decision_value(value: str | None) -> WakefulResidentDecisionKind:
    try:
        return WakefulResidentDecisionKind(str(value or "stop"))
    except ValueError:
        return WakefulResidentDecisionKind.STOP


def _metadata(content: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in content.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        key, value = line[2:].split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _section(content: str, name: str) -> str:
    lines = _section_lines(content, name)
    return "\n".join(line for line in lines if not line.startswith("- ")).strip()


def _section_items(content: str, name: str) -> list[str]:
    items: list[str] = []
    for line in _section_lines(content, name):
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value != "none":
                items.append(value)
    return items


def _section_lines(content: str, name: str) -> list[str]:
    wanted = f"## {name}".casefold()
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip().casefold() == wanted:
            start = idx + 1
            break
    if start < 0:
        return []
    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.strip():
            collected.append(line)
    return collected


def _render_list(items: Any) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    if not values:
        return "- none"
    return "\n".join(f"- {item}" for item in values)


def _int_value(value: str | None) -> int:
    try:
        return int(str(value or "0"))
    except ValueError:
        return 0
