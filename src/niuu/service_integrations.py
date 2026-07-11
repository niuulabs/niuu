"""Shared integration seeding helpers used across co-hosted service apps."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5
from typing import Any, Protocol

from niuu.domain.models import IntegrationConnection, IntegrationType, SecretType


class IntegrationSettings(Protocol):
    """Settings section required for integration seeding."""

    integrations: Any


async def seed_linear_integration(
    integration_repo: object,
    credential_store: object,
    api_key: str,
) -> None:
    """Seed Linear integration from config into the DB."""
    owner_id = "dev-user"
    credential_name = "linear-config"

    await credential_store.store(
        owner_type="user",
        owner_id=owner_id,
        name=credential_name,
        secret_type=SecretType.API_KEY,
        data={"api_key": api_key},
    )

    connection = IntegrationConnection(
        id="f976d725-2a19-558a-a2d0-99258577f615",
        owner_id=owner_id,
        integration_type=IntegrationType.ISSUE_TRACKER,
        adapter="volundr.adapters.outbound.linear.LinearAdapter",
        credential_name=credential_name,
        config={},
        enabled=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        slug="linear",
    )
    await integration_repo.save_connection(connection)


def seeded_integration_connection_id(
    *,
    owner_type: str,
    owner_id: str,
    integration_type: str,
    adapter: str,
    credential_name: str,
    slug: str,
) -> str:
    """Return a stable UUID for a config-seeded integration connection."""
    value = (
        f"volundr:integration-seed:{owner_type}:{owner_id}:{integration_type}:"
        f"{adapter}:{credential_name}:{slug}"
    )
    return str(uuid5(NAMESPACE_URL, value))


async def seed_configured_integrations(
    integration_repo: object,
    credential_store: object,
    settings: IntegrationSettings,
) -> None:
    """Seed configured integration connections into storage at startup."""
    for seed in settings.integrations.seed_connections:
        if seed.credential is not None:
            await credential_store.store(
                owner_type=seed.owner_type,
                owner_id=seed.owner_id,
                name=seed.credential_name,
                secret_type=seed.credential.secret_type,
                data=seed.credential.data,
                metadata=seed.credential.metadata,
            )

        connection = IntegrationConnection(
            id=seed.id
            or seeded_integration_connection_id(
                owner_type=seed.owner_type,
                owner_id=seed.owner_id,
                integration_type=str(seed.integration_type),
                adapter=seed.adapter,
                credential_name=seed.credential_name,
                slug=seed.slug,
            ),
            owner_id=seed.owner_id,
            integration_type=seed.integration_type,
            adapter=seed.adapter,
            credential_name=seed.credential_name,
            config=seed.config,
            enabled=seed.enabled,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            slug=seed.slug,
        )
        await integration_repo.save_connection(connection)


def has_seeded_linear_integration(settings: IntegrationSettings) -> bool:
    """Return whether config already seeds a Linear issue-tracker connection."""
    for seed in settings.integrations.seed_connections:
        if seed.integration_type != IntegrationType.ISSUE_TRACKER:
            continue
        if seed.slug == "linear":
            return True
        if seed.adapter == "volundr.adapters.outbound.linear.LinearAdapter":
            return True
    return False
