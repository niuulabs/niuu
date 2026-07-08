"""Tests for the Guild app surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import guild.app as guild_app
from niuu.service_settings import Settings


class _DummyPATValidator:
    async def is_valid(self, _raw_token: str) -> bool:
        return True


class _DummyInstanceRepository:
    def __init__(self) -> None:
        self.ensure_schema = AsyncMock()


@asynccontextmanager
async def _fake_database_pool(_config) -> AsyncIterator[object]:
    yield object()


def test_create_app_mounts_only_guild_routes(monkeypatch) -> None:
    seed_instances = AsyncMock(return_value=1)
    instance_repo = _DummyInstanceRepository()

    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(guild_app, "PostgresInstanceRepository", lambda _pool: instance_repo)
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", seed_instances)

    app = guild_app.create_app(
        settings=Settings(
            niuu={
                "instances": [
                    {
                        "kind": "volundr",
                        "slug": "guild-alpha",
                        "name": "Guild Alpha",
                        "base_url": "http://guild-alpha:8080",
                        "visibility": "system",
                    }
                ]
            }
        )
    )

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

        paths = set(client.get("/openapi.json").json()["paths"])
        assert "/api/v1/niuu/instances" in paths
        assert "/api/v1/niuu/instances/catalog" in paths
        assert "/api/v1/niuu/targets/volundr" in paths
        assert "/api/v1/niuu/observatory/snapshot" in paths
        assert "/api/v1/forge/sessions" in paths
        assert "/api/v1/ravn/ravens" in paths
        assert "/api/v1/ravn/sessions" in paths
        assert "/api/v1/tokens" not in paths
        assert "/api/v1/features" not in paths
        assert "/api/v1/identity/auth/config" not in paths
        assert "/api/v1/niuu/repos" not in paths

    seed_instances.assert_awaited_once()
    instance_repo.ensure_schema.assert_awaited_once()


def test_create_app_uses_loaded_settings_and_skips_empty_seed(monkeypatch) -> None:
    loaded_settings = Settings()
    seed_instances = AsyncMock(return_value=0)
    instance_repo = _DummyInstanceRepository()

    monkeypatch.setattr(guild_app, "_load_settings", lambda: loaded_settings)
    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(guild_app, "PostgresInstanceRepository", lambda _pool: instance_repo)
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", seed_instances)

    app = guild_app.create_app()

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.json() == {"status": "healthy"}
        assert app.state.settings.database.name == "guild"
        assert loaded_settings.database.name == "volundr"
        assert app.state.pat_validator.__class__ is _DummyPATValidator

    seed_instances.assert_not_awaited()
    instance_repo.ensure_schema.assert_awaited_once()


def test_create_app_seeds_embedded_forge_when_no_workers_configured(monkeypatch) -> None:
    loaded_settings = Settings()
    seed_instances = AsyncMock(return_value=0)
    seed_embedded = AsyncMock()
    instance_repo = _DummyInstanceRepository()

    monkeypatch.setattr(guild_app, "_load_settings", lambda: loaded_settings)
    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(guild_app, "PostgresInstanceRepository", lambda _pool: instance_repo)
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", seed_instances)
    monkeypatch.setattr(guild_app, "_seed_embedded_forge_instance", seed_embedded)

    embedded = guild_app.FastAPI()
    app = guild_app.create_app(embedded_forge_app=embedded)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    seed_instances.assert_not_awaited()
    seed_embedded.assert_awaited_once()
    instance_repo.ensure_schema.assert_awaited_once()
