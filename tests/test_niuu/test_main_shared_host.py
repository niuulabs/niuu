"""Regression tests for the co-hosted Niuu shared API surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import niuu.main as niuu_main
from niuu.config import GitConfig
from niuu.service_settings import Settings


class _DummyGitRegistry:
    async def close(self) -> None:
        return None


class _DummyPATValidator:
    async def is_valid(self, _raw_token: str) -> bool:
        return True


class _DummyTenantService:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def ensure_default_tenant(self) -> None:
        return None


@asynccontextmanager
async def _fake_database_pool(_config) -> AsyncIterator[object]:
    yield object()


def test_create_app_mounts_shared_identity_features_and_personas(monkeypatch) -> None:
    monkeypatch.setattr(niuu_main, "create_git_registry", lambda _cfg: _DummyGitRegistry())
    monkeypatch.setattr(niuu_main, "database_pool", _fake_database_pool)
    monkeypatch.setattr(niuu_main, "create_storage_adapter", lambda _settings: object())
    monkeypatch.setattr(
        niuu_main,
        "create_identity_adapter",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        niuu_main,
        "create_pat_validator",
        lambda *_args, **_kwargs: _DummyPATValidator(),
    )
    monkeypatch.setattr(niuu_main, "create_credential_store", lambda _settings: object())
    monkeypatch.setattr(niuu_main, "release_credential_store", lambda _settings: None)
    monkeypatch.setattr(niuu_main, "TenantService", _DummyTenantService)
    monkeypatch.setattr(
        niuu_main,
        "PostgresIntegrationRepository",
        lambda pool: ("integrations", pool),
    )
    monkeypatch.setattr(niuu_main, "PostgresMappingRepository", lambda pool: ("mappings", pool))
    monkeypatch.setattr(niuu_main, "ConfigMCPServerProvider", lambda _servers: object())
    monkeypatch.setattr(niuu_main, "InMemorySecretManager", lambda: object())
    monkeypatch.setattr(
        niuu_main,
        "CredentialService",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(niuu_main, "SecretMountStrategyRegistry", lambda: object())
    monkeypatch.setattr(
        niuu_main,
        "IntegrationRegistry",
        lambda _definitions: object(),
    )
    monkeypatch.setattr(niuu_main, "definitions_from_config", lambda _definitions: [])
    monkeypatch.setattr(niuu_main, "TrackerFactory", lambda _store: object())
    monkeypatch.setattr(
        niuu_main,
        "TrackerService",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(niuu_main, "seed_configured_integrations", AsyncMock())
    monkeypatch.setattr(niuu_main, "has_seeded_linear_integration", lambda _settings: True)

    app = niuu_main.create_app(
        git_config=GitConfig(),
        settings=Settings(
            auth_discovery={
                "issuer": "https://issuer.example.com",
                "cli_client_id": "volundr-cli",
                "scopes": "openid profile email",
            }
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/identity/auth/config")
        assert response.status_code == 200
        assert response.json()["issuer"] == "https://issuer.example.com"

        paths = {route.path for route in app.routes}
        assert "/api/v1/niuu/repos" in paths
        assert "/api/v1/identity/auth/config" in paths
        assert "/api/v1/features" in paths
        assert "/api/v1/personas" in paths
        assert "/api/v1/ravn/personas" in paths
        assert "/api/v1/tokens" in paths
        assert "/api/v1/credentials/settings" in paths
        assert "/api/v1/credentials/user" in paths
        assert "/api/v1/niuu/credentials/settings" in paths
        assert "/api/v1/niuu/credentials/user" in paths
        assert "/api/v1/integrations/settings" in paths
        assert "/api/v1/integrations" in paths
        assert "/internal/api/v1/integrations" in paths
        assert "/api/v1/integrations/catalog" in paths
        assert "/api/v1/tracker/status" in paths
        assert "/api/v1/tracker/issues" in paths
        assert "/api/v1/tracker/repo-mappings" in paths
        assert "/api/v1/niuu/instances" not in paths
        assert "/api/v1/niuu/volundr/sessions" not in paths
