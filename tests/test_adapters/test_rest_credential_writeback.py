"""Sessions may hand a rotated sign-in credential back, only for their own enrollment field."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from volundr.adapters.inbound.auth import extract_principal
from volundr.adapters.inbound.rest_credential_writeback import (
    create_credential_writeback_router,
)
from volundr.adapters.outbound.memory_integrations import InMemoryIntegrationRepository
from volundr.config import _default_integration_definitions
from volundr.domain.models import IntegrationConnection, IntegrationType, Principal, SecretType
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)

ROUTE = "/api/v1/internal/credentials/writeback"


class _CredentialStore:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str, str], dict] = {}

    async def get(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        if item is None:
            return None
        return SimpleNamespace(secret_type=item["secret_type"], metadata=item["metadata"])

    async def get_value(self, owner_type, owner_id, name):
        item = self.items.get((owner_type, owner_id, name))
        return dict(item["data"]) if item is not None else None

    async def store(self, owner_type, owner_id, name, secret_type, data, metadata=None):
        self.items[(owner_type, owner_id, name)] = {
            "secret_type": secret_type,
            "data": dict(data),
            "metadata": dict(metadata or {}),
        }
        return await self.get(owner_type, owner_id, name)


def _connection(slug: str, name: str, owner: str = "owner-1") -> IntegrationConnection:
    now = datetime.now(UTC)
    return IntegrationConnection(
        id=f"{owner}-{name}",
        owner_id=owner,
        integration_type=IntegrationType.AI_PROVIDER,
        adapter="x",
        credential_name=name,
        config={},
        enabled=True,
        created_at=now,
        updated_at=now,
        slug=slug,
    )


def _harness(*, user_id: str = "owner-1"):
    repo = InMemoryIntegrationRepository()
    store = _CredentialStore()
    registry = IntegrationRegistry(
        definitions_from_config([d.model_dump() for d in _default_integration_definitions()])
    )
    app = FastAPI()
    app.include_router(
        create_credential_writeback_router(
            integration_repository=repo, integration_registry=registry, credential_store=store
        )
    )
    app.dependency_overrides[extract_principal] = lambda: Principal(
        user_id=user_id, tenant_id="tenant-1", email="o@example.test", roles=["volundr:developer"]
    )
    return TestClient(app), repo, store


async def _seed_grok(repo, store, owner="owner-1", name="grok-credentials") -> None:
    await repo.save_connection(_connection("grok-build", name, owner))
    await store.store(
        "user",
        owner,
        name,
        SecretType.OAUTH_TOKEN,
        {"auth.json": '{"access_token": "old"}'},
        {"source": "credential_enrollment", "auth_state": "auth_required", "auth_error_code": "x"},
    )


def test_stores_the_rotated_file_on_the_callers_own_credential() -> None:
    client, repo, store = _harness()
    asyncio.run(_seed_grok(repo, store))

    response = client.post(
        ROUTE,
        json={
            "credential_name": "grok-credentials",
            "credential_field": "auth.json",
            "value": '{"access_token": "rotated"}',
        },
    )

    assert response.status_code == 200
    assert response.json() == {"stored": True, "credential_name": "grok-credentials"}
    item = store.items[("user", "owner-1", "grok-credentials")]
    assert item["data"] == {"auth.json": '{"access_token": "rotated"}'}
    assert item["metadata"]["auth_state"] == "active"
    assert "auth_error_code" not in item["metadata"]
    assert item["metadata"]["source"] == "credential_enrollment"


def test_an_unchanged_file_is_not_rewritten() -> None:
    client, repo, store = _harness()
    asyncio.run(_seed_grok(repo, store))

    response = client.post(
        ROUTE,
        json={
            "credential_name": "grok-credentials",
            "credential_field": "auth.json",
            "value": '{"access_token": "old"}',
        },
    )

    assert response.status_code == 200
    assert response.json()["stored"] is False
    assert store.items[("user", "owner-1", "grok-credentials")]["metadata"]["auth_state"] == (
        "auth_required"
    )


def test_refuses_other_users_credentials_and_foreign_fields() -> None:
    client, repo, store = _harness(user_id="owner-2")
    asyncio.run(_seed_grok(repo, store))  # belongs to owner-1

    other = client.post(
        ROUTE,
        json={"credential_name": "grok-credentials", "credential_field": "auth.json", "value": "x"},
    )
    assert other.status_code == 404

    client, repo, store = _harness()
    asyncio.run(_seed_grok(repo, store))
    wrong_field = client.post(
        ROUTE,
        json={"credential_name": "grok-credentials", "credential_field": "token", "value": "x"},
    )
    assert wrong_field.status_code == 422
    assert store.items[("user", "owner-1", "grok-credentials")]["data"] == {
        "auth.json": '{"access_token": "old"}'
    }


def test_refuses_credentials_that_were_not_enrolled() -> None:
    client, repo, store = _harness()

    async def seed() -> None:
        await repo.save_connection(_connection("anthropic", "anthropic-key"))
        await store.store("user", "owner-1", "anthropic-key", SecretType.API_KEY, {"api_key": "k"})

    asyncio.run(seed())

    response = client.post(
        ROUTE,
        json={"credential_name": "anthropic-key", "credential_field": "api_key", "value": "new"},
    )

    assert response.status_code == 422
    assert store.items[("user", "owner-1", "anthropic-key")]["data"] == {"api_key": "k"}
