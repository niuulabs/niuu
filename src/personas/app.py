"""Application factory for the standalone persona registry API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry
from volundr.adapters.inbound.rest_ravn_personas import create_ravn_personas_router
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.config import Settings
from volundr.domain.services import TenantService
from volundr.infrastructure.database import database_pool
from volundr.main import (
    _create_authorization_adapter,
    _create_identity_adapter,
    _create_storage_adapter,
    configure_logging,
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and return the persona registry FastAPI app."""
    if settings is None:
        settings = Settings()

    configure_logging(settings.logging)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        config: Settings = app.state.settings
        async with database_pool(config.database) as pool:
            tenant_repository = PostgresTenantRepository(pool)
            user_repository = PostgresUserRepository(pool)
            storage_adapter = _create_storage_adapter(config)
            identity_adapter = _create_identity_adapter(
                config,
                user_repository,
                storage=storage_adapter,
            )
            authorization_adapter = _create_authorization_adapter(config)

            app.state.identity = identity_adapter
            app.state.authorization = authorization_adapter
            app.state.persona_registry = PostgresPersonaRegistry(pool)

            tenant_service = TenantService(tenant_repository, user_repository)
            await tenant_service.ensure_default_tenant()

            yield

    app = FastAPI(
        title="Persona Registry API",
        description="Canonical persona registry surface for Ravn, Tyr, and runtime consumers.",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.include_router(create_ravn_personas_router())
    return app
