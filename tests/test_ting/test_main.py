"""Focused tests for Ting application wiring helpers."""

from __future__ import annotations

import pytest

from ravn.ports.persona import PersonaPort
from ting.api.persona_names import build_persona_names_dependency


class _StubPersonaSource(PersonaPort):
    def __init__(self, names: list[str], *, should_raise: bool = False) -> None:
        self._names = names
        self._should_raise = should_raise

    def load(self, name: str):
        return None

    def list_names(self) -> list[str]:
        if self._should_raise:
            raise RuntimeError("boom")
        return self._names


class TestBuildPersonaNamesDependency:
    @pytest.mark.asyncio
    async def test_returns_names_from_persona_source(self) -> None:
        dependency = build_persona_names_dependency(_StubPersonaSource(["reviewer", "coordinator"]))

        assert await dependency() == {"reviewer", "coordinator"}

    @pytest.mark.asyncio
    async def test_none_source_returns_empty_set(self) -> None:
        dependency = build_persona_names_dependency(None)

        assert await dependency() == set()

    @pytest.mark.asyncio
    async def test_source_error_returns_empty_set(self) -> None:
        dependency = build_persona_names_dependency(_StubPersonaSource([], should_raise=True))

        assert await dependency() == set()


async def test_shared_integrations_database_closes_separate_pool(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from ting import main
    from ting.config import Settings

    configured = []
    shared_pool = object()

    @asynccontextmanager
    async def pool(config):
        configured.append(config.name)
        yield shared_pool
        configured.append("closed")

    repo = MagicMock()
    monkeypatch.setattr(main, "database_pool", pool)
    monkeypatch.setattr(main, "PostgresIntegrationRepository", repo)
    settings = Settings(shared_integrations={"database_name": "niuu_shared"})
    async with main._integration_repository(settings, object()):
        repo.assert_called_once_with(shared_pool)
        assert configured == ["niuu_shared"]
    assert configured == ["niuu_shared", "closed"]
    assert settings.database.name == "ting"


async def test_shared_integrations_rejects_unauthenticated_production_http():
    from ting.config import Settings
    from ting.main import _integration_repository

    settings = Settings(
        auth={"allow_anonymous_dev": False},
        shared_integrations={"base_url": "https://identity.test"},
    )
    with pytest.raises(ValueError, match="HTTP auth adapter"):
        async with _integration_repository(settings, object()):
            pytest.fail("Unauthenticated production HTTP was accepted")


async def test_shared_integrations_forwards_configured_auth_and_closes(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from ting import main
    from ting.config import Settings

    repository = AsyncMock()
    constructor = MagicMock(return_value=repository)
    monkeypatch.setattr(main, "HTTPIntegrationRepository", constructor)
    settings = Settings(
        shared_integrations={
            "base_url": "https://identity.test",
            "auth": {"adapter": "niuu.adapters.outbound.http_auth.RequestBearerTokenAuthAdapter"},
        },
    )
    async with main._integration_repository(settings, object()):
        assert (
            constructor.call_args.kwargs["auth_adapter"]
            == settings.shared_integrations.auth.adapter
        )
    repository.close.assert_awaited_once()


async def test_shared_integrations_rejects_ambiguous_store():
    from ting.config import Settings
    from ting.main import _integration_repository

    settings = Settings(
        shared_integrations={"base_url": "https://identity.test", "database_name": "shared"}
    )
    with pytest.raises(ValueError, match="not both"):
        async with _integration_repository(settings, object()):
            pytest.fail("Ambiguous integration store accepted")
