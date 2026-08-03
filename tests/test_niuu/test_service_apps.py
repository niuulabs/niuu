"""Coverage for service-owned application factories."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import APIRouter
from fastapi.testclient import TestClient

from volundr.config import (
    FeatureModuleConfig,
    IntegrationsConfig,
    LinearConfig,
    LocalMountsConfig,
    SeededIntegrationConnectionConfig,
    Settings,
)


def _probe_router(path: str) -> APIRouter:
    router = APIRouter()

    @router.get(path)
    async def _probe() -> dict[str, str]:
        return {"path": path}

    return router


def _dynamic_conflict_router(path: str) -> APIRouter:
    router = APIRouter()

    @router.put(path)
    async def _conflict() -> dict[str, str]:
        return {"path": path}

    return router


@asynccontextmanager
async def _fake_db_pool(_config):
    yield object()


class DummyTokenIssuer:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_identity_service_app_initializes_state_and_routes(monkeypatch) -> None:
    from identity import app as identity_app

    settings = Settings()
    identity_adapter = object()
    storage_adapter = object()
    pat_validator = object()
    pat_service = object()
    ensured: dict[str, bool] = {"value": False}
    captured: dict[str, object] = {}

    class DummyTenantService:
        def __init__(self, tenant_repository: object, user_repository: object) -> None:
            captured["tenant_repository"] = tenant_repository
            captured["user_repository"] = user_repository

        async def ensure_default_tenant(self) -> None:
            ensured["value"] = True

    monkeypatch.setattr(identity_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(identity_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(identity_app, "PostgresUserRepository", lambda pool: ("users", pool))
    monkeypatch.setattr(identity_app, "PostgresTenantRepository", lambda pool: ("tenants", pool))
    monkeypatch.setattr(identity_app, "PostgresPATRepository", lambda pool: ("pats", pool))
    monkeypatch.setattr(identity_app, "create_storage_adapter", lambda _settings: storage_adapter)
    monkeypatch.setattr(
        identity_app,
        "create_identity_adapter",
        lambda _settings, user_repository, storage=None, tenant_service=None: (
            captured.setdefault("identity_user_repository", user_repository),
            captured.setdefault("identity_storage", storage),
            captured.setdefault("identity_tenant_service", tenant_service),
            identity_adapter,
        )[-1],
    )
    monkeypatch.setattr(
        identity_app,
        "create_pat_validator",
        lambda _settings, _repo: pat_validator,
    )
    monkeypatch.setattr(identity_app, "import_class", lambda _path: DummyTokenIssuer)
    monkeypatch.setattr(identity_app, "TenantService", DummyTenantService)
    monkeypatch.setattr(
        identity_app,
        "PATService",
        lambda **kwargs: (captured.setdefault("pat_service_kwargs", kwargs), pat_service)[-1],
    )
    monkeypatch.setattr(
        identity_app,
        "create_identity_router",
        lambda tenant_service=None, **_: (
            captured.setdefault("tenant_service", tenant_service),
            _probe_router("/api/v1/identity/probe"),
        )[-1],
    )
    monkeypatch.setattr(
        identity_app,
        "create_pats_router",
        lambda extract_principal, prefix: (
            captured.setdefault("pats_prefix", prefix),
            captured.setdefault("extract_principal", extract_principal),
            _probe_router("/api/v1/tokens/probe"),
        )[-1],
    )

    app = identity_app.create_app(settings)
    assert app.title == "Identity API"

    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/identity/probe").status_code == 200
        assert client.get("/api/v1/tokens/probe").status_code == 200
        assert app.state.identity is identity_adapter
        assert app.state.storage is storage_adapter
        assert app.state.pat_validator is pat_validator
        assert app.state.pat_service is pat_service

    assert ensured["value"] is True
    assert captured["pats_prefix"] == "/api/v1/tokens"
    assert captured["tenant_service"] is not None
    assert captured["identity_storage"] is storage_adapter
    assert "token_issuer" in captured["pat_service_kwargs"]  # type: ignore[index]


def test_features_service_app_applies_mini_mode_feature_defaults(monkeypatch) -> None:
    from features import app as features_app

    settings = Settings(
        local_mounts=LocalMountsConfig(mini_mode=True),
        features=[
            FeatureModuleConfig(
                key="terminal",
                label="Terminal",
                icon="Terminal",
                scope="user",
                default_enabled=True,
            ),
            FeatureModuleConfig(
                key="code",
                label="Code",
                icon="Code",
                scope="user",
                default_enabled=True,
            ),
            FeatureModuleConfig(
                key="docs",
                label="Docs",
                icon="Book",
                scope="user",
                default_enabled=True,
            ),
        ],
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(features_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(features_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(features_app, "PostgresUserRepository", lambda pool: ("users", pool))
    monkeypatch.setattr(features_app, "PostgresPATRepository", lambda pool: ("pats", pool))
    monkeypatch.setattr(features_app, "create_identity_adapter", lambda _settings, _repo: object())
    monkeypatch.setattr(features_app, "create_pat_validator", lambda _settings, _repo: object())
    monkeypatch.setattr(
        features_app,
        "FeatureService",
        lambda pool, feature_configs: (
            captured.setdefault("feature_configs", feature_configs),
            SimpleNamespace(feature_configs=feature_configs),
        )[-1],
    )
    monkeypatch.setattr(
        features_app,
        "create_features_router",
        lambda feature_service: (
            captured.setdefault("feature_service", feature_service),
            _probe_router("/api/v1/features/probe"),
        )[-1],
    )

    app = features_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/features/probe").status_code == 200

    feature_configs = captured["feature_configs"]
    assert [cfg.default_enabled for cfg in feature_configs if cfg.key == "terminal"] == [False]
    assert [cfg.default_enabled for cfg in feature_configs if cfg.key == "code"] == [False]
    assert [cfg.default_enabled for cfg in feature_configs if cfg.key == "docs"] == [True]


def test_credentials_service_app_releases_credential_store(monkeypatch) -> None:
    from credentials import app as credentials_app

    settings = Settings()
    credential_store = object()
    identity_adapter = object()
    pat_validator = object()
    released: list[Settings] = []
    captured: dict[str, object] = {}

    monkeypatch.setattr(credentials_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(credentials_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(credentials_app, "PostgresUserRepository", lambda pool: ("users", pool))
    monkeypatch.setattr(credentials_app, "PostgresPATRepository", lambda pool: ("pats", pool))
    monkeypatch.setattr(
        credentials_app, "create_identity_adapter", lambda _settings, _repo: identity_adapter
    )
    monkeypatch.setattr(
        credentials_app, "create_pat_validator", lambda _settings, _repo: pat_validator
    )
    monkeypatch.setattr(
        credentials_app, "create_credential_store", lambda _settings: credential_store
    )
    monkeypatch.setattr(
        credentials_app,
        "release_credential_store",
        lambda released_settings: released.append(released_settings),
    )
    monkeypatch.setattr(
        credentials_app,
        "create_canonical_credentials_router",
        lambda credential_service, prefix="/api/v1/credentials": (
            captured.setdefault("credential_service", credential_service),
            _dynamic_conflict_router(f"{prefix}/{{name}}"),
        )[-1],
    )
    monkeypatch.setattr(
        credentials_app,
        "create_canonical_secrets_router",
        lambda mcp_provider, secret_manager, prefix="/api/v1/credentials": (
            captured.setdefault("mcp_provider", mcp_provider),
            captured.setdefault("secret_manager", secret_manager),
            _probe_router(f"{prefix}/secrets-probe"),
        )[-1],
    )
    monkeypatch.setattr(
        credentials_app,
        "create_credentials_settings_router",
        lambda prefix="/api/v1/credentials": _probe_router(f"{prefix}/settings"),
    )

    app = credentials_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/credentials/settings").status_code == 200
        assert client.get("/api/v1/niuu/credentials/settings").status_code == 200
        assert client.get("/api/v1/credentials/secrets-probe").status_code == 200
        assert client.get("/api/v1/niuu/credentials/secrets-probe").status_code == 200
        assert app.state.identity is identity_adapter
        assert app.state.pat_validator is pat_validator

    assert len(released) == 1
    assert released[0].database.name == "niuu_shared"
    assert captured["credential_service"] is not None
    assert captured["mcp_provider"] is not None
    assert captured["secret_manager"] is not None


def test_integrations_service_app_seeds_connections_and_linear(monkeypatch) -> None:
    from integrations import app as integrations_app

    settings = Settings(
        integrations=IntegrationsConfig(
            seed_connections=[
                SeededIntegrationConnectionConfig(
                    owner_id="dev-user",
                    integration_type="messaging",
                    adapter="ting.adapters.telegram_notification.TelegramNotificationAdapter",
                    credential_name="telegram-main",
                    slug="telegram",
                )
            ]
        ),
        linear=LinearConfig(enabled=True, api_key="linear-key"),
    )
    seed_configured = AsyncMock()
    seed_linear = AsyncMock()
    captured: dict[str, object] = {}
    released: list[Settings] = []
    reconciled: list[object] = []

    async def _never_ending_reconcile(service: object) -> None:
        reconciled.append(service)
        await asyncio.Event().wait()

    monkeypatch.setattr(integrations_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(integrations_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(integrations_app, "PostgresUserRepository", lambda pool: ("users", pool))
    monkeypatch.setattr(integrations_app, "PostgresPATRepository", lambda pool: ("pats", pool))
    monkeypatch.setattr(
        integrations_app, "PostgresIntegrationRepository", lambda pool: ("integrations", pool)
    )
    monkeypatch.setattr(
        integrations_app, "create_identity_adapter", lambda _settings, _repo: object()
    )
    monkeypatch.setattr(integrations_app, "create_pat_validator", lambda _settings, _repo: object())
    monkeypatch.setattr(integrations_app, "create_credential_store", lambda _settings: object())
    monkeypatch.setattr(
        integrations_app,
        "release_credential_store",
        lambda released_settings: released.append(released_settings),
    )
    monkeypatch.setattr(integrations_app, "seed_configured_integrations", seed_configured)
    monkeypatch.setattr(integrations_app, "seed_linear_integration", seed_linear)
    monkeypatch.setattr(integrations_app, "has_seeded_linear_integration", lambda _settings: False)
    monkeypatch.setattr(integrations_app, "definitions_from_config", lambda defs: defs)
    monkeypatch.setattr(
        integrations_app,
        "IntegrationRegistry",
        lambda definitions: (
            captured.setdefault("integration_registry", definitions),
            SimpleNamespace(definitions=definitions),
        )[-1],
    )
    monkeypatch.setattr(
        integrations_app,
        "TrackerFactory",
        lambda credential_store: (
            captured.setdefault("tracker_factory_store", credential_store),
            SimpleNamespace(),
        )[-1],
    )
    monkeypatch.setattr(
        integrations_app,
        "PostgresCredentialEnrollmentRepository",
        lambda pool: ("credential-enrollments", pool),
    )
    monkeypatch.setattr(
        integrations_app,
        "_create_credential_enrollment_runner",
        lambda _settings: SimpleNamespace(supports_enrollment=lambda _method: False),
    )
    monkeypatch.setattr(
        integrations_app,
        "CredentialEnrollmentService",
        lambda **kwargs: (
            captured.setdefault("enrollment_service_kwargs", kwargs),
            SimpleNamespace(),
        )[-1],
    )
    monkeypatch.setattr(
        integrations_app,
        "reconcile_credential_enrollments_loop",
        _never_ending_reconcile,
    )

    def _capture_integrations_router(
        integration_repo: object,
        tracker_factory: object,
        registry: object,
        credential_store: object,
        credential_enrollment_service: object,
    ) -> APIRouter:
        captured["integrations_router_repo"] = integration_repo
        captured["integrations_router_registry"] = registry
        captured["integrations_router_enrollment_service"] = credential_enrollment_service
        return _dynamic_conflict_router("/api/v1/integrations/{connection_id}")

    monkeypatch.setattr(
        integrations_app,
        "create_canonical_integrations_router",
        _capture_integrations_router,
    )
    monkeypatch.setattr(
        integrations_app,
        "create_canonical_oauth_router",
        lambda oauth_config, integration_registry, credential_store, integration_repo: (
            captured.setdefault("oauth_router_registry", integration_registry),
            _probe_router("/api/v1/integrations/oauth-probe"),
        )[-1],
    )
    monkeypatch.setattr(
        integrations_app,
        "create_integrations_settings_router",
        lambda: _probe_router("/api/v1/integrations/settings"),
    )

    app = integrations_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/integrations/settings").status_code == 200
        assert client.get("/api/v1/integrations/oauth-probe").status_code == 200

    seed_configured.assert_awaited_once()
    seed_linear.assert_awaited_once()
    assert len(released) == 1
    assert released[0].database.name == "niuu_shared"
    assert captured["integrations_router_repo"][0] == "integrations"  # type: ignore[index]
    # The shared integrations API owns interactive enrollment: without the service
    # the Codex device login answers 503 no matter how the cluster is configured.
    assert captured["integrations_router_enrollment_service"] is not None
    enrollment_kwargs = captured["enrollment_service_kwargs"]
    assert enrollment_kwargs["repository"][0] == "credential-enrollments"  # type: ignore[index]
    assert enrollment_kwargs["integration_repository"][0] == "integrations"  # type: ignore[index]
    assert reconciled == [captured["integrations_router_enrollment_service"]]


def test_tracker_service_app_uses_linear_default_tracker(monkeypatch) -> None:
    from tracker import app as tracker_app

    settings = Settings(linear=LinearConfig(enabled=True, api_key="linear-key"))
    seed_linear = AsyncMock()
    captured: dict[str, object] = {}
    released: list[Settings] = []

    monkeypatch.setattr(tracker_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(tracker_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(tracker_app, "PostgresUserRepository", lambda pool: ("users", pool))
    monkeypatch.setattr(tracker_app, "PostgresPATRepository", lambda pool: ("pats", pool))
    monkeypatch.setattr(
        tracker_app,
        "PostgresIntegrationRepository",
        lambda pool: ("integrations", pool),
    )
    monkeypatch.setattr(tracker_app, "PostgresMappingRepository", lambda pool: ("mappings", pool))
    monkeypatch.setattr(tracker_app, "create_identity_adapter", lambda _settings, _repo: object())
    monkeypatch.setattr(tracker_app, "create_pat_validator", lambda _settings, _repo: object())
    monkeypatch.setattr(tracker_app, "create_credential_store", lambda _settings: object())
    monkeypatch.setattr(
        tracker_app,
        "release_credential_store",
        lambda released_settings: released.append(released_settings),
    )
    monkeypatch.setattr(tracker_app, "seed_configured_integrations", AsyncMock())
    monkeypatch.setattr(tracker_app, "seed_linear_integration", seed_linear)
    monkeypatch.setattr(tracker_app, "has_seeded_linear_integration", lambda _settings: False)
    monkeypatch.setattr(
        tracker_app,
        "TrackerFactory",
        lambda credential_store: (
            captured.setdefault("tracker_factory_store", credential_store),
            SimpleNamespace(),
        )[-1],
    )
    monkeypatch.setattr(
        tracker_app,
        "LinearAdapter",
        lambda api_key: (captured.setdefault("linear_api_key", api_key), SimpleNamespace())[-1],
    )
    monkeypatch.setattr(
        tracker_app,
        "TrackerService",
        lambda default_tracker, mapping_repository, integration_repo, tracker_factory: (
            captured.setdefault("default_tracker", default_tracker),
            captured.setdefault("mapping_repository", mapping_repository),
            SimpleNamespace(),
        )[-1],
    )
    monkeypatch.setattr(
        tracker_app,
        "create_canonical_tracker_router",
        lambda tracker_service: _probe_router("/api/v1/tracker/probe"),
    )
    monkeypatch.setattr(
        tracker_app,
        "create_canonical_issues_router",
        lambda integration_repo, tracker_factory: _probe_router("/api/v1/tracker/issues-probe"),
    )

    app = tracker_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/tracker/probe").status_code == 200
        assert client.get("/api/v1/tracker/issues-probe").status_code == 200

    seed_linear.assert_awaited_once()
    assert captured["linear_api_key"] == "linear-key"
    assert captured["default_tracker"] is not None
    assert len(released) == 1
    assert released[0].database.name == "niuu_shared"


def test_audit_service_app_starts_and_stops_subscriber(monkeypatch) -> None:
    from audit import app as audit_app

    settings = Settings()
    settings.sleipnir.enabled = True
    subscriber = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    captured: dict[str, object] = {}

    monkeypatch.setattr(audit_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(audit_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(audit_app, "PostgresAuditRepository", lambda pool: ("audit-repo", pool))
    monkeypatch.setattr(
        audit_app,
        "import_class",
        lambda _path: lambda **kwargs: {"kwargs": kwargs},
    )
    monkeypatch.setattr(
        audit_app,
        "AuditSubscriber",
        lambda sleipnir_bus, audit_repository: (
            captured.setdefault("sleipnir_bus", sleipnir_bus),
            captured.setdefault("audit_repository", audit_repository),
            subscriber,
        )[-1],
    )
    monkeypatch.setattr(
        audit_app,
        "create_canonical_audit_router",
        lambda audit_repository: _probe_router("/api/v1/audit/probe"),
    )
    monkeypatch.setattr(
        audit_app,
        "create_audit_router",
        lambda audit_repository: _probe_router("/api/v1/audit/legacy-probe"),
    )

    app = audit_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/audit/probe").status_code == 200
        assert client.get("/api/v1/audit/legacy-probe").status_code == 200

    subscriber.start.assert_awaited_once()
    subscriber.stop.assert_awaited_once()


def test_audit_service_app_swallows_subscriber_start_failures(monkeypatch) -> None:
    from audit import app as audit_app

    settings = Settings()
    settings.sleipnir.enabled = True
    subscriber = SimpleNamespace(
        start=AsyncMock(side_effect=RuntimeError("boom")),
        stop=AsyncMock(),
    )

    monkeypatch.setattr(audit_app, "database_pool", _fake_db_pool)
    monkeypatch.setattr(audit_app, "configure_logging", lambda _logging: None)
    monkeypatch.setattr(audit_app, "PostgresAuditRepository", lambda pool: ("audit-repo", pool))
    monkeypatch.setattr(
        audit_app,
        "import_class",
        lambda _path: lambda **kwargs: {"kwargs": kwargs},
    )
    monkeypatch.setattr(audit_app, "AuditSubscriber", lambda *_args: subscriber)
    monkeypatch.setattr(
        audit_app,
        "create_canonical_audit_router",
        lambda audit_repository: _probe_router("/api/v1/audit/probe"),
    )
    monkeypatch.setattr(
        audit_app,
        "create_audit_router",
        lambda audit_repository: _probe_router("/api/v1/audit/legacy-probe"),
    )

    app = audit_app.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}

    subscriber.start.assert_awaited_once()
    subscriber.stop.assert_awaited_once()
