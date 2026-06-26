from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ravn.domain.resident_portfolio import (
    ResidentDelegationRecord,
    ResidentDelegationStatus,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkerBrief,
)
from ravn.resident_expert import LocalResidentDomainExpertMemory
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    ResidentDelegationConfig,
    ResidentDelegationRuntime,
    build_worker_brief,
    select_delegation_candidates,
)
from scripts.prove_resident_delegation import (
    _cancelled_real_delegation_records,
    _observed_real_results,
    _real_delegation_records,
    _sample_delegation_result_pair,
    _successful_real_results,
    _successful_real_source_objective_ids,
)

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.briefs: list[ResidentWorkerBrief] = []
        self.sessions: dict[str, ResidentExecutionSession] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        self.briefs.append(brief)
        session = ResidentExecutionSession(
            session_id=f"session-{brief.objective_id}",
            status=ResidentDelegationStatus.COMPLETED.value,
            backend_name="recording",
            summary="completed",
        )
        self.sessions[session.session_id] = session
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return self.sessions[session_id]

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return ResidentExecutionResult(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            summary=f"Result for {session_id}",
            output_refs=(f"outputs/{session_id}.md",),
            findings=(f"finding from {session_id}",),
            follow_up_suggestions=(f"review output from {session_id}",),
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="recording",
            summary=reason,
        )


class StaleDelegationExecutor:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []
        self.launched: list[ResidentWorkerBrief] = []

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        self.launched.append(brief)
        return ResidentExecutionSession(
            session_id=f"unexpected-{brief.objective_id}",
            status=ResidentDelegationStatus.RUNNING.value,
            backend_name="recording",
            summary="unexpected launch",
        )

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.RUNNING.value,
            backend_name="recording",
            summary="still running",
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return None

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        self.cancelled.append((session_id, reason))
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="recording",
            summary=reason,
        )


class ResultExecutor:
    def __init__(self, result: ResidentExecutionResult) -> None:
        self.result = result
        self.briefs: list[ResidentWorkerBrief] = []
        self.sessions: dict[str, ResidentExecutionSession] = {}

    async def launch(self, brief: ResidentWorkerBrief) -> ResidentExecutionSession:
        self.briefs.append(brief)
        session = ResidentExecutionSession(
            session_id=self.result.session_id,
            status=self.result.status,
            backend_name="recording",
            summary=self.result.summary,
        )
        self.sessions[session.session_id] = session
        return session

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return self.sessions[session_id]

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        assert session_id == self.result.session_id
        return self.result

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="recording",
            summary=reason,
        )


def _objective(
    objective_id: str,
    title: str,
    *,
    status: str = ResidentObjectiveStatus.CANDIDATE.value,
    risk_boundaries: tuple[str, ...] = (),
    dependencies: tuple[str, ...] = (),
    priority_score: int = 0,
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A bounded worker result exists.",
        proof_criteria=("A bounded worker result exists.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        dependencies=dependencies,
        risk_boundaries=risk_boundaries,
        status=status,
        priority_score=priority_score,
        source_evidence=(f"evidence for {title}",),
        reasoning="ready for delegated bounded work",
    )


async def _write_portfolio(
    backend: LocalResidentWorkItemBackend,
    *objectives: ResidentObjective,
) -> None:
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE, objectives=objectives))


def test_delegation_selection_respects_dependencies_and_existing_records() -> None:
    done = _objective(
        "done",
        "Completed dependency",
        status=ResidentObjectiveStatus.COMPLETED.value,
    )
    ready = _objective("ready", "Ready objective", dependencies=("done",))
    blocked = _objective("blocked", "Blocked objective", dependencies=("missing",))
    existing = ResidentDelegationRecord(
        id="delegation-ready",
        source_objective_id="ready",
        backend_session_id="session-ready",
        backend_name="recording",
        brief=build_worker_brief(MANDATE, ready),
        status=ResidentDelegationStatus.RUNNING.value,
        reason="already running",
    )

    selected, gated = select_delegation_candidates(
        (done, ready, blocked),
        delegations=(existing,),
        mandate=MANDATE,
        max_selected=3,
    )

    assert selected == ()
    assert gated == ()


def test_worker_brief_is_generated_from_objective_context() -> None:
    objective = _objective(
        "brief",
        "Review generic evidence",
        risk_boundaries=("external_side_effect",),
    )

    brief = build_worker_brief(MANDATE, objective)

    assert brief.mandate == MANDATE
    assert brief.objective_id == "brief"
    assert brief.desired_outcome == objective.expected_outcome
    assert brief.proof_criteria == objective.proof_criteria
    assert "evidence for Review generic evidence" in brief.evidence
    assert "external_side_effect" in brief.risk_boundaries


@pytest.mark.asyncio
async def test_delegation_uses_backend_agnostic_execution_port(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = RecordingExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
    ).run(MANDATE)

    assert len(executor.briefs) == 1
    assert report.created_delegations[0].backend_name == "recording"
    assert report.observed_results


@pytest.mark.asyncio
async def test_multiple_delegated_workstreams_can_exist_at_once(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("one", "First delegated objective"),
        _objective("two", "Second delegated objective"),
        _objective("three", "Third delegated objective"),
    )
    executor = RecordingExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=2),
    ).run(MANDATE)

    assert len(report.created_delegations) == 2
    assert len(executor.briefs) == 2


@pytest.mark.asyncio
async def test_active_delegation_consumes_launch_capacity(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    active = _objective("active", "Already delegated objective")
    next_objective = _objective("next", "Next delegated objective")
    await _write_portfolio(backend, active, next_objective)
    existing = ResidentDelegationRecord(
        id="delegation-active",
        source_objective_id=active.id,
        backend_session_id="session-active",
        backend_name="recording",
        brief=build_worker_brief(MANDATE, active),
        status=ResidentDelegationStatus.RUNNING.value,
        reason="already delegated",
    )
    await backend.write_delegation(existing)
    executor = StaleDelegationExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=1),
    ).run(MANDATE)

    assert report.created_delegations == ()
    assert executor.launched == []
    assert report.observed_results == ()


@pytest.mark.asyncio
async def test_running_delegation_snapshot_remains_observable_without_completion(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-running",
            status=ResidentDelegationStatus.RUNNING.value,
            summary="workflow artifact snapshot while worker is still running",
            output_refs=("research/campaigns/proof/plan.md",),
            findings=("terminal worker result still pending",),
        )
    )

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
    ).run(MANDATE)
    delegations = {item.id: item for item in await backend.list_delegations(MANDATE)}
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert report.observed_results == ()
    assert delegations["delegation-one"].status == ResidentDelegationStatus.RUNNING.value
    assert delegations["delegation-one"].result_refs == ()
    assert objectives["one"].status == ResidentObjectiveStatus.ACTIVE.value
    assert not any(
        ref.startswith("resident/delegation-results/") for ref in report.persisted_refs
    )


@pytest.mark.asyncio
async def test_duplicate_active_delegations_are_reconciled_without_new_launch(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    objective = _objective("duplicate-source", "Investigate duplicate worker sessions")
    await _write_portfolio(backend, objective)
    keep = ResidentDelegationRecord(
        id="delegation-duplicate-source-a",
        source_objective_id=objective.id,
        backend_session_id="session-keep",
        backend_name="recording",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.RUNNING.value,
        reason="first active worker",
        created_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    duplicate = ResidentDelegationRecord(
        id="delegation-duplicate-source-b",
        source_objective_id=objective.id,
        backend_session_id="session-duplicate",
        backend_name="recording",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.LAUNCHED.value,
        reason="duplicate active worker",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    await backend.write_delegation(keep)
    await backend.write_delegation(duplicate)
    executor = StaleDelegationExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=1),
    ).run(MANDATE)

    delegations = {item.id: item for item in await backend.list_delegations(MANDATE)}
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}
    decisions = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "resident/portfolio/decisions").glob("*.md"))
    )
    assert report.created_delegations == ()
    assert executor.launched == []
    assert [item[0] for item in executor.cancelled] == ["session-duplicate"]
    assert delegations[keep.id].status == ResidentDelegationStatus.RUNNING.value
    assert delegations[duplicate.id].status == ResidentDelegationStatus.CANCELLED.value
    assert any(
        "duplicate delegation delegation-duplicate-source-b cancelled" in item
        for item in objectives[objective.id].proof_progress
    )
    assert "duplicates_reconciled=1" in decisions


@pytest.mark.asyncio
async def test_risky_work_creates_operator_objective_instead_of_launching(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("safe", "Safe delegated objective"),
        _objective("risky", "Risky delegated objective", risk_boundaries=("spending",)),
    )
    executor = RecordingExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=2),
    ).run(MANDATE)
    objectives = await backend.list_objectives(MANDATE)

    assert len(executor.briefs) == 1
    assert report.operator_questions
    assert any(item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value for item in objectives)


@pytest.mark.asyncio
async def test_risky_work_does_not_repeat_existing_operator_question(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(
        backend,
        _objective("risky", "Risky delegated objective", risk_boundaries=("spending",)),
    )
    runtime = ResidentDelegationRuntime(backend=backend, executor=RecordingExecutor())

    first = await runtime.run(MANDATE)
    second = await runtime.run(MANDATE)

    assert first.operator_questions
    assert second.operator_questions == ()


@pytest.mark.asyncio
async def test_delegation_records_persist_and_reload(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))

    await ResidentDelegationRuntime(
        backend=backend,
        executor=RecordingExecutor(),
    ).run(MANDATE)
    restored = await backend.list_delegations(MANDATE)

    assert len(restored) == 1
    assert restored[0].source_objective_id == "one"
    assert restored[0].brief.objective_title == "First delegated objective"
    assert restored[0].result_refs


@pytest.mark.asyncio
async def test_results_merge_back_into_portfolio_and_create_followups(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=RecordingExecutor(),
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert objectives["one"].status == ResidentObjectiveStatus.COMPLETED.value
    assert objectives["one"].artifact_links
    assert report.created_follow_up_objectives
    assert any("Follow up delegated result" in item.title for item in objectives.values())


@pytest.mark.asyncio
async def test_delegation_review_consolidates_into_domain_expert_memory(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=RecordingExecutor(),
        expert_memory=expert_memory,
    ).run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert model is not None
    assert "finding from session-one" in model.known_facts
    assert any(
        "reviewed delegation delegation-one: complete" in item
        for item in model.resident_decisions
    )
    assert any(item.path.startswith("resident/delegation-reviews/") for item in model.artifacts)
    assert any(
        ref.startswith("resident/domain-expert/consolidations/")
        for ref in report.persisted_refs
    )
    assert any(
        ref.startswith("resident/domain-expert/consolidations/")
        for ref in objectives["one"].consolidation_links
    )


@pytest.mark.asyncio
async def test_completed_workflow_delegation_does_not_create_capability_gap(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-workflow",
            status=ResidentDelegationStatus.COMPLETED.value,
            summary="Local workflow artifact research/campaigns/proof/plan.md",
            output_refs=("research/campaigns/proof/plan.md",),
            findings=("workflow completed with durable artifact output",),
            follow_up_suggestions=("Review workflow output for session session-workflow",),
        )
    )

    await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        expert_memory=expert_memory,
    ).run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)

    assert model is not None
    assert model.capability_gaps == ()


@pytest.mark.asyncio
async def test_resident_abandons_stale_delegation_without_relaunching(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    objective = _objective("stale", "Stale delegated objective")
    await _write_portfolio(backend, objective)
    stale_record = ResidentDelegationRecord(
        id="delegation-stale",
        source_objective_id=objective.id,
        backend_session_id="session-stale",
        backend_name="recording",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.RUNNING.value,
        reason="resident launched this previously",
        updated_at=datetime.now(UTC) - timedelta(hours=2),
    )
    await backend.write_delegation(stale_record)
    executor = StaleDelegationExecutor()

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_delegations=1, abandon_after_seconds=1),
    ).run(MANDATE)

    delegations = {item.id: item for item in await backend.list_delegations(MANDATE)}
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert len(executor.cancelled) == 1
    assert executor.cancelled[0][0] == "session-stale"
    assert "abandoning stale delegated session" in executor.cancelled[0][1]
    assert executor.launched == []
    assert report.created_delegations == ()
    assert delegations["delegation-stale"].status == ResidentDelegationStatus.CANCELLED.value
    assert objectives["stale"].status == ResidentObjectiveStatus.BLOCKED.value
    assert any(
        "delegation delegation-stale abandoned" in item
        for item in objectives["stale"].proof_progress
    )
    assert any(item.id == "stale" for item in report.updated_objectives)


@pytest.mark.asyncio
async def test_failed_delegation_creates_actionable_retry_objective(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-failed",
            status=ResidentDelegationStatus.FAILED.value,
            summary="worker failed before producing evidence",
        )
    )

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_retry_follow_up_depth=1),
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}
    retry = objectives["retry-delegated-result-first-delegated-objective"]

    assert report.reviews[0].decision == "retry"
    assert objectives["one"].status == ResidentObjectiveStatus.PAUSED.value
    assert retry.status == ResidentObjectiveStatus.CANDIDATE.value
    assert retry.dependencies == ()
    assert retry.supersedes == ("one",)
    selected, gated = select_delegation_candidates(
        tuple(objectives.values()),
        delegations=tuple(await backend.list_delegations(MANDATE)),
        mandate=MANDATE,
        max_selected=1,
    )
    assert gated == ()
    assert selected[0].id == retry.id


@pytest.mark.asyncio
async def test_blocked_delegation_consolidates_failure_without_fact_pollution(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    expert_memory = LocalResidentDomainExpertMemory(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-blocked",
            status=ResidentDelegationStatus.FAILED.value,
            summary="workflow adapter unavailable",
            findings=("stderr is not a domain fact",),
            blocked_reason="workflow adapter unavailable",
        )
    )

    await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        expert_memory=expert_memory,
    ).run(MANDATE)
    model = await expert_memory.read_domain_model(MANDATE)

    assert model is not None
    assert "stderr is not a domain fact" not in model.known_facts
    assert any("delegation delegation-one failed" in item for item in model.failure_notes)
    assert "workflow adapter unavailable" in model.capability_gaps


@pytest.mark.asyncio
async def test_operator_blocked_delegation_creates_needs_operator_objective(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-source-needed",
            status=ResidentDelegationStatus.BLOCKED.value,
            summary="source-backed work cannot continue yet",
            blocked_reason=(
                "Provide a public/sanitized product source or explicit approval "
                "before continuing the source-backed snapshot."
            ),
        )
    )

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}
    follow_up = next(
        item
        for item in objectives.values()
        if item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
    )

    assert report.created_follow_up_objectives
    assert follow_up.kind == ResidentObjectiveKind.OPERATOR_QUESTION.value
    assert "Provide a public/sanitized product source" in follow_up.pending_question
    assert follow_up.dependencies == ()


@pytest.mark.asyncio
async def test_incomplete_delegation_creates_actionable_evidence_work(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("one", "First delegated objective"))
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-incomplete",
            status=ResidentDelegationStatus.COMPLETED.value,
            summary="worker reported completion but no evidence",
        )
    )

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}
    evidence = objectives["gather-delegated-evidence-first-delegated-objective"]

    assert report.reviews[0].decision == "needs_follow_up"
    assert objectives["one"].status == ResidentObjectiveStatus.PAUSED.value
    assert evidence.dependencies == ()
    selected, gated = select_delegation_candidates(
        tuple(objectives.values()),
        delegations=tuple(await backend.list_delegations(MANDATE)),
        mandate=MANDATE,
        max_selected=1,
    )
    assert gated == ()
    assert selected[0].id == evidence.id


@pytest.mark.asyncio
async def test_repeated_failed_delegation_creates_review_instead_of_retry_loop(
    tmp_path: Path,
) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    retry_source = _objective(
        "retry-delegated-result-first-delegated-objective",
        "Retry delegated result: First delegated objective",
    ).with_updates(supersedes=("one",))
    await _write_portfolio(backend, retry_source)
    executor = ResultExecutor(
        ResidentExecutionResult(
            session_id="session-failed-again",
            status=ResidentDelegationStatus.FAILED.value,
            summary="retry failed before producing evidence",
        )
    )

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=executor,
        config=ResidentDelegationConfig(max_retry_follow_up_depth=1),
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert report.reviews[0].decision == "retry"
    repeated_retry_id = (
        "retry-delegated-result-retry-delegated-result-first-delegated-objective"
    )
    assert repeated_retry_id not in objectives
    assert objectives[retry_source.id].superseded_by
    expected_title = (
        "Review repeated delegation failure: Retry delegated result: "
        "First delegated objective"
    )
    assert any(
        item.title == expected_title
        for item in objectives.values()
    )


def test_resident_delegation_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/resident_portfolio.py").read_text(encoding="utf-8").casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "prd",
        "srd",
        "forge",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source


def test_delegation_proof_helpers_match_results_to_real_sessions() -> None:
    objective = _objective("one", "First delegated objective")
    local = ResidentDelegationRecord(
        id="delegation-local",
        source_objective_id=objective.id,
        backend_session_id="local-session",
        backend_name="local-subprocess",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.COMPLETED.value,
        reason="historical local proof",
    )
    real = ResidentDelegationRecord(
        id="delegation-real",
        source_objective_id=objective.id,
        backend_session_id="real-session",
        backend_name="workflow",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.COMPLETED.value,
        reason="real worker proof",
    )
    cancelled = ResidentDelegationRecord(
        id="delegation-cancelled",
        source_objective_id=objective.id,
        backend_session_id="cancelled-session",
        backend_name="workflow",
        brief=build_worker_brief(MANDATE, objective),
        status=ResidentDelegationStatus.CANCELLED.value,
        reason="resident abandoned stale worker",
    )
    unmatched = ResidentExecutionResult(
        session_id="other-session",
        status=ResidentDelegationStatus.COMPLETED.value,
        summary="not the proof result",
    )
    failed = ResidentExecutionResult(
        session_id="real-session",
        status=ResidentDelegationStatus.FAILED.value,
        summary="failed launch metadata is not proof",
    )
    running = ResidentExecutionResult(
        session_id="real-session",
        status=ResidentDelegationStatus.RUNNING.value,
        summary="running artifact snapshot is not terminal proof",
        output_refs=("forge/sessions/real-session/partial.md",),
    )
    matched = ResidentExecutionResult(
        session_id="real-session",
        status=ResidentDelegationStatus.COMPLETED.value,
        summary="real proof result",
        output_refs=("forge/sessions/real-session/conversation.md",),
    )

    real_records = _real_delegation_records([local, real, cancelled])
    observed = [unmatched, failed, running, matched]
    real_results = _observed_real_results(real_records, observed)
    successful_results = _successful_real_results(real_records, observed)
    sample = _sample_delegation_result_pair([local, real, cancelled], [unmatched, matched])

    assert real_records == [real, cancelled]
    assert _cancelled_real_delegation_records([local, real, cancelled]) == [cancelled]
    assert real_results == [failed, running, matched]
    assert successful_results == [matched]
    assert _successful_real_source_objective_ids(real_records, observed) == {objective.id}
    assert sample == (real, matched)
