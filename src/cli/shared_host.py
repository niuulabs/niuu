"""Application factory for the Niuu shared API."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_credentials_settings import create_credentials_settings_router
from niuu.adapters.inbound.rest_integrations_settings import create_integrations_settings_router
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.adapters.outbound.git_registry import create_git_registry
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.config import GitConfig
from niuu.cors import apply_cors_middleware
from niuu.domain.services.pat import PATService
from niuu.domain.services.repo import RepoService
from niuu.service_database import database_pool
from niuu.service_databases import apply_service_database_settings
from niuu.service_integrations import (
    has_seeded_linear_integration,
    seed_configured_integrations,
    seed_linear_integration,
)
from niuu.service_runtime import (
    create_credential_store,
    create_identity_adapter,
    create_pat_validator,
    create_storage_adapter,
    create_workload_identity_service,
    release_credential_store,
)
from niuu.utils import import_class
from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry
from volundr.adapters.inbound.rest_credentials import create_canonical_credentials_router
from volundr.adapters.inbound.rest_features import create_features_router
from volundr.adapters.inbound.rest_integrations import create_canonical_integrations_router
from volundr.adapters.inbound.rest_issues import create_canonical_issues_router
from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router
from volundr.adapters.inbound.rest_ravn_personas import create_ravn_personas_router
from volundr.adapters.inbound.rest_secrets import create_canonical_secrets_router
from volundr.adapters.inbound.rest_tenants import create_identity_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.linear import LinearAdapter
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.adapters.outbound.postgres_credential_enrollments import (
    PostgresCredentialEnrollmentRepository,
)
from volundr.adapters.outbound.postgres_mappings import PostgresMappingRepository
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.composition_builders import _create_credential_enrollment_runner
from volundr.config import Settings
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.credential_enrollment import (
    CredentialEnrollmentService,
    reconcile_credential_enrollments_loop,
)
from volundr.domain.services.feature import FeatureService
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry
from volundr.domain.services.tenant import TenantService
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


def _load_settings() -> Settings:
    """Load shared-service settings from YAML and environment."""
    return Settings()


def create_app(
    git_config: GitConfig | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Create the Niuu shared FastAPI application.

    Args:
        git_config: Git provider configuration.  When ``None``, loaded
            from the shared YAML / env vars automatically.
        settings: Shared-service settings. When ``None``, loaded from the
            shared YAML / env vars automatically.
    """
    app = FastAPI(
        title="Niuu Shared Services",
        description=(
            "Shared API endpoints — repos, identity, credentials, integrations, tracker,"
            " features, personas, and PATs."
        ),
        version="0.1.0",
    )

    loaded_settings = apply_service_database_settings(settings or _load_settings(), "niuu-shared")
    app.state.settings = loaded_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        cfg = git_config or loaded_settings.git
        git_registry = create_git_registry(cfg)

        async with database_pool(loaded_settings.database) as pool:
            repo_service = RepoService(git_registry)
            user_repository = PostgresUserRepository(pool)
            tenant_repository = PostgresTenantRepository(pool)
            storage_adapter = create_storage_adapter(loaded_settings)
            tenant_service = TenantService(tenant_repository, user_repository)
            identity_adapter = create_identity_adapter(
                loaded_settings,
                user_repository,
                storage=storage_adapter,
                tenant_service=tenant_service,
            )
            await tenant_service.ensure_default_tenant()

            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(loaded_settings, pat_repository)
            token_issuer_cls = import_class(loaded_settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**loaded_settings.pat.token_issuer_kwargs)
            pat_service = PATService(
                repo=pat_repository,
                token_issuer=token_issuer,
                ttl_days=loaded_settings.pat.ttl_days,
                validator=pat_validator,
            )
            credential_store = create_credential_store(loaded_settings)
            credential_service = CredentialService(
                store=credential_store,
                strategies=SecretMountStrategyRegistry(),
            )
            mcp_provider = ConfigMCPServerProvider(loaded_settings.mcp_servers)
            secret_manager = InMemorySecretManager()
            integration_repo = PostgresIntegrationRepository(pool)
            integration_registry = IntegrationRegistry(
                definitions_from_config(
                    [
                        definition.model_dump()
                        for definition in loaded_settings.integrations.definitions
                    ]
                )
            )
            tracker_factory = TrackerFactory(credential_store)
            credential_enrollment_service = CredentialEnrollmentService(
                repository=PostgresCredentialEnrollmentRepository(pool),
                runner=_create_credential_enrollment_runner(loaded_settings),
                integration_repository=integration_repo,
                integration_registry=integration_registry,
                credential_store=credential_store,
            )
            mapping_repository = PostgresMappingRepository(pool)
            default_tracker = None

            if loaded_settings.integrations.seed_connections:
                await seed_configured_integrations(
                    integration_repo=integration_repo,
                    credential_store=credential_store,
                    settings=loaded_settings,
                )
                logger.info(
                    "Seeded %d integration connection(s) from config",
                    len(loaded_settings.integrations.seed_connections),
                )

            if (
                loaded_settings.linear.enabled
                and loaded_settings.linear.api_key
                and not has_seeded_linear_integration(loaded_settings)
            ):
                await seed_linear_integration(
                    integration_repo,
                    credential_store,
                    api_key=loaded_settings.linear.api_key,
                )
                logger.info("Linear integration seeded from config")

            if loaded_settings.linear.enabled and loaded_settings.linear.api_key:
                default_tracker = LinearAdapter(api_key=loaded_settings.linear.api_key)

            tracker_service = TrackerService(
                default_tracker,
                mapping_repository,
                integration_repo=integration_repo,
                tracker_factory=tracker_factory,
            )

            feature_configs = list(loaded_settings.features)
            if loaded_settings.local_mounts.mini_mode:
                mini_disabled = {"terminal", "code"}
                for feature_config in feature_configs:
                    if feature_config.key in mini_disabled:
                        feature_config.default_enabled = False
            feature_service = FeatureService(pool, feature_configs)

            app.state.git_registry = git_registry
            app.state.repo_service = repo_service
            app.state.identity = identity_adapter
            app.state.storage = storage_adapter
            app.state.pat_validator = pat_validator
            app.state.pat_service = pat_service
            app.state.workload_identity_service = create_workload_identity_service(
                loaded_settings.workload_identity
            )
            app.state.persona_registry = PostgresPersonaRegistry(pool)

            app.include_router(create_repos_router(repo_service))
            app.include_router(create_identity_router(tenant_service))
            app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))
            app.include_router(create_credentials_settings_router())
            app.include_router(create_credentials_settings_router("/api/v1/niuu/credentials"))
            app.include_router(create_canonical_credentials_router(credential_service))
            app.include_router(
                create_canonical_credentials_router(
                    credential_service,
                    prefix="/api/v1/niuu/credentials",
                )
            )
            app.include_router(create_canonical_secrets_router(mcp_provider, secret_manager))
            app.include_router(
                create_canonical_secrets_router(
                    mcp_provider,
                    secret_manager,
                    prefix="/api/v1/niuu/credentials",
                )
            )
            app.include_router(create_integrations_settings_router())
            app.include_router(
                create_canonical_integrations_router(
                    integration_repo,
                    tracker_factory,
                    registry=integration_registry,
                    credential_store=credential_store,
                    credential_enrollment_service=credential_enrollment_service,
                )
            )
            app.include_router(
                create_canonical_integrations_router(
                    integration_repo,
                    tracker_factory,
                    prefix="/internal/api/v1/integrations",
                    registry=integration_registry,
                    credential_store=credential_store,
                    credential_enrollment_service=credential_enrollment_service,
                )
            )
            app.include_router(
                create_canonical_oauth_router(
                    oauth_config=loaded_settings.oauth,
                    integration_registry=integration_registry,
                    credential_store=credential_store,
                    integration_repo=integration_repo,
                )
            )
            app.include_router(create_canonical_tracker_router(tracker_service=tracker_service))
            app.include_router(create_canonical_issues_router(integration_repo, tracker_factory))
            app.include_router(create_features_router(feature_service))
            app.include_router(create_ravn_personas_router())

            enrollment_reconcile_task = asyncio.create_task(
                reconcile_credential_enrollments_loop(credential_enrollment_service)
            )
            try:
                yield
            finally:
                enrollment_reconcile_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await enrollment_reconcile_task
                release_credential_store(loaded_settings)
                await git_registry.close()

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, loaded_settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
