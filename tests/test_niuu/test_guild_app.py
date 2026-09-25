"""Tests for the Guild app surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import guild.app as guild_app
from niuu.config import InstanceSeedConfig
from niuu.domain.models import InstanceKind, InstanceVisibility
from volundr.config import Settings


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


def test_create_app_threads_dev_identity_to_the_ravn_session_proxy(monkeypatch) -> None:
    from fastapi import APIRouter

    received: list[bool] = []

    def _router(_service, *, embedded_forge_app=None, dev_identity=False):
        received.append(dev_identity)
        return APIRouter()

    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(
        guild_app, "PostgresInstanceRepository", lambda _pool: _DummyInstanceRepository()
    )
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", AsyncMock(return_value=0))
    monkeypatch.setattr(guild_app, "create_ravn_session_proxy_router", _router)

    for dev_identity in (False, True):
        with TestClient(guild_app.create_app(settings=Settings(), dev_identity=dev_identity)):
            pass

    assert received == [False, True]


def test_instance_health_config_rejects_non_positive_interval_and_timeout() -> None:
    import pydantic

    from niuu.config import InstanceHealthConfig, InstanceProbeConfig

    with pytest.raises(pydantic.ValidationError):
        InstanceHealthConfig(interval_seconds=0)
    with pytest.raises(pydantic.ValidationError):
        InstanceHealthConfig(interval_seconds=-1)
    with pytest.raises(pydantic.ValidationError):
        InstanceProbeConfig(timeout_seconds=0)


def test_build_instance_probe_imports_the_configured_adapter_and_injects_embedded_app() -> None:
    """The probe is a dynamic adapter (.claude/rules/dynamic-adapters.md):
    config names the class, every other key is a kwarg, and embedded_app is
    injected by the composition root rather than configured."""
    from niuu.adapters.outbound.http_instance_probe import HttpInstanceProbeAdapter
    from niuu.config import InstanceProbeConfig

    embedded = guild_app.FastAPI()
    probe = guild_app._build_instance_probe(
        InstanceProbeConfig(timeout_seconds=7.5), embedded_app=embedded
    )

    assert isinstance(probe, HttpInstanceProbeAdapter)
    assert probe._timeout_seconds == 7.5
    assert probe._embedded_app is embedded


def test_build_instance_probe_honours_a_different_configured_adapter_class() -> None:
    class _StubProbe:
        def __init__(
            self,
            *,
            embedded_app=None,
            timeout_seconds: float = 1.0,
            health_paths: dict | None = None,
        ) -> None:
            self.embedded_app = embedded_app
            self.timeout_seconds = timeout_seconds
            self.health_paths = health_paths or {}

    import niuu.config as niuu_config_module

    setattr(niuu_config_module, "_StubProbeForTest", _StubProbe)
    try:
        from niuu.config import InstanceProbeConfig

        probe = guild_app._build_instance_probe(
            InstanceProbeConfig(
                adapter="niuu.config._StubProbeForTest",
                timeout_seconds=2.0,
            ),
            embedded_app=None,
        )
        assert isinstance(probe, _StubProbe)
        assert probe.timeout_seconds == 2.0
    finally:
        delattr(niuu_config_module, "_StubProbeForTest")


def _seed(
    *,
    is_default: bool = False,
    kind: InstanceKind = InstanceKind.VOLUNDR,
    slug: str = "remote",
) -> InstanceSeedConfig:
    return InstanceSeedConfig(
        kind=kind,
        slug=slug,
        name="Remote Forge",
        base_url="https://remote.example.com",
        visibility=InstanceVisibility.SYSTEM,
        is_default=is_default,
    )


class _RecordingInstanceService:
    def __init__(self) -> None:
        self.upsert_calls: list[dict] = []

    async def upsert_seed_instance(self, **kwargs) -> None:
        self.upsert_calls.append(kwargs)


class TestEmbeddedForgeDefaultOwnership:
    """Local Forge must not silently outrank an operator's configured
    default — list_visible sorts default-before-name, so an unconditional
    is_default=True on the embedded seed would beat a configured default
    remote on every restart."""

    async def test_embedded_forge_is_default_when_no_configured_volundr_claims_it(self) -> None:
        service = _RecordingInstanceService()

        await guild_app._seed_embedded_forge_instance(service, [_seed(is_default=False)])

        assert service.upsert_calls[-1]["is_default"] is True

    async def test_embedded_forge_defers_to_a_configured_default_volundr(self) -> None:
        service = _RecordingInstanceService()

        await guild_app._seed_embedded_forge_instance(service, [_seed(is_default=True)])

        assert service.upsert_calls[-1]["is_default"] is False

    async def test_a_default_seed_of_another_kind_does_not_count(self) -> None:
        """Only a Volundr seed's is_default matters — a default Mimir or
        Bifrost entry has no bearing on which Volundr instance is default."""
        service = _RecordingInstanceService()

        await guild_app._seed_embedded_forge_instance(
            service, [_seed(is_default=True, kind=InstanceKind.MIMIR)]
        )

        assert service.upsert_calls[-1]["is_default"] is True

    def test_has_configured_default_volundr_checks_kind_and_flag(self) -> None:
        assert guild_app._has_configured_default_volundr([]) is False
        assert guild_app._has_configured_default_volundr([_seed(is_default=False)]) is False
        assert guild_app._has_configured_default_volundr([_seed(is_default=True)]) is True
        assert (
            guild_app._has_configured_default_volundr(
                [_seed(is_default=True, kind=InstanceKind.TING)]
            )
            is False
        )


def test_create_app_skips_the_embedded_seed_when_the_operator_names_their_own_local_slug(
    monkeypatch,
) -> None:
    """An operator who deliberately configures their own `local`-slugged
    Volundr entry owns that identity — the embedded seed must not
    overwrite it back to the embedded transport on the next restart."""
    seed_instances = AsyncMock(return_value=1)
    seed_embedded = AsyncMock()
    instance_repo = _DummyInstanceRepository()

    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(guild_app, "PostgresInstanceRepository", lambda _pool: instance_repo)
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", seed_instances)
    monkeypatch.setattr(guild_app, "_seed_embedded_forge_instance", seed_embedded)

    embedded = guild_app.FastAPI()
    app = guild_app.create_app(
        settings=Settings(
            niuu={
                "instances": [
                    {
                        "kind": "volundr",
                        "slug": "local",
                        "name": "My Own Local Forge",
                        "base_url": "http://127.0.0.1:9090",
                        "visibility": "system",
                    }
                ]
            }
        ),
        embedded_forge_app=embedded,
    )

    with TestClient(app):
        pass

    seed_instances.assert_awaited_once()
    seed_embedded.assert_not_awaited()


def test_create_app_seeds_embedded_forge_alongside_other_configured_instances(
    monkeypatch,
) -> None:
    """A configured remote (under a different slug) and the embedded Local
    Forge are independent — both get seeded, and the embedded one still
    computes its own is_default from what was configured."""
    seed_instances = AsyncMock(return_value=1)
    instance_repo = _DummyInstanceRepository()
    recording_service = _RecordingInstanceService()

    monkeypatch.setattr(guild_app, "database_pool", _fake_database_pool)
    monkeypatch.setattr(guild_app, "PostgresInstanceRepository", lambda _pool: instance_repo)
    monkeypatch.setattr(guild_app, "PostgresPATRepository", lambda _pool: object())
    monkeypatch.setattr(guild_app, "create_pat_validator", lambda *_args: _DummyPATValidator())
    monkeypatch.setattr(guild_app, "seed_configured_instances", seed_instances)
    monkeypatch.setattr(guild_app, "InstanceService", lambda *_a, **_k: recording_service)

    embedded = guild_app.FastAPI()
    app = guild_app.create_app(
        settings=Settings(
            niuu={
                "instances": [
                    {
                        "kind": "volundr",
                        "slug": "remote-alpha",
                        "name": "Remote Alpha",
                        "base_url": "http://remote-alpha:8080",
                        "visibility": "system",
                        "is_default": True,
                    }
                ]
            }
        ),
        embedded_forge_app=embedded,
    )

    with TestClient(app):
        pass

    seed_instances.assert_awaited_once()
    # The embedded seed still ran (a different slug than "local"), and
    # deferred is_default to the configured Remote Alpha.
    embedded_calls = [c for c in recording_service.upsert_calls if c.get("slug") == "local"]
    assert len(embedded_calls) == 1
    assert embedded_calls[0]["is_default"] is False
