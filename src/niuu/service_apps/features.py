"""Shared features service app factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.service_database import database_pool
from niuu.service_runtime import configure_logging, create_identity_adapter, create_pat_validator
from niuu.service_settings import Settings
from volundr.adapters.inbound.rest_features import create_features_router
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.domain.services.feature import FeatureService


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone features API application."""
    if settings is None:
        settings = Settings()

    configure_logging(settings.logging)

    app = FastAPI(
        title="Features API",
        description="Feature catalog and preference surfaces.",
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
            feature_configs = list(settings.features)

            if settings.local_mounts.mini_mode:
                mini_disabled = {"terminal", "code"}
                for feature_config in feature_configs:
                    if feature_config.key in mini_disabled:
                        feature_config.default_enabled = False

            feature_service = FeatureService(pool, feature_configs)

            app.state.identity = identity_adapter
            app.state.pat_validator = pat_validator
            app.include_router(create_features_router(feature_service))
            yield

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
