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


def _mounted_paths(app) -> list[str]:
    """Flatten FastAPI's routing tree, including included-router wrappers."""
    paths: list[str] = []
    for route in app.routes:
        if hasattr(route, "path"):
            paths.append(route.path)
        elif hasattr(route, "original_router"):
            paths.extend(sub.path for sub in route.original_router.routes if hasattr(sub, "path"))
    return paths


class TestWorkflowExecutionRouterMounting:
    """The router surface mirrors which packs are actually configured."""

    def test_workflow_execution_disabled_mounts_neither_router(self) -> None:
        from ting.config import Settings
        from ting.main import create_app

        app = create_app(Settings())
        paths = _mounted_paths(app)

        assert not any("workflow-executions" in path for path in paths)
        assert not any("delivery-executions" in path for path in paths)

    def test_generic_only_mounts_workflow_executions_and_not_delivery(self) -> None:
        from ting.config import Settings
        from ting.main import create_app

        settings = Settings(workflow_execution={"enabled": True, "delivery": {"enabled": False}})
        app = create_app(settings)
        paths = _mounted_paths(app)

        assert any("workflow-executions" in path for path in paths)
        assert not any("delivery-executions" in path for path in paths)

    def test_delivery_enabled_mounts_both_routers(self) -> None:
        from ting.config import Settings
        from ting.main import create_app

        settings = Settings(workflow_execution={"enabled": True, "delivery": {"enabled": True}})
        app = create_app(settings)
        paths = _mounted_paths(app)

        assert any("workflow-executions" in path for path in paths)
        assert any("delivery-executions" in path for path in paths)


def test_guild_registry_unavailable_maps_to_503_with_the_error_as_the_remedy() -> None:
    """A Guild outage must surface as a clear 503, not a generic 500 — the
    old fallback made it indistinguishable from "no connections configured"
    (see .claude/rules/no-fallbacks.md)."""
    import asyncio
    import json

    from ting.adapters.volundr_factory import GuildRegistryUnavailableError
    from ting.config import Settings
    from ting.main import create_app

    app = create_app(Settings())
    handler = app.exception_handlers[GuildRegistryUnavailableError]

    response = asyncio.run(handler(None, GuildRegistryUnavailableError("guild is down")))

    assert response.status_code == 503
    body = json.loads(bytes(response.body))
    assert "guild is down" in body["detail"]


def test_delivery_enabled_without_review_authenticator_raises_with_remedy():
    """Startup fails loudly instead of silently running an unconfigured pack."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from fastapi.testclient import TestClient

    from ting.config import AuthConfig, Settings, WorkflowRepositoryConfig
    from ting.main import create_app

    settings = Settings(
        auth=AuthConfig(allow_anonymous_dev=True),
        workflow_repository=WorkflowRepositoryConfig(
            adapter="ting.adapters.postgres_workflows.PostgresWorkflowRepository",
            kwargs={},
            seed_bundled=True,
        ),
        workflow_execution={"enabled": True, "delivery": {"enabled": True}},
    )
    app = create_app(settings)

    mock_pool = MagicMock()
    mock_pool.fetch = AsyncMock(return_value=[])
    mock_pool.fetchrow = AsyncMock(return_value=None)
    mock_pool.execute = AsyncMock(return_value="INSERT 0 1")
    mock_pool.close = AsyncMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_pool
    mock_pool.transaction.return_value.__aenter__.return_value = None

    with patch("ting.main.database_pool") as mock_db:
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_pool)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)
        with pytest.raises(RuntimeError, match="review_authenticator_adapter"):
            with TestClient(app):
                pytest.fail("Missing delivery review authenticator was accepted")
