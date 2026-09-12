"""Shared connection persistence and session ownership regression tests."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from volundr.composition_builders import integration_database_pool
from volundr.config import Settings
from volundr.domain.services.session import SessionService


@pytest.mark.parametrize("name", ["", "volundr"])
async def test_standalone_reuses_service_pool(name):
    settings = Settings(database={"name": "volundr"}, integrations={"database_name": name})
    service_pool = object()
    async with integration_database_pool(settings, service_pool) as pool:
        assert pool is service_pool


async def test_shared_pool_preserves_server_configuration_and_closes(monkeypatch):
    settings = Settings(integrations={"database_name": "niuu_shared"})
    shared_pool = object()
    observed = []

    @asynccontextmanager
    async def database_pool(config):
        observed.append(config)
        try:
            yield shared_pool
        finally:
            observed.append("closed")

    monkeypatch.setattr("niuu.service_database.database_pool", database_pool)
    with pytest.raises(RuntimeError, match="session failure"):
        async with integration_database_pool(settings, object()) as pool:
            assert pool is shared_pool
            raise RuntimeError("session failure")
    assert observed[0] == settings.database.model_copy(update={"name": "niuu_shared"})
    assert observed[1] == "closed"


async def test_foreign_connection_rejected_before_session_launch():
    repository = AsyncMock()
    pods = AsyncMock()
    connections = AsyncMock()
    connections.get_connection.return_value = SimpleNamespace(enabled=True, owner_id="other")
    contributor = AsyncMock()
    service = SessionService(
        repository, pods, integration_repo=connections, contributors=[contributor]
    )
    with pytest.raises(ValueError, match="Integration connection not found"):
        await service._start_with_pipeline(
            SimpleNamespace(),
            SimpleNamespace(user_id="owner"),
            None,
            None,
            False,
            integration_ids=["foreign"],
        )
    contributor.contribute.assert_not_awaited()
    assert not pods.mock_calls


@pytest.mark.parametrize("connection", [None, SimpleNamespace(enabled=False)])
async def test_missing_or_disabled_selection_is_not_silently_dropped(connection):
    integrations = AsyncMock()
    integrations.get_connection.return_value = connection
    pods = AsyncMock()
    service = SessionService(AsyncMock(), pods, integration_repo=integrations)
    with pytest.raises(ValueError, match="missing or disabled"):
        await service._start_with_pipeline(
            SimpleNamespace(), None, None, None, False, integration_ids=["selected"]
        )
    pods.start.assert_not_called()
