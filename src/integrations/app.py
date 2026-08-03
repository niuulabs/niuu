"""Shared integrations service app factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.rest_integrations_settings import create_integrations_settings_router
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.service_database import database_pool
from niuu.service_databases import apply_service_database_settings
from niuu.service_integrations import (
    has_seeded_linear_integration,
    seed_configured_integrations,
    seed_linear_integration,
)
from niuu.service_runtime import (
    configure_logging,
    create_credential_store,
    create_identity_adapter,
    create_pat_validator,
    release_credential_store,
)
from volundr.adapters.inbound.rest_integrations import create_canonical_integrations_router
from volundr.adapters.inbound.rest_oauth import create_canonical_oauth_router
from volundr.adapters.outbound.postgres_credential_enrollments import (
    PostgresCredentialEnrollmentRepository,
)
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.composition_builders import _create_credential_enrollment_runner
from volundr.config import Settings
from volundr.domain.services.credential_enrollment import (
    CredentialEnrollmentService,
    reconcile_credential_enrollments_loop,
)
from volundr.domain.services.integration_registry import (
    IntegrationRegistry,
    definitions_from_config,
)
from volundr.domain.services.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone integrations API application."""
    if settings is None:
        settings = Settings()
    settings = apply_service_database_settings(settings, "niuu-shared")

    configure_logging(settings.logging)

    app = FastAPI(
        title="Integrations API",
        description="Integration catalog, connections, and OAuth surfaces.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        settings = app.state.settings
        async with database_pool(settings.database) as pool:
            user_repository = PostgresUserRepository(pool)
            identity_adapter = create_identity_adapter(settings, user_repository)
            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(settings, pat_repository)
            credential_store = create_credential_store(settings)
            integration_repo = PostgresIntegrationRepository(pool)
            integration_registry = IntegrationRegistry(
                definitions_from_config(
                    [definition.model_dump() for definition in settings.integrations.definitions]
                )
            )
            tracker_factory = TrackerFactory(credential_store)
            credential_enrollment_service = CredentialEnrollmentService(
                repository=PostgresCredentialEnrollmentRepository(pool),
                runner=_create_credential_enrollment_runner(settings),
                integration_repository=integration_repo,
                integration_registry=integration_registry,
                credential_store=credential_store,
            )

            if settings.integrations.seed_connections:
                await seed_configured_integrations(
                    integration_repo=integration_repo,
                    credential_store=credential_store,
                    settings=settings,
                )
                logger.info(
                    "Seeded %d integration connection(s) from config",
                    len(settings.integrations.seed_connections),
                )

            if (
                settings.linear.enabled
                and settings.linear.api_key
                and not has_seeded_linear_integration(settings)
            ):
                await seed_linear_integration(
                    integration_repo,
                    credential_store,
                    api_key=settings.linear.api_key,
                )
                logger.info("Linear integration seeded from config")

            app.state.identity = identity_adapter
            app.state.pat_validator = pat_validator
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
                create_canonical_oauth_router(
                    oauth_config=settings.oauth,
                    integration_registry=integration_registry,
                    credential_store=credential_store,
                    integration_repo=integration_repo,
                )
            )

            enrollment_reconcile_task = asyncio.create_task(
                reconcile_credential_enrollments_loop(credential_enrollment_service)
            )
            try:
                yield
            finally:
                enrollment_reconcile_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await enrollment_reconcile_task
                release_credential_store(settings)

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
