"""Shared identity service app factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from identity.service import TenantService
from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.domain.services.pat import PATService
from niuu.service_database import database_pool
from niuu.service_databases import apply_service_database_settings
from niuu.service_runtime import (
    configure_logging,
    create_identity_adapter,
    create_pat_validator,
    create_storage_adapter,
    create_workload_identity_service,
)
from niuu.utils import import_class
from volundr.adapters.inbound.rest_tenants import create_identity_router
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone identity API application."""
    if settings is None:
        settings = Settings()
    settings = apply_service_database_settings(settings, "niuu-shared")

    configure_logging(settings.logging)

    app = FastAPI(
        title="Identity API",
        description="Identity, tenancy, and token surfaces.",
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
            tenant_repository = PostgresTenantRepository(pool)
            storage_adapter = create_storage_adapter(settings)
            tenant_service = TenantService(tenant_repository, user_repository)
            identity_adapter = create_identity_adapter(
                settings,
                user_repository,
                storage=storage_adapter,
                tenant_service=tenant_service,
            )
            await tenant_service.ensure_default_tenant()
            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(settings, pat_repository)
            token_issuer_cls = import_class(settings.pat.token_issuer_adapter)
            token_issuer = token_issuer_cls(**settings.pat.token_issuer_kwargs)
            pat_service = PATService(
                repo=pat_repository,
                token_issuer=token_issuer,
                ttl_days=settings.pat.ttl_days,
                validator=pat_validator,
            )

            app.state.identity = identity_adapter
            app.state.storage = storage_adapter
            app.state.pat_validator = pat_validator
            app.state.pat_service = pat_service
            app.state.workload_identity_service = create_workload_identity_service(
                settings.workload_identity
            )
            app.include_router(create_identity_router(tenant_service))
            app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))
            yield

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
