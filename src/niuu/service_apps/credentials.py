"""Shared credentials service app factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.rest_credentials_settings import create_credentials_settings_router
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.service_database import database_pool
from niuu.service_runtime import (
    configure_logging,
    create_credential_store,
    create_identity_adapter,
    create_pat_validator,
    release_credential_store,
)
from niuu.service_settings import Settings
from volundr.adapters.inbound.rest_credentials import create_canonical_credentials_router
from volundr.adapters.inbound.rest_secrets import create_canonical_secrets_router
from volundr.adapters.outbound.config_mcp_servers import ConfigMCPServerProvider
from volundr.adapters.outbound.memory_secrets import InMemorySecretManager
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.domain.services.credential import CredentialService
from volundr.domain.services.mount_strategies import SecretMountStrategyRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone credentials API application."""
    if settings is None:
        settings = Settings()

    configure_logging(settings.logging)

    app = FastAPI(
        title="Credentials API",
        description="Credential, secret, and MCP metadata surfaces.",
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
            credential_service = CredentialService(
                store=credential_store,
                strategies=SecretMountStrategyRegistry(),
            )
            mcp_provider = ConfigMCPServerProvider(settings.mcp_servers)
            secret_manager = InMemorySecretManager()

            app.state.identity = identity_adapter
            app.state.pat_validator = pat_validator
            app.include_router(create_credentials_settings_router())
            app.include_router(create_canonical_credentials_router(credential_service))
            app.include_router(create_canonical_secrets_router(mcp_provider, secret_manager))

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
