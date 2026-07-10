"""PostgreSQL resident runtime adapter tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from volundr.adapters.outbound.postgres_resident_runtimes import (
    PostgresResidentRuntimeRepository,
)
from volundr.domain.models import (
    ResidentBackend,
    ResidentCapability,
    ResidentCondition,
    ResidentConditionStatus,
    ResidentDesiredState,
    ResidentEndpoint,
    ResidentEngine,
    ResidentObservedState,
    ResidentRuntime,
)


def _runtime() -> ResidentRuntime:
    now = datetime.now(UTC)
    return ResidentRuntime(
        id=uuid4(),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        persona_name="product-steward",
        model="gpt-5.6",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-openshell",
        desired_state=ResidentDesiredState.RUNNING,
        observed_state=ResidentObservedState.ACTIVE,
        backend_ref={"kind": "Sandbox", "name": "muninn"},
        endpoints=[ResidentEndpoint(kind="chat", protocol="skuld-v1", url="/s/muninn")],
        capabilities=[ResidentCapability.CHAT],
        conditions=[ResidentCondition(type="Ready", status=ResidentConditionStatus.TRUE)],
        created_at=now,
        updated_at=now,
    )


def _row(runtime: ResidentRuntime) -> dict:
    return {
        "id": runtime.id,
        "owner_id": runtime.owner_id,
        "tenant_id": runtime.tenant_id,
        "name": runtime.name,
        "persona_name": runtime.persona_name,
        "model": runtime.model,
        "backend": runtime.backend.value,
        "engine": runtime.engine.value,
        "profile_id": runtime.profile_id,
        "desired_state": runtime.desired_state.value,
        "observed_state": runtime.observed_state.value,
        "backend_ref": runtime.backend_ref,
        "endpoints": [endpoint.model_dump(mode="json") for endpoint in runtime.endpoints],
        "capabilities": [capability.value for capability in runtime.capabilities],
        "conditions": [condition.model_dump(mode="json") for condition in runtime.conditions],
        "created_at": runtime.created_at,
        "updated_at": runtime.updated_at,
    }


async def test_create_and_update_persist_full_runtime_contract() -> None:
    pool = AsyncMock()
    repository = PostgresResidentRuntimeRepository(pool)
    runtime = _runtime()

    assert await repository.create(runtime) == runtime
    assert await repository.update(runtime) == runtime

    create_args = pool.execute.await_args_list[0].args
    assert "INSERT INTO resident_runtimes" in create_args[0]
    assert runtime.owner_id in create_args
    assert runtime.backend.value in create_args
    update_args = pool.execute.await_args_list[1].args
    assert "UPDATE resident_runtimes" in update_args[0]
    assert runtime.observed_state.value in update_args


async def test_get_and_scoped_list_map_json_and_enums() -> None:
    runtime = _runtime()
    pool = AsyncMock()
    pool.fetchrow.return_value = _row(runtime)
    pool.fetch.return_value = [_row(runtime)]
    repository = PostgresResidentRuntimeRepository(pool)

    assert await repository.get(runtime.id) == runtime
    assert await repository.get_by_owner_name(runtime.owner_id, runtime.name) == runtime
    assert await repository.list(tenant_id="tenant-a", owner_id="user-a") == [runtime]
    assert await repository.list(tenant_id="tenant-a") == [runtime]

    owner_query = pool.fetch.await_args_list[0].args[0]
    assert "tenant_id = $1 AND owner_id = $2" in owner_query
    admin_query = pool.fetch.await_args_list[1].args[0]
    assert "tenant_id = $1" in admin_query
    assert "owner_id = $2" not in admin_query


async def test_missing_get_and_delete_result() -> None:
    pool = AsyncMock()
    pool.fetchrow.return_value = None
    pool.execute.side_effect = ["DELETE 1", "DELETE 0"]
    repository = PostgresResidentRuntimeRepository(pool)
    runtime_id = uuid4()

    assert await repository.get(runtime_id) is None
    assert await repository.delete(runtime_id)
    assert not await repository.delete(runtime_id)


async def test_reconciliation_list_excludes_deleted_intent() -> None:
    runtime = _runtime()
    pool = AsyncMock()
    pool.fetch.return_value = [_row(runtime)]
    repository = PostgresResidentRuntimeRepository(pool)

    assert await repository.list_for_reconciliation() == [runtime]
    assert "desired_state <> 'deleted'" in pool.fetch.await_args.args[0]
