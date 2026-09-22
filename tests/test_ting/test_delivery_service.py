"""Unit tests for ting.delivery.service: the coordinator surface and the
module-level payload/child-factory helpers that back it.

DeliveryExecutionCoordinator is exercised against a small fake service (same
pattern as tests/test_ting/test_delivery_execution.py's
`test_coordinator_derives_canonical_work_order_digests`) so these tests stay
independent of the full DeliveryExecutionService/repository wiring.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from ting.delivery.service import (
    DeliveryExecutionCoordinator,
    _make_delivery_children,
    _proposal_from_payload,
)
from ting.domain.workflow_execution import (
    ChildExecutionState,
    ExecutionState,
    WorkflowExecutionError,
    digest_json,
)


def _coordinator(service: object) -> DeliveryExecutionCoordinator:
    return DeliveryExecutionCoordinator(
        service,  # type: ignore[arg-type]
        execution_id=uuid4(),
        owner_id="owner-1",
        tenant_id="tenant-1",
        coordinator_id="coordinator-1",
    )


# ---------------------------------------------------------------------------
# DeliveryExecutionCoordinator.expand() validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expand_requires_plan_revision() -> None:
    coordinator = _coordinator(service=object())

    with pytest.raises(WorkflowExecutionError, match="plan_revision is required"):
        await coordinator.expand({"generation": 1, "workstreams": []})


@pytest.mark.asyncio
async def test_expand_requires_workstreams_list() -> None:
    coordinator = _coordinator(service=object())

    with pytest.raises(WorkflowExecutionError, match="workstreams must be a list"):
        await coordinator.expand(
            {"generation": 1, "plan_revision": "approved-plan", "workstreams": "not-a-list"}
        )


# ---------------------------------------------------------------------------
# DeliveryExecutionCoordinator.reconcile()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_delegates_to_service_and_returns_its_payload() -> None:
    calls: list[object] = []

    class Service:
        async def reconcile(self, execution_id):
            calls.append(execution_id)
            return {"reconciled": True, "launched": 2}

    execution_id = uuid4()
    coordinator = DeliveryExecutionCoordinator(
        Service(),  # type: ignore[arg-type]
        execution_id=execution_id,
        owner_id="owner-1",
        tenant_id="tenant-1",
        coordinator_id="coordinator-1",
    )

    result = await coordinator.reconcile({"ignored": "payload"})

    assert result == {"reconciled": True, "launched": 2}
    assert calls == [execution_id]


# ---------------------------------------------------------------------------
# DeliveryExecutionCoordinator.cancel()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_delegates_and_shapes_response() -> None:
    execution_id = uuid4()
    captured: dict[str, object] = {}

    class Service:
        async def cancel(self, exec_id, *, owner_id, tenant_id):
            captured["execution_id"] = exec_id
            captured["owner_id"] = owner_id
            captured["tenant_id"] = tenant_id
            return SimpleNamespace(id=exec_id, state=ExecutionState.CANCELED)

    coordinator = DeliveryExecutionCoordinator(
        Service(),  # type: ignore[arg-type]
        execution_id=execution_id,
        owner_id="owner-9",
        tenant_id="tenant-9",
        coordinator_id="coordinator-1",
    )

    result = await coordinator.cancel({"ignored": "payload"})

    assert result == {"executionId": str(execution_id), "state": "canceled"}
    assert captured == {
        "execution_id": execution_id,
        "owner_id": "owner-9",
        "tenant_id": "tenant-9",
    }


# ---------------------------------------------------------------------------
# DeliveryExecutionCoordinator.message()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_requires_valid_uuid_attempt_id() -> None:
    coordinator = _coordinator(service=object())

    with pytest.raises(WorkflowExecutionError, match="attempt_id must be a UUID"):
        await coordinator.message({"attempt_id": "not-a-uuid", "child_key": "api"})


@pytest.mark.asyncio
async def test_message_delegates_and_shapes_response() -> None:
    execution_id = uuid4()
    attempt_id = uuid4()
    captured: dict[str, object] = {}

    class Service:
        async def message(
            self,
            exec_id,
            *,
            owner_id,
            tenant_id,
            child_key,
            attempt_id,
            answer,
            metadata,
            message_id,
        ):
            captured.update(
                {
                    "execution_id": exec_id,
                    "owner_id": owner_id,
                    "tenant_id": tenant_id,
                    "child_key": child_key,
                    "attempt_id": attempt_id,
                    "answer": answer,
                    "metadata": metadata,
                    "message_id": message_id,
                }
            )
            return SimpleNamespace(
                execution_id=exec_id,
                key=child_key,
                attempt=3,
                state=ChildExecutionState.RUNNING,
            )

    coordinator = DeliveryExecutionCoordinator(
        Service(),  # type: ignore[arg-type]
        execution_id=execution_id,
        owner_id="owner-1",
        tenant_id="tenant-1",
        coordinator_id="coordinator-1",
    )

    result = await coordinator.message(
        {
            "attemptId": str(attempt_id),
            "childKey": "api",
            "answer": "go ahead",
            "metadata": {"source": "operator"},
            "messageId": "msg-1",
        }
    )

    assert result == {
        "executionId": str(execution_id),
        "childKey": "api",
        "attempt": 3,
        "state": "running",
    }
    assert captured["attempt_id"] == attempt_id
    assert captured["child_key"] == "api"
    assert captured["answer"] == "go ahead"
    assert captured["metadata"] == {"source": "operator"}
    assert captured["message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_message_accepts_snake_case_keys() -> None:
    execution_id = uuid4()
    attempt_id = uuid4()
    captured: dict[str, object] = {}

    class Service:
        async def message(self, exec_id, *, child_key, attempt_id, **kwargs):
            captured["child_key"] = child_key
            captured["attempt_id"] = attempt_id
            return SimpleNamespace(
                execution_id=exec_id,
                key=child_key,
                attempt=1,
                state=ChildExecutionState.WAITING,
            )

    coordinator = DeliveryExecutionCoordinator(
        Service(),  # type: ignore[arg-type]
        execution_id=execution_id,
        owner_id="owner-1",
        tenant_id="tenant-1",
        coordinator_id="coordinator-1",
    )

    result = await coordinator.message({"attempt_id": str(attempt_id), "child_key": "worker"})

    assert result["state"] == "waiting"
    assert captured == {"child_key": "worker", "attempt_id": attempt_id}


# ---------------------------------------------------------------------------
# _proposal_from_payload()
# ---------------------------------------------------------------------------


_BASE_SHA = "a" * 40


def _workspace(key: str = "api") -> dict:
    return {
        "allocation_id": f"allocation-{key}",
        "campaign_id": str(uuid4()),
        "workstream_key": key,
        "repository": "git@example/repo",
        "workspace_path": f"/worktrees/{key}",
        "repository_path": "git@example/repo",
        "base_sha": _BASE_SHA,
        "branch_name": f"workstream/{key}",
        "worker_id": f"worker-{key}",
        "allowed_paths": ["src"],
        "test_contract_ids": [],
    }


def _valid_payload(**overrides: object) -> dict:
    payload = {
        "key": "api",
        "objective": "Implement API",
        "requirementIds": ["REQ-1"],
        "dependencies": [],
        "input": {"foo": "bar"},
        "repository": "git@example/repo",
        "baseSha": _BASE_SHA,
        "budgetUnits": 5,
        "deadline": datetime.now(UTC).isoformat(),
        "agentId": "agent-1",
        "skillId": "skill-1",
        "workspace": _workspace(),
    }
    payload.update(overrides)
    return payload


def test_proposal_from_payload_rejects_non_dict() -> None:
    with pytest.raises(WorkflowExecutionError, match="each workstream must be an object"):
        _proposal_from_payload("not-a-dict", plan_digest="sha256:deadbeef")


def test_proposal_from_payload_rejects_mismatched_input_digest() -> None:
    payload = _valid_payload(inputDigest="sha256:doesnotmatch")

    with pytest.raises(
        WorkflowExecutionError, match="workstream inputDigest does not match canonical input"
    ):
        _proposal_from_payload(payload, plan_digest="sha256:deadbeef")


def test_proposal_from_payload_rejects_mismatched_plan_digest() -> None:
    payload = _valid_payload(planDigest="sha256:doesnotmatch")

    with pytest.raises(
        WorkflowExecutionError, match="workstream planDigest does not match plan_revision"
    ):
        _proposal_from_payload(payload, plan_digest="sha256:deadbeef")


def test_proposal_from_payload_builds_proposal_when_digests_match_or_absent() -> None:
    plan_digest = digest_json({"planRevision": "approved-plan"})
    payload = _valid_payload()
    input_digest = digest_json(payload["input"])
    payload["inputDigest"] = input_digest
    payload["planDigest"] = plan_digest

    proposal = _proposal_from_payload(payload, plan_digest=plan_digest)

    assert proposal.key == "api"
    assert proposal.input_digest == input_digest
    assert proposal.plan_digest == plan_digest
    assert proposal.requirement_ids == ("REQ-1",)
    assert proposal.agent_id == "agent-1"
    assert proposal.skill_id == "skill-1"


# ---------------------------------------------------------------------------
# _make_delivery_children()
# ---------------------------------------------------------------------------


def test_make_delivery_children_rejects_wrong_message_namespace() -> None:
    with pytest.raises(WorkflowExecutionError, match="delivery child message namespace is invalid"):
        _make_delivery_children(
            execution=SimpleNamespace(),
            generation=1,
            ordered=(),
            message_namespace="not-workflow-execution",
        )
