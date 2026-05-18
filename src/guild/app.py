"""Application factory for the Guild API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_instances import PostgresInstanceRepository
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.domain.services.instances import InstanceService
from niuu.service_database import database_pool
from niuu.service_instances import seed_configured_instances
from niuu.service_runtime import create_pat_validator
from niuu.service_settings import Settings


def _load_settings() -> Settings:
    """Load Guild settings from YAML and environment."""
    return Settings()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the Guild FastAPI application."""
    app = FastAPI(
        title="Guild",
        description="Shared instance registry, discovery, and Volundr aggregation APIs.",
        version="0.1.0",
    )

    loaded_settings = settings or _load_settings()
    app.state.settings = loaded_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with database_pool(loaded_settings.database) as pool:
            instance_repository = PostgresInstanceRepository(pool)
            instance_service = InstanceService(instance_repository)

            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(loaded_settings, pat_repository)

            app.state.instance_service = instance_service
            app.state.pat_validator = pat_validator

            if loaded_settings.niuu.instances:
                await seed_configured_instances(
                    instance_service,
                    list(loaded_settings.niuu.instances),
                )

            app.include_router(create_instances_router(instance_service))
            app.include_router(create_volundr_router(instance_service))

            yield

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, loaded_settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
