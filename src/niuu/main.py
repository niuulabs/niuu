"""Application factory for the Niuu shared API."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.adapters.inbound.auth import extract_principal
from niuu.adapters.inbound.rest_pats import create_pats_router
from niuu.adapters.inbound.rest_repos import create_repos_router
from niuu.adapters.outbound.git_registry import create_git_registry
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.config import GitConfig
from niuu.cors import apply_cors_middleware
from niuu.domain.services.pat import PATService
from niuu.domain.services.repo import RepoService
from niuu.service_database import database_pool
from niuu.service_runtime import (
    create_identity_adapter,
    create_pat_validator,
    create_storage_adapter,
)
from niuu.service_settings import Settings
from niuu.utils import import_class
from ravn.adapters.personas.postgres_registry import PostgresPersonaRegistry
from volundr.adapters.inbound.rest_features import create_features_router
from volundr.adapters.inbound.rest_ravn_personas import create_ravn_personas_router
from volundr.adapters.inbound.rest_tenants import create_identity_router
from volundr.adapters.outbound.postgres_tenants import PostgresTenantRepository
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.domain.services.feature import FeatureService
from volundr.domain.services.tenant import TenantService

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
        description="Shared API endpoints — repos, identity, features, personas, and PATs.",
        version="0.1.0",
    )

    loaded_settings = settings or _load_settings()
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
            app.state.persona_registry = PostgresPersonaRegistry(pool)

            app.include_router(create_repos_router(repo_service))
            app.include_router(create_identity_router(tenant_service))
            app.include_router(create_pats_router(extract_principal, prefix="/api/v1/tokens"))
            app.include_router(create_features_router(feature_service))
            app.include_router(create_ravn_personas_router())

            try:
                yield
            finally:
                await git_registry.close()

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, loaded_settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
