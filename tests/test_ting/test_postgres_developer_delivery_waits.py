"""Tests for the postgres durable ledger of exact remote delivery observations."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tests.test_ting.test_developer_execution import _execution
from ting.adapters.postgres_developer_delivery_waits import (
    PostgresDeveloperDeliveryWaitRepository,
)
from ting.domain.developer_delivery_wait import (
    DeveloperDeliveryObservation,
    DeveloperDeliveryObservationStatus,
    DeveloperDeliveryWait,
    DeveloperDeliveryWaitMode,
    DeveloperDeliveryWaitRequest,
    DeveloperDeliveryWaitState,
    developer_delivery_candidate_digest,
)
from ting.domain.developer_execution import DeveloperExecutionError
from ting.domain.workflow_execution import ExecutionState


def _request(**changes) -> DeveloperDeliveryWaitRequest:
    payload = {
        "mode": DeveloperDeliveryWaitMode.CHECKS,
        "repository": "https://example.test/repo.git",
        "review_number": 7,
        "expected_head_sha": "a" * 40,
        "expected_base_sha": "b" * 40,
        "expected_target_branch": "main",
        "policy_id": "policy-1",
    }
    payload.update(changes)
    return DeveloperDeliveryWaitRequest.model_validate(payload)


def _execution_row(execution) -> dict:
    return {
        "id": execution.id,
        "revision": execution.revision,
        "current_generation": execution.current_generation,
        "integration_candidate": None,
        "integration_allocation": None,
        "cancel_requested": execution.cancel_requested,
        "state": execution.state.value,
    }


def _wait_row(wait_id, execution, request, *, request_digest, candidate_digest, **changes) -> dict:
    row = {
        "id": wait_id,
        "execution_id": execution.id,
        "execution_generation": execution.current_generation,
        "execution_revision": execution.revision,
        "candidate_digest": candidate_digest,
        "request_digest": request_digest,
        "request": json.dumps(request.canonical_payload(), sort_keys=True),
        "state": DeveloperDeliveryWaitState.PENDING.value,
        "next_poll_at": datetime.now(UTC),
        "observation": None,
        "attempt_count": 0,
        "last_error": "",
        "lease_owner": "",
        "lease_token": None,
        "fencing_generation": 0,
        "lease_expires_at": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "notified_at": None,
    }
    row.update(changes)
    return row


def _observation(
    *, terminal_status: DeveloperDeliveryObservationStatus
) -> DeveloperDeliveryObservation:
    return DeveloperDeliveryObservation(
        status=terminal_status,
        repository="https://example.test/repo.git",
        review_number=7,
        expected_head_sha="a" * 40,
        expected_base_sha="b" * 40,
        expected_target_branch="main",
        observed_at=datetime.now(UTC),
        reason="",
    )


class _ContextManager:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Connection:
    """Dispatches fetchrow/execute calls by query prefix, like the sibling adapter tests."""

    def __init__(self):
        self.in_transaction = False
        self.fetchrow_handlers: list[tuple[str, object]] = []
        self.execute_handlers: list[tuple[str, object]] = []
        self.executed_queries: list[str] = []

    def transaction(self):
        connection = self

        class _Transaction(_ContextManager):
            async def __aenter__(self):
                connection.in_transaction = True
                return connection

            async def __aexit__(self, *_args):
                connection.in_transaction = False
                return False

        return _Transaction(self)

    async def fetchrow(self, query, *args):
        assert self.in_transaction
        for marker, handler in self.fetchrow_handlers:
            if marker in query:
                return handler(*args) if callable(handler) else handler
        raise AssertionError(f"unexpected fetchrow: {query}")

    async def execute(self, query, *args):
        assert self.in_transaction
        self.executed_queries.append(query)
        for marker, handler in self.execute_handlers:
            if marker in query:
                return handler(*args) if callable(handler) else handler
        raise AssertionError(f"unexpected execute: {query}")


class _Pool:
    def __init__(self, connection: _Connection, *, execute_result: str = "UPDATE 1"):
        self.connection = connection
        self.execute_result = execute_result
        self.executed_queries: list[str] = []

    def acquire(self):
        return _ContextManager(self.connection)

    async def execute(self, query, *args):
        self.executed_queries.append(query)
        return self.execute_result


def _reserve_context():
    execution = replace(
        _execution(), state=ExecutionState.RUNNING, revision=1, current_generation=1
    )
    request = _request()
    candidate_digest = developer_delivery_candidate_digest(
        integration_candidate=None, integration_allocation=None
    )
    request_digest = request.digest
    wait_id = uuid4()
    return execution, request, candidate_digest, request_digest, wait_id


@pytest.mark.asyncio
async def test_reserve_inserts_new_wait_and_projects_execution() -> None:
    execution, request, candidate_digest, request_digest, wait_id = _reserve_context()
    connection = _Connection()
    inserted_row = _wait_row(
        wait_id,
        execution,
        request,
        request_digest=request_digest,
        candidate_digest=candidate_digest,
    )
    connection.fetchrow_handlers = [
        (
            "FROM developer_executions WHERE id = $1 FOR UPDATE",
            lambda *_: _execution_row(execution),
        ),
        ("AND execution_generation = $2", lambda *_: None),
        ("AND request_digest = $2", lambda *_: None),
        ("INSERT INTO developer_delivery_waits", lambda *_: inserted_row),
    ]
    connection.execute_handlers = [("UPDATE developer_executions", lambda *_: "UPDATE 1")]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    next_poll_at = datetime.now(UTC) + timedelta(seconds=30)
    wait = await repository.reserve(
        execution,
        request,
        wait_id=wait_id,
        request_digest=request_digest,
        candidate_digest=candidate_digest,
        next_poll_at=next_poll_at,
    )

    assert wait.id == wait_id
    assert wait.state == DeveloperDeliveryWaitState.PENDING
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_reserve_replays_existing_wait_for_same_request_digest() -> None:
    execution, request, candidate_digest, request_digest, wait_id = _reserve_context()
    connection = _Connection()
    existing_row = _wait_row(
        wait_id,
        execution,
        request,
        request_digest=request_digest,
        candidate_digest=candidate_digest,
    )
    connection.fetchrow_handlers = [
        (
            "FROM developer_executions WHERE id = $1 FOR UPDATE",
            lambda *_: _execution_row(execution),
        ),
        ("AND execution_generation = $2", lambda *_: None),
        ("AND request_digest = $2", lambda *_: existing_row),
    ]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    wait = await repository.reserve(
        execution,
        request,
        wait_id=uuid4(),
        request_digest=request_digest,
        candidate_digest=candidate_digest,
        next_poll_at=datetime.now(UTC),
    )

    assert wait.id == wait_id
    assert not connection.executed_queries


@pytest.mark.asyncio
async def test_reserve_rejects_changed_execution() -> None:
    execution, request, candidate_digest, request_digest, wait_id = _reserve_context()
    connection = _Connection()
    stale_row = _execution_row(execution) | {"revision": execution.revision + 1}
    connection.fetchrow_handlers = [
        ("FROM developer_executions WHERE id = $1 FOR UPDATE", lambda *_: stale_row),
    ]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    with pytest.raises(DeveloperExecutionError, match="changed while reserving"):
        await repository.reserve(
            execution,
            request,
            wait_id=wait_id,
            request_digest=request_digest,
            candidate_digest=candidate_digest,
            next_poll_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_claim_due_passes_now_limit_worker_and_lease_and_maps_rows() -> None:
    execution = _execution()
    request = _request()
    candidate_digest = developer_delivery_candidate_digest(
        integration_candidate=None, integration_allocation=None
    )
    row = _wait_row(
        uuid4(),
        execution,
        request,
        request_digest=request.digest,
        candidate_digest=candidate_digest,
    )

    class _ClaimPool:
        def __init__(self):
            self.query = ""
            self.args = ()

        async def fetch(self, query, *args):
            self.query = query
            self.args = args
            return [row]

    pool = _ClaimPool()
    repository = PostgresDeveloperDeliveryWaitRepository(pool)
    now = datetime.now(UTC)
    lease_until = now + timedelta(seconds=60)

    waits = await repository.claim_due(
        worker_id="worker-1", limit=5, lease_until=lease_until, now=now
    )

    assert pool.args == (now, 5, "worker-1", lease_until)
    assert "FOR UPDATE SKIP LOCKED" in pool.query
    assert len(waits) == 1
    assert waits[0].id == row["id"]


def _leased_wait(**changes) -> DeveloperDeliveryWait:
    execution = _execution()
    request = _request()
    candidate_digest = developer_delivery_candidate_digest(
        integration_candidate=None, integration_allocation=None
    )
    value = DeveloperDeliveryWait(
        id=uuid4(),
        execution_id=execution.id,
        request=request,
        request_digest=request.digest,
        execution_generation=execution.current_generation,
        execution_revision=execution.revision,
        candidate_digest=candidate_digest,
        state=DeveloperDeliveryWaitState.PENDING,
        next_poll_at=datetime.now(UTC),
        lease_token=uuid4(),
        fencing_generation=1,
    )
    return replace(value, **changes)


@pytest.mark.asyncio
async def test_record_pending_updates_and_raises_on_stale_lease() -> None:
    wait = _leased_wait()
    pool_ok = _Pool(_Connection(), execute_result="UPDATE 1")
    repository = PostgresDeveloperDeliveryWaitRepository(pool_ok)

    await repository.record_pending(wait, None, next_poll_at=datetime.now(UTC))

    assert pool_ok.executed_queries

    pool_stale = _Pool(_Connection(), execute_result="UPDATE 0")
    repository_stale = PostgresDeveloperDeliveryWaitRepository(pool_stale)
    with pytest.raises(DeveloperExecutionError, match="lease is no longer current"):
        await repository_stale.record_pending(wait, None, next_poll_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_record_terminal_success_projects_execution() -> None:
    wait = _leased_wait()
    observation = _observation(terminal_status=DeveloperDeliveryObservationStatus.CHECKS_PASSED)
    connection = _Connection()
    updated_row = _wait_row(
        wait.id,
        _execution(id=wait.execution_id),
        wait.request,
        request_digest=wait.request_digest,
        candidate_digest=wait.candidate_digest,
        state=DeveloperDeliveryWaitState.READY.value,
    )
    connection.fetchrow_handlers = [
        ("UPDATE developer_delivery_waits", lambda *_: updated_row),
    ]
    connection.execute_handlers = [("UPDATE developer_executions", lambda *_: "UPDATE 1")]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    result = await repository.record_terminal(
        wait,
        observation,
        expected_execution_revision=wait.execution_revision,
        project_execution=True,
    )

    assert result.state == DeveloperDeliveryWaitState.READY
    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_record_terminal_raises_when_wait_lease_is_stale() -> None:
    wait = _leased_wait()
    observation = _observation(terminal_status=DeveloperDeliveryObservationStatus.CHECKS_PASSED)
    connection = _Connection()
    connection.fetchrow_handlers = [
        ("UPDATE developer_delivery_waits", lambda *_: None),
    ]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    with pytest.raises(DeveloperExecutionError, match="lease is no longer current"):
        await repository.record_terminal(
            wait,
            observation,
            expected_execution_revision=wait.execution_revision,
            project_execution=True,
        )


@pytest.mark.asyncio
async def test_record_terminal_raises_and_rolls_back_when_execution_row_did_not_update() -> None:
    """The T-D fix: a concurrent revision bump on the execution must fail the whole write."""
    wait = _leased_wait()
    observation = _observation(terminal_status=DeveloperDeliveryObservationStatus.CHECKS_PASSED)
    connection = _Connection()
    updated_row = _wait_row(
        wait.id,
        _execution(id=wait.execution_id),
        wait.request,
        request_digest=wait.request_digest,
        candidate_digest=wait.candidate_digest,
        state=DeveloperDeliveryWaitState.READY.value,
    )
    connection.fetchrow_handlers = [
        ("UPDATE developer_delivery_waits", lambda *_: updated_row),
    ]
    connection.execute_handlers = [("UPDATE developer_executions", lambda *_: "UPDATE 0")]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    with pytest.raises(DeveloperExecutionError, match="stays pending and is observed again"):
        await repository.record_terminal(
            wait,
            observation,
            expected_execution_revision=wait.execution_revision,
            project_execution=True,
        )

    assert connection.in_transaction is False


@pytest.mark.asyncio
async def test_record_terminal_skips_execution_projection_when_not_requested() -> None:
    wait = _leased_wait()
    observation = _observation(terminal_status=DeveloperDeliveryObservationStatus.CHECKS_FAILED)
    connection = _Connection()
    updated_row = _wait_row(
        wait.id,
        _execution(id=wait.execution_id),
        wait.request,
        request_digest=wait.request_digest,
        candidate_digest=wait.candidate_digest,
        state=DeveloperDeliveryWaitState.FAILED.value,
    )
    connection.fetchrow_handlers = [
        ("UPDATE developer_delivery_waits", lambda *_: updated_row),
    ]
    repository = PostgresDeveloperDeliveryWaitRepository(_Pool(connection))

    result = await repository.record_terminal(
        wait,
        observation,
        expected_execution_revision=wait.execution_revision,
        project_execution=False,
    )

    assert result.state == DeveloperDeliveryWaitState.FAILED
    assert connection.executed_queries == []


@pytest.mark.asyncio
async def test_mark_notified_updates_and_raises_on_stale_lease() -> None:
    wait = _leased_wait(state=DeveloperDeliveryWaitState.READY)
    pool_ok = _Pool(_Connection(), execute_result="UPDATE 1")
    repository = PostgresDeveloperDeliveryWaitRepository(pool_ok)

    await repository.mark_notified(wait)

    assert pool_ok.executed_queries

    pool_stale = _Pool(_Connection(), execute_result="UPDATE 0")
    repository_stale = PostgresDeveloperDeliveryWaitRepository(pool_stale)
    with pytest.raises(DeveloperExecutionError, match="notification lease is no longer current"):
        await repository_stale.mark_notified(wait)


def test_require_updated_raises_for_anything_other_than_update_one() -> None:
    from ting.adapters.postgres_developer_delivery_waits import _require_updated

    _require_updated("UPDATE 1", "should not raise")
    with pytest.raises(DeveloperExecutionError, match="boom"):
        _require_updated("UPDATE 0", "boom")
    with pytest.raises(DeveloperExecutionError, match="boom"):
        _require_updated("UPDATE 2", "boom")
