"""Domain-neutral proof for Ting's reusable expanded-workflow lifecycle."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import pytest

from ting.domain.services.workflow_execution import WorkflowExecutionLifecycle
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ChildTemplate,
    ExecutionBudget,
    ExpansionPolicy,
    WorkflowChildExecution,
    WorkflowChildProposal,
    WorkflowExecution,
    WorkflowExecutionError,
    calculate_join,
    digest_json,
    make_children,
    make_retry,
    resolve_child_template,
    validate_expansion,
)
from ting.ports.workflow_execution import WorkflowExecutionRepository


def _digest(value: str) -> str:
    return "sha256:" + sha256(value.encode()).hexdigest()


def _editorial_execution(*, templates: dict[str, ChildTemplate] | None = None) -> WorkflowExecution:
    now = datetime.now(UTC)
    schema = {
        "type": "object",
        "properties": {
            "articleId": {"type": "string", "minLength": 1},
            "locale": {"type": "string", "minLength": 1},
        },
        "required": ["articleId", "locale"],
        "additionalProperties": False,
    }
    return WorkflowExecution(
        id=uuid4(),
        name="Publish translated handbook",
        prompt="Translate, fact-check, and publish the handbook",
        owner_id="editor-1",
        tenant_id="publishing",
        workflow_id=uuid4(),
        workflow_revision="2026-09-19",
        workflow_digest=_digest("editorial-workflow"),
        parent_session_id="editorial-session-1",
        parent_node_id="commission-translations",
        connection_id="content-platform",
        policy=ExpansionPolicy(
            coordinator_id="editorial-coordinator",
            templates=templates
            or {
                "translation": ChildTemplate(
                    dependency_alias="translation-assignment",
                    id=uuid4(),
                    revision="translation-v2",
                    digest=_digest("translation-template"),
                ),
            },
            input_schema=schema,
            result_schema={
                "type": "object",
                "properties": {"attemptId": {"type": "string"}},
                "required": ["attemptId"],
                "additionalProperties": False,
            },
            max_children=5,
            max_attempts=2,
            max_active_children=2,
        ),
        budget=ExecutionBudget(total_units=12),
        deadline=now + timedelta(hours=6),
        context={"publication": "employee-handbook"},
        created_at=now,
        updated_at=now,
    )


def _multi_template_editorial_execution() -> WorkflowExecution:
    """An editorial node offering two named templates instead of one."""
    return _editorial_execution(
        templates={
            "translation": ChildTemplate(
                dependency_alias="translation-assignment",
                id=uuid4(),
                revision="translation-v2",
                digest=_digest("translation-template"),
            ),
            "review": ChildTemplate(
                dependency_alias="review-assignment",
                id=uuid4(),
                revision="review-v1",
                digest=_digest("review-template"),
            ),
        }
    )


def _assignment(
    execution: WorkflowExecution,
    locale: str,
    *,
    dependencies: tuple[str, ...] = (),
    template: str = "",
) -> WorkflowChildProposal:
    payload = {"articleId": "handbook-2026", "locale": locale}
    return WorkflowChildProposal(
        key=locale,
        objective=f"Prepare the {locale} edition",
        dependencies=dependencies,
        input=payload,
        input_digest=digest_json(payload),
        plan_digest=digest_json({"planRevision": "editorial-plan-4"}),
        budget_units=3,
        deadline=execution.deadline - timedelta(hours=1),
        agent_id=f"translator-{locale}",
        skill_id="translate-and-fact-check",
        template=template,
        context={"styleGuide": "house-2026"},
    )


class EditorialExecutionRepository(
    WorkflowExecutionRepository[WorkflowExecution, WorkflowChildExecution],
):
    """Minimal fake exercising only the expansion path of the generic repository."""

    def __init__(self) -> None:
        self.execution: WorkflowExecution | None = None
        self.children: list[WorkflowChildExecution] = []
        self.sealed = False

    async def create(self, execution):
        self.execution = execution
        return execution

    async def reserve_parent_launch(self, execution):
        return execution, True

    async def attach_parent_session(self, execution_id, *, session_id, connection_id):
        raise NotImplementedError

    async def get(self, execution_id, *, owner_id, tenant_id):
        del owner_id, tenant_id
        return self.execution if self.execution and self.execution.id == execution_id else None

    async def get_internal(self, execution_id):
        return self.execution if self.execution and self.execution.id == execution_id else None

    async def get_by_parent_session(self, *, owner_id, session_id):
        raise NotImplementedError

    async def list(self, *, owner_id, tenant_id, state="", limit, cursor=""):
        raise NotImplementedError

    async def list_reconcilable(self, *, limit):
        return []

    async def list_parent_stop_pending(self, *, limit):
        return []

    async def list_deadline_expired_children(self, *, now, limit):
        return []

    async def list_deadline_expired_executions(self, *, now, limit):
        return []

    async def mark_parent_stopped(self, execution_id):
        raise NotImplementedError

    async def record_parent_stop_error(self, execution_id, *, error, max_attempts):
        raise NotImplementedError

    async def claim_launches(self, *, worker_id, limit, lease_until):
        return []

    async def record_handle(self, child, handle, *, worker_id, lease_token, fencing_generation):
        raise NotImplementedError

    async def record_observation(self, child, observation):
        raise NotImplementedError

    async def record_gate_report(self, child_id, report):
        raise NotImplementedError

    async def record_reconcile_error(
        self, child_id, *, error, failure_kind, max_consecutive_failures
    ):
        raise NotImplementedError

    async def request_cancel(self, execution_id, *, owner_id, tenant_id):
        raise NotImplementedError

    async def update_execution_state(
        self, execution_id, *, state, suspension_reason, expected_revision
    ):
        raise NotImplementedError

    async def mark_blocker_notification(self, execution_id, *, blocker_revision):
        raise NotImplementedError

    async def reserve_message(self, message):
        raise NotImplementedError

    async def mark_message_delivered(self, message_id):
        raise NotImplementedError

    async def reserve_generation(self, execution, children, *, plan_revision):
        self.children.extend(children)
        self.sealed = True
        self.execution = replace(
            execution,
            current_generation=children[0].generation,
            plan_revision=plan_revision,
            budget=replace(
                execution.budget,
                reserved_units=sum(child.budget_units for child in children),
            ),
        )
        return self.execution

    async def list_children(self, execution_id):
        return [child for child in self.children if child.execution_id == execution_id]

    async def get_child(self, child_id):
        return next((child for child in self.children if child.id == child_id), None)

    async def seal_generation(self, execution_id, generation):
        del execution_id, generation
        self.sealed = True

    async def join_status(self, execution_id, generation):
        return calculate_join(
            generation,
            sealed=self.sealed,
            children=await self.list_children(execution_id),
        )

    async def create_retry(self, child, retry, *, owner_id, tenant_id):
        del owner_id, tenant_id
        self.children = [
            replace(item, state=ChildExecutionState.SUPERSEDED) if item.id == child.id else item
            for item in self.children
        ]
        self.children.append(retry)
        return retry


@pytest.mark.asyncio
async def test_noncoding_lifecycle_service_reserves_the_same_durable_ledger_contract() -> None:
    execution = _editorial_execution()
    repository = EditorialExecutionRepository()
    lifecycle = WorkflowExecutionLifecycle(repository=repository)

    reserved = await lifecycle.expand(
        execution,
        coordinator_id="editorial-coordinator",
        generation=1,
        plan_revision="editorial-plan-4",
        children=[_assignment(execution, "pt-BR")],
    )

    assert reserved.current_generation == 1
    assert reserved.budget.reserved_units == 3
    assert repository.children[0].message_id.startswith("workflow-execution:")


def test_noncoding_workflow_uses_full_expansion_attempt_and_join_lifecycle() -> None:
    execution = _editorial_execution()
    ordered = validate_expansion(
        execution,
        coordinator_id="editorial-coordinator",
        generation=1,
        children=[
            _assignment(execution, "fr-CA", dependencies=("es-MX",)),
            _assignment(execution, "es-MX"),
        ],
    )

    assert [proposal.key for proposal in ordered] == ["es-MX", "fr-CA"]
    children = list(make_children(execution, 1, ordered))
    assert children[0].context == {"styleGuide": "house-2026"}
    assert calculate_join(1, sealed=True, children=children).pending == (
        "es-MX",
        "fr-CA",
    )

    children[0] = replace(children[0], state=ChildExecutionState.COMPLETED)
    children[1] = replace(children[1], state=ChildExecutionState.BLOCKED)
    blocked = calculate_join(1, sealed=True, children=children)
    assert blocked.blocked == ("fr-CA",)

    retry = make_retry(
        children[1],
        attempt_id=children[1].id,
        max_attempts=execution.policy.max_attempts,
    )
    assert retry.attempt == 2
    assert retry.input == {"articleId": "handbook-2026", "locale": "fr-CA"}
    assert retry.task_id == ""

    children.extend([replace(children[1], state=ChildExecutionState.SUPERSEDED), retry])
    children[-1] = replace(children[-1], state=ChildExecutionState.COMPLETED)
    assert calculate_join(1, sealed=True, children=children).ready is True


def test_noncoding_workflow_enforces_shared_budget_and_attempt_limits() -> None:
    execution = _editorial_execution()
    oversized = replace(_assignment(execution, "de-DE"), budget_units=13)
    with pytest.raises(WorkflowExecutionError, match="available budget"):
        validate_expansion(
            execution,
            coordinator_id="editorial-coordinator",
            generation=1,
            children=[oversized],
        )

    child = make_children(execution, 1, (_assignment(execution, "de-DE"),))[0]
    exhausted = replace(child, attempt=2, state=ChildExecutionState.FAILED)
    with pytest.raises(WorkflowExecutionError, match="attempt limit"):
        make_retry(exhausted, attempt_id=exhausted.id, max_attempts=2)


def test_child_naming_an_unknown_template_is_rejected() -> None:
    execution = _multi_template_editorial_execution()
    proposal = _assignment(execution, "fr-CA", template="proofreading")
    with pytest.raises(WorkflowExecutionError, match="unknown child template"):
        validate_expansion(
            execution,
            coordinator_id="editorial-coordinator",
            generation=1,
            children=[proposal],
        )


def test_omitting_template_when_several_are_offered_is_rejected() -> None:
    execution = _multi_template_editorial_execution()
    proposal = _assignment(execution, "fr-CA")
    with pytest.raises(WorkflowExecutionError, match="child template is required"):
        validate_expansion(
            execution,
            coordinator_id="editorial-coordinator",
            generation=1,
            children=[proposal],
        )


def test_omitting_template_when_only_one_is_offered_defaults_to_it() -> None:
    execution = _editorial_execution()
    proposal = _assignment(execution, "fr-CA")
    ordered = validate_expansion(
        execution,
        coordinator_id="editorial-coordinator",
        generation=1,
        children=[proposal],
    )
    child = make_children(execution, 1, ordered)[0]
    sole = execution.policy.sole_template
    assert child.template_id == sole.id
    assert child.template_revision == sole.revision
    assert child.template_digest == sole.digest


def test_each_child_is_stamped_with_its_own_named_templates_pin() -> None:
    execution = _multi_template_editorial_execution()
    proposals = [
        _assignment(execution, "fr-CA", template="translation"),
        _assignment(execution, "es-MX", template="review"),
    ]
    ordered = validate_expansion(
        execution,
        coordinator_id="editorial-coordinator",
        generation=1,
        children=proposals,
    )
    children = {child.key: child for child in make_children(execution, 1, ordered)}
    translation = execution.policy.templates["translation"]
    review = execution.policy.templates["review"]
    assert children["fr-CA"].template_id == translation.id
    assert children["fr-CA"].template_revision == translation.revision
    assert children["fr-CA"].template_digest == translation.digest
    assert children["es-MX"].template_id == review.id
    assert children["es-MX"].template_revision == review.revision
    assert children["es-MX"].template_digest == review.digest

    # resolve_child_template recovers the same named template from the
    # child's own recorded pin, independent of what other templates the
    # node offers.
    name, template = resolve_child_template(execution.policy, children["es-MX"])
    assert name == "review"
    assert template == review


def test_retry_keeps_the_childs_original_template() -> None:
    execution = _multi_template_editorial_execution()
    proposal = _assignment(execution, "es-MX", template="review")
    child = make_children(execution, 1, (proposal,))[0]
    blocked = replace(child, state=ChildExecutionState.BLOCKED)

    retry = make_retry(blocked, attempt_id=blocked.id, max_attempts=execution.policy.max_attempts)

    review = execution.policy.templates["review"]
    assert retry.template_id == review.id
    assert retry.template_revision == review.revision
    assert retry.template_digest == review.digest
