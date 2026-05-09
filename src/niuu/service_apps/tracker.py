"""Shared tracker service app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_integrations import PostgresIntegrationRepository
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.service_database import database_pool
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
from niuu.service_settings import Settings
from volundr.adapters.inbound.rest_issues import create_canonical_issues_router
from volundr.adapters.inbound.rest_tracker import create_canonical_tracker_router
from volundr.adapters.outbound.linear import LinearAdapter
from volundr.adapters.outbound.postgres_mappings import PostgresMappingRepository
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.domain.services.tracker import TrackerService
from volundr.domain.services.tracker_factory import TrackerFactory

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone tracker API application."""
    if settings is None:
        settings = Settings()

    configure_logging(settings.logging)

    app = FastAPI(
        title="Tracker API",
        description="Tracker status, issues, and repo mapping surfaces.",
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
            tracker_factory = TrackerFactory(credential_store)
            integration_repo = PostgresIntegrationRepository(pool)
            mapping_repository = PostgresMappingRepository(pool)
            default_tracker = None

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

            if settings.linear.enabled and settings.linear.api_key:
                default_tracker = LinearAdapter(api_key=settings.linear.api_key)

            tracker_service = TrackerService(
                default_tracker,
                mapping_repository,
                integration_repo=integration_repo,
                tracker_factory=tracker_factory,
            )

            app.state.identity = identity_adapter
            app.state.pat_validator = pat_validator
            app.include_router(create_canonical_tracker_router(tracker_service=tracker_service))
            app.include_router(create_canonical_issues_router(integration_repo, tracker_factory))

            try:
                yield
            finally:
                release_credential_store(settings)

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
