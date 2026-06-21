from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ravn.domain.capability_catalog import WorkflowCapability
from ravn.domain.events import RavnEvent, RavnEventType
from ravn.domain.operator_contact import (
    OperatorContactKind,
    OperatorContactRequest,
    OperatorContactResult,
    OperatorContactStatus,
    answer_operator_contact,
    emit_help_needed_operator_contact,
)
from ravn.domain.resident_portfolio import (
    ResidentDelegationRecord,
    ResidentDelegationReviewDecision,
    ResidentDelegationStatus,
    ResidentExecutionResult,
    ResidentExecutionSession,
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
)
from ravn.ports.capability import (
    WorkflowArtifact,
    WorkflowLaunchRequest,
    WorkflowLaunchResult,
    WorkflowRunEvent,
    WorkflowRunReference,
    WorkflowRunStatus,
)
from ravn.resident_portfolio import (
    LocalResidentWorkItemBackend,
    LocalSimulatedResidentExecutor,
    LocalSubprocessResidentExecutor,
    ResidentAutonomyLoopConfig,
    ResidentAutonomyLoopRuntime,
    ResidentDelegationConfig,
    ResidentDelegationRuntime,
    WorkflowResidentExecutionAdapter,
    build_worker_brief,
    review_delegation_result,
)
from ravn.wakeful_resident import LocalWakefulResidentMemory

MANDATE = (
    "Help evolve Valkyries/Ravn into autonomous, self-improving domain experts. "
    "They should learn, plan, create work, use tools, manage execution, remember "
    "outcomes, and improve without being babysat."
)


class RecordingWorkflowPort:
    def __init__(self) -> None:
        self.launches: list[WorkflowLaunchRequest] = []

    async def list_workflows(self) -> list[WorkflowCapability]:
        return [
            WorkflowCapability(
                workflow_id="generic-worker",
                name="Generic Worker",
                description="Run bounded resident work",
                tags=["resident", "worker"],
            )
        ]

    async def launch_workflow(self, request: WorkflowLaunchRequest) -> WorkflowLaunchResult:
        self.launches.append(request)
        return WorkflowLaunchResult(
            workflow_id=request.workflow_id,
            workflow_name="Generic Worker",
            session_id="workflow-session-1",
            session_name=request.session_name,
            status="completed",
        )

    async def get_workflow_status(self, reference: WorkflowRunReference) -> WorkflowRunStatus:
        return WorkflowRunStatus(
            state="completed",
            session_id=reference.session_id or "workflow-session-1",
            workflow_id=reference.workflow_id or "generic-worker",
            terminal=True,
        )

    async def list_workflow_events(
        self,
        reference: WorkflowRunReference,
        *,
        limit: int = 100,
    ) -> list[WorkflowRunEvent]:
        return [
            WorkflowRunEvent(
                event_type="workflow.completed",
                data={"session_id": reference.session_id or "workflow-session-1"},
            )
        ][:limit]

    async def list_workflow_artifacts(
        self,
        reference: WorkflowRunReference,
    ) -> list[WorkflowArtifact]:
        return [
            WorkflowArtifact(
                path="workflow/output.md",
                title="Workflow output",
                summary="Worker produced durable output.",
            )
        ]

    async def read_workflow_artifact(
        self,
        reference: WorkflowRunReference,
        *,
        path: str,
    ) -> Any:
        raise NotImplementedError


class ThinResultExecutor:
    async def launch(self, brief: Any) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=f"thin-{brief.objective_id}",
            status=ResidentDelegationStatus.COMPLETED.value,
            backend_name="thin",
            summary="thin result",
        )

    async def read_status(self, session_id: str) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            backend_name="thin",
            summary="thin result",
        )

    async def read_result(self, session_id: str) -> ResidentExecutionResult | None:
        return ResidentExecutionResult(
            session_id=session_id,
            status=ResidentDelegationStatus.COMPLETED.value,
            summary="thin result without evidence",
        )

    async def cancel(self, session_id: str, reason: str) -> ResidentExecutionSession:
        return ResidentExecutionSession(
            session_id=session_id,
            status=ResidentDelegationStatus.CANCELLED.value,
            backend_name="thin",
            summary=reason,
        )


class PendingAskOperator:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def ask(self, request: Any) -> Any:
        self.requests.append(request)
        return OperatorContactResult(
            request=request,
            status=OperatorContactStatus.PENDING.value,
        )


class ApprovingAskOperator:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def ask(self, request: Any) -> Any:
        from datetime import UTC, datetime

        self.requests.append(request)
        return OperatorContactResult(
            request=request,
            status=OperatorContactStatus.ANSWERED.value,
            answer="Approved for this bounded proof.",
            approved=True,
            responded_at=datetime.now(UTC),
        )


class RecordingChannel:
    def __init__(self) -> None:
        self.events: list[RavnEvent] = []

    async def emit(self, event: RavnEvent) -> None:
        self.events.append(event)


def _objective(
    objective_id: str,
    title: str,
    *,
    risk_boundaries: tuple[str, ...] = (),
) -> ResidentObjective:
    return ResidentObjective(
        id=objective_id,
        title=title,
        purpose=f"Advance {title}",
        serves_mandate_because="It advances the resident mandate.",
        expected_outcome="A reviewed worker result exists.",
        proof_criteria=("A reviewed worker result exists.",),
        kind=ResidentObjectiveKind.RESEARCH.value,
        status=ResidentObjectiveStatus.CANDIDATE.value,
        risk_boundaries=risk_boundaries,
        source_evidence=(title,),
        reasoning="ready for resident autonomy loop",
    )


async def _write_portfolio(
    backend: LocalResidentWorkItemBackend,
    *objectives: ResidentObjective,
) -> None:
    for objective in objectives:
        await backend.write_objective(objective)
    await backend.write_portfolio(ResidentPortfolio(mandate=MANDATE, objectives=objectives))


@pytest.mark.asyncio
async def test_workflow_adapter_launches_real_capability_port() -> None:
    workflows = RecordingWorkflowPort()
    adapter = WorkflowResidentExecutionAdapter(workflows=workflows)
    objective = _objective("workflow-objective", "Run workflow objective")

    session = await adapter.launch(build_worker_brief(MANDATE, objective))
    result = await adapter.read_result(session.session_id)

    assert session.backend_name == "workflow"
    assert workflows.launches
    assert "Run workflow objective" in workflows.launches[0].prompt
    assert result is not None
    assert result.output_refs == ("workflow/output.md",)


@pytest.mark.asyncio
async def test_local_subprocess_executor_is_not_the_simulator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    executor = LocalSubprocessResidentExecutor()
    objective = _objective("subprocess-objective", "Run local worker objective")
    brief = build_worker_brief(MANDATE, objective)

    session = await executor.launch(brief)
    result = await executor.read_result(session.session_id)

    assert not isinstance(executor, LocalSimulatedResidentExecutor)
    assert session.backend_name == "local-subprocess"
    assert result is not None
    assert result.output_refs
    assert Path(result.output_refs[0]).exists()


def run_operator_contact_request_for_test(
    question: str,
    *,
    source_objective_id: str = "",
) -> Any:
    return OperatorContactRequest(
        id="operator-contact-test",
        question=question,
        reason="test reason",
        impact="test impact",
        source_objective_id=source_objective_id,
        risk_boundaries=("spending",) if source_objective_id else (),
        kind=OperatorContactKind.HELP_NEEDED,
    )


@pytest.mark.asyncio
async def test_delegated_results_are_reviewed_before_completion(tmp_path: Path) -> None:
    backend = LocalResidentWorkItemBackend(tmp_path)
    await _write_portfolio(backend, _objective("thin", "Thin delegated objective"))

    report = await ResidentDelegationRuntime(
        backend=backend,
        executor=ThinResultExecutor(),
        config=ResidentDelegationConfig(max_delegations=1),
    ).run(MANDATE)
    objectives = {item.id: item for item in await backend.list_objectives(MANDATE)}

    assert report.reviews[0].decision == ResidentDelegationReviewDecision.NEEDS_FOLLOW_UP.value
    assert objectives["thin"].status == ResidentObjectiveStatus.PAUSED.value


@pytest.mark.asyncio
async def test_autonomy_loop_runs_multiple_cycles_and_persists_wake_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    backend = LocalResidentWorkItemBackend(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    await _write_portfolio(
        backend,
        _objective("one", "First autonomous objective"),
        _objective("two", "Second autonomous objective"),
        _objective("risky", "Risky autonomous objective", risk_boundaries=("spending",)),
    )

    run = await ResidentAutonomyLoopRuntime(
        backend=backend,
        executor=LocalSubprocessResidentExecutor(),
        ask_operator=ApprovingAskOperator(),
        wake_memory=wake_memory,
        config=ResidentAutonomyLoopConfig(max_cycles=2, max_delegations_per_cycle=1),
    ).run(MANDATE)
    wake_records = await wake_memory.list_wake_records(MANDATE, limit=5)
    objectives = await backend.list_objectives(MANDATE)

    assert len(run.cycles) == 2
    assert all(cycle.review_decisions for cycle in run.cycles)
    assert len(wake_records) == 2
    assert any(item.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value for item in objectives)
    assert any(item.title.startswith("Follow up delegated result") for item in objectives)


@pytest.mark.asyncio
async def test_autonomy_loop_emits_operator_contact_and_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    backend = LocalResidentWorkItemBackend(tmp_path)
    wake_memory = LocalWakefulResidentMemory(tmp_path)
    ask_operator = PendingAskOperator()
    await _write_portfolio(
        backend,
        _objective("risky", "Risky autonomous objective", risk_boundaries=("spending",)),
    )

    run = await ResidentAutonomyLoopRuntime(
        backend=backend,
        executor=LocalSubprocessResidentExecutor(),
        ask_operator=ask_operator,
        wake_memory=wake_memory,
        config=ResidentAutonomyLoopConfig(max_cycles=10, max_delegations_per_cycle=1),
    ).run(MANDATE)
    contact_pages = sorted((tmp_path / "resident" / "operator-contacts").glob("*.md"))
    wake_records = await wake_memory.list_wake_records(MANDATE, limit=20)

    assert len(run.cycles) == 1
    assert len(ask_operator.requests) == 1
    assert run.operator_contacts[0].status == "pending"
    assert "Waiting for operator answer" in run.final_suggested_next_action
    assert contact_pages
    assert "status: pending" in contact_pages[0].read_text(encoding="utf-8")
    assert len(wake_records) == 1


@pytest.mark.asyncio
async def test_autonomy_loop_uses_operator_approval_for_next_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    backend = LocalResidentWorkItemBackend(tmp_path)
    ask_operator = ApprovingAskOperator()
    await _write_portfolio(
        backend,
        _objective("risky", "Risky autonomous objective", risk_boundaries=("spending",)),
    )

    run = await ResidentAutonomyLoopRuntime(
        backend=backend,
        executor=LocalSubprocessResidentExecutor(),
        ask_operator=ask_operator,
        config=ResidentAutonomyLoopConfig(max_cycles=2, max_delegations_per_cycle=1),
    ).run(MANDATE)

    assert len(ask_operator.requests) == 1
    assert run.operator_contacts[0].approved is True
    assert len(run.cycles) == 2
    assert run.cycles[1].delegation_report.created_delegations
    assert run.cycles[1].delegation_report.created_delegations[0].source_objective_id == "risky"


@pytest.mark.asyncio
async def test_headless_operator_contact_emits_existing_help_needed_event() -> None:
    channel = RecordingChannel()
    operator_request = run_operator_contact_request_for_test(
        "May I proceed?",
        source_objective_id="risky",
    )

    result = await emit_help_needed_operator_contact(
        channel,
        operator_request,
        source="resident-proof",
        persona="proof-ravn",
        session_id="session-1",
    )

    assert result.status == "pending"
    assert result.emitted_ref == operator_request.id
    assert len(channel.events) == 1
    event = channel.events[0]
    assert event.type == RavnEventType.HELP_NEEDED
    assert event.source == "resident-proof"
    assert event.session_id == "session-1"
    assert event.correlation_id == operator_request.id
    assert event.payload["persona"] == "proof-ravn"
    assert event.payload["summary"] == operator_request.question
    assert event.payload["context"]["operator_contact_id"] == operator_request.id


@pytest.mark.asyncio
async def test_interactive_operator_contact_awaits_ask_user_answer() -> None:
    async def ask_user(question: str) -> str:
        assert question == "May I proceed?"
        return "yes, proceed with this one"

    result = await answer_operator_contact(
        run_operator_contact_request_for_test("May I proceed?"),
        ask_user,
        approval_decider=lambda answer: "yes" in answer,
    )

    assert result.status == "answered"
    assert result.answer == "yes, proceed with this one"
    assert result.approved is True


def test_review_helper_can_complete_when_evidence_is_present() -> None:
    source = _objective("reviewed", "Reviewed objective")
    result = ResidentExecutionResult(
        session_id="session-reviewed",
        status=ResidentDelegationStatus.COMPLETED.value,
        summary="useful result",
        output_refs=("result.md",),
    )
    delegation = ResidentDelegationRecord(
        id="delegation-reviewed",
        source_objective_id=source.id,
        backend_session_id="session-reviewed",
        backend_name="test",
        brief=build_worker_brief(MANDATE, source),
        status=ResidentDelegationStatus.COMPLETED.value,
        reason="test",
    )

    review = review_delegation_result(source, delegation, result)

    assert review.decision == ResidentDelegationReviewDecision.COMPLETE.value


def test_resident_autonomy_loop_contains_no_domain_specific_playbook_terms() -> None:
    source = Path("src/ravn/resident_portfolio.py").read_text(encoding="utf-8").casefold()

    for forbidden in (
        "kanuck",
        "inventory",
        "3d printing",
        "prd",
        "srd",
        "forge",
        "codex",
        "blender",
        "slicing",
        "product catalog",
    ):
        assert forbidden not in source
