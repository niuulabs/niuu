"""REST tests for resident runtime reads and profile discovery."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_resident_runtimes import create_resident_runtimes_router
from volundr.domain.models import (
    Principal,
    ResidentBackend,
    ResidentCapability,
    ResidentDeploymentProfile,
    ResidentEngine,
    ResidentLogEntry,
    ResidentLogPage,
    ResidentRuntime,
    ResidentSession,
)
from volundr.domain.services.resident_runtime import (
    ResidentRuntimeConflictError,
    ResidentRuntimeDeploymentError,
    ResidentRuntimeNotFoundError,
)

_PRINCIPAL = Principal(
    user_id="user-a",
    email="user-a@example.test",
    tenant_id="tenant-a",
    roles=["volundr:developer"],
)


def _client(service: Mock) -> TestClient:
    app = FastAPI()
    app.include_router(create_resident_runtimes_router(service))
    app.dependency_overrides[extract_principal] = lambda: _PRINCIPAL
    return TestClient(app)


def test_lists_only_public_profile_fields() -> None:
    profile = ResidentDeploymentProfile(
        id="ravn-openshell",
        display_name="Ravn on OpenShell",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        capabilities=[ResidentCapability.CHAT],
        deployment={"image": "private-deployment-input"},
    )
    service = Mock()
    service.list_profiles.return_value = [profile]

    response = _client(service).get("/api/v1/forge/resident-profiles")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "ravn-openshell",
            "displayName": "Ravn on OpenShell",
            "description": "",
            "backend": "openshell",
            "engine": "ravn",
            "capabilities": ["chat"],
            "defaultModel": "",
            "allowedModels": [],
            "modelPrefix": "",
            "labels": [],
        }
    ]


def test_lists_and_gets_with_authenticated_principal() -> None:
    runtime = ResidentRuntime(
        id=uuid4(),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-openshell",
    )
    service = Mock()
    service.list = AsyncMock(return_value=[runtime])
    service.get = AsyncMock(return_value=runtime)
    client = _client(service)

    listed = client.get("/api/v1/forge/resident-runtimes")
    fetched = client.get(f"/api/v1/forge/resident-runtimes/{runtime.id}")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(runtime.id)
    assert listed.json()[0]["profileId"] == "ravn-openshell"
    assert fetched.status_code == 200
    service.list.assert_awaited_once_with(_PRINCIPAL)
    service.get.assert_awaited_once_with(_PRINCIPAL, runtime.id)


def test_hidden_runtime_returns_not_found() -> None:
    service = Mock()
    service.get = AsyncMock(side_effect=ResidentRuntimeNotFoundError("Resident runtime not found"))

    response = _client(service).get(f"/api/v1/forge/resident-runtimes/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["detail"] == "Resident runtime not found"


def test_reads_normalized_resident_logs() -> None:
    runtime_id = uuid4()
    service = Mock()
    service.logs = AsyncMock(
        return_value=ResidentLogPage(
            entries=[
                ResidentLogEntry(
                    timestamp_ms=1234,
                    level="OCSF",
                    source="sandbox",
                    message="PROC:LAUNCH ravn",
                )
            ],
            buffer_total=1,
        )
    )

    response = _client(service).get(
        f"/api/v1/forge/resident-runtimes/{runtime_id}/logs",
        params=[("lines", "25"), ("source", "sandbox"), ("min_level", "INFO")],
    )

    assert response.status_code == 200
    assert response.json()["entries"][0]["message"] == "PROC:LAUNCH ravn"
    service.logs.assert_awaited_once_with(
        _PRINCIPAL,
        runtime_id,
        lines=25,
        sources=("sandbox",),
        min_level="INFO",
    )


def test_records_resident_usage() -> None:
    runtime = ResidentRuntime(
        id=uuid4(),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        backend=ResidentBackend.OPENSHELL,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-openshell",
        capabilities=[ResidentCapability.USAGE],
        tokens_used=42,
        message_count=1,
        cost=0.12,
    )
    service = Mock()
    service.record_usage = AsyncMock(return_value=runtime)

    response = _client(service).post(
        f"/api/v1/forge/resident-runtimes/{runtime.id}/usage",
        json={
            "tokens": 42,
            "cost": 0.12,
            "message_count": 1,
            "provider": "cloud",
            "model": "gpt-5.6-sol",
        },
    )

    assert response.status_code == 200
    assert response.json()["tokensUsed"] == 42
    service.record_usage.assert_awaited_once_with(
        _PRINCIPAL,
        runtime.id,
        tokens=42,
        cost=0.12,
        message_count=1,
    )


def test_native_resident_session_crud_uses_authenticated_service() -> None:
    runtime_id = uuid4()
    session = ResidentSession(
        id=uuid4(),
        resident_id=runtime_id,
        title="Persistent work",
        model="niuu/gpt-5.6-sol",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        chat_endpoint=f"/s/{runtime_id}/session",
    )
    service = Mock()
    service.list_sessions = AsyncMock(return_value=[session])
    service.create_session = AsyncMock(return_value=session)
    service.delete_session = AsyncMock(return_value=None)
    client = _client(service)

    listed = client.get(f"/api/v1/forge/resident-runtimes/{runtime_id}/sessions")
    created = client.post(
        f"/api/v1/forge/resident-runtimes/{runtime_id}/sessions",
        json={"title": "Persistent work", "model": "niuu/gpt-5.6-sol"},
    )
    deleted = client.delete(f"/api/v1/forge/resident-runtimes/{runtime_id}/sessions/{session.id}")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(session.id)
    assert created.status_code == 201
    assert created.json()["chatEndpoint"] == session.chat_endpoint
    assert deleted.status_code == 204
    service.list_sessions.assert_awaited_once_with(_PRINCIPAL, runtime_id)
    service.create_session.assert_awaited_once_with(
        _PRINCIPAL,
        runtime_id,
        title="Persistent work",
        model="niuu/gpt-5.6-sol",
    )
    service.delete_session.assert_awaited_once_with(_PRINCIPAL, runtime_id, session.id)


def test_create_and_lifecycle_routes_use_authenticated_service() -> None:
    runtime = ResidentRuntime(
        id=uuid4(),
        owner_id="user-a",
        tenant_id="tenant-a",
        name="Muninn",
        backend=ResidentBackend.HELMRELEASE,
        engine=ResidentEngine.RAVN,
        profile_id="ravn-helm",
    )
    service = Mock()
    service.create = AsyncMock(return_value=runtime)
    service.restart = AsyncMock(return_value=runtime)
    service.set_desired_state = AsyncMock(return_value=runtime)
    service.delete = AsyncMock(return_value=True)
    client = _client(service)

    created = client.post(
        "/api/v1/forge/resident-runtimes",
        json={
            "name": "Muninn",
            "profileId": "ravn-helm",
            "personaName": "product-steward",
            "model": "gpt-5.6",
        },
    )
    restarted = client.post(f"/api/v1/forge/resident-runtimes/{runtime.id}/restart")
    suspended = client.post(f"/api/v1/forge/resident-runtimes/{runtime.id}/suspend")
    resumed = client.post(f"/api/v1/forge/resident-runtimes/{runtime.id}/resume")
    deleted = client.delete(f"/api/v1/forge/resident-runtimes/{runtime.id}")

    assert created.status_code == 201
    assert restarted.status_code == suspended.status_code == resumed.status_code == 200
    assert deleted.status_code == 204
    service.create.assert_awaited_once_with(
        _PRINCIPAL,
        name="Muninn",
        profile_id="ravn-helm",
        persona_name="product-steward",
        model="gpt-5.6",
    )
    service.delete.assert_awaited_once_with(_PRINCIPAL, runtime.id)


def test_delete_is_idempotent_when_record_is_already_absent() -> None:
    service = Mock()
    service.delete = AsyncMock(side_effect=ResidentRuntimeNotFoundError("already absent"))

    response = _client(service).delete(f"/api/v1/forge/resident-runtimes/{uuid4()}")

    assert response.status_code == 204


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ResidentRuntimeConflictError("duplicate"), 409),
        (ResidentRuntimeDeploymentError("flux failed"), 502),
    ],
)
def test_create_maps_domain_failures(error, expected_status) -> None:
    service = Mock()
    service.create = AsyncMock(side_effect=error)

    response = _client(service).post(
        "/api/v1/forge/resident-runtimes",
        json={"name": "Muninn", "profileId": "ravn-helm"},
    )

    assert response.status_code == expected_status
