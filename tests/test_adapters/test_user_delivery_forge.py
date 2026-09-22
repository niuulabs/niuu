"""Per-principal dynamic forge resolution tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.delivery import ResolvedRef
from niuu.domain.models import Principal
from niuu.ports.delivery import DeliveryForgeProvider
from volundr.adapters.outbound.user_delivery_forge import UserDeliveryForgeProvider


def _principal() -> Principal:
    return Principal(
        user_id="owner-1",
        email="owner@example.invalid",
        tenant_id="tenant-1",
        roles=["volundr:developer"],
    )


@pytest.mark.asyncio
async def test_resolves_configured_provider_fresh_for_principal() -> None:
    repository = "https://gitlab.example/org/repo"
    resolved = ResolvedRef(
        provider="gitlab",
        repository=repository,
        ref="main",
        sha="a" * 40,
        observed_at=datetime.now(UTC),
    )
    provider = MagicMock(spec=DeliveryForgeProvider)
    provider.resolve_ref = AsyncMock(return_value=resolved)
    integrations = MagicMock()
    integrations.find_git_provider_for = AsyncMock(return_value=provider)
    facade = UserDeliveryForgeProvider(integrations, _principal())
    assert await facade.resolve_ref(repository, "main") == resolved
    integrations.find_git_provider_for.assert_awaited_once_with(repository, "owner-1")
    provider.resolve_ref.assert_awaited_once_with(repository, "main")


@pytest.mark.asyncio
async def test_missing_or_non_strict_provider_is_rejected() -> None:
    integrations = MagicMock()
    integrations.find_git_provider_for = AsyncMock(return_value=None)
    facade = UserDeliveryForgeProvider(integrations, _principal())
    with pytest.raises(ValueError, match="No source-control integration"):
        await facade.resolve_ref("https://gitlab.example/org/repo", "main")
    integrations.find_git_provider_for.return_value = object()
    with pytest.raises(ValueError, match="lacks strict delivery support"):
        await facade.resolve_ref("https://gitlab.example/org/repo", "main")


def test_requires_user_and_tenant_identity() -> None:
    with pytest.raises(ValueError, match="user and tenant"):
        UserDeliveryForgeProvider(
            MagicMock(),
            Principal(user_id="", email="", tenant_id="", roles=[]),
        )
