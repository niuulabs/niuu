"""Application factory for the Guild API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp

from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.adapters.inbound.rest_pats import create_workload_identity_jwks_router
from niuu.adapters.inbound.rest_ravn import (
    create_ravn_router,
    create_ravn_session_proxy_router,
)
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.outbound.http_agent_directory import HttpAgentDirectoryClient
from niuu.adapters.outbound.http_observatory_topology import (
    HttpObservatoryTopologyClient,
)
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_instances import PostgresInstanceRepository
from niuu.adapters.postgres_observatory_fragments import (
    PostgresObservatoryFragmentRepository,
)
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.cors import apply_cors_middleware
from niuu.domain.models import InstanceKind, InstanceVisibility
from niuu.domain.services.agent_directory import AgentDirectoryAggregationService
from niuu.domain.services.instances import InstanceService
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.observatory_topology import (
    ObservatoryTopologyAggregationService,
)
from niuu.service_databases import apply_service_database_settings, database_pool
from niuu.service_instances import seed_configured_instances
from niuu.service_runtime import (
    configure_logging,
    create_pat_validator,
    create_workload_identity_service,
)
from volundr.config import Settings


def _load_settings() -> Settings:
    """Load Guild settings from YAML and environment."""
    return Settings()


async def _seed_embedded_forge_instance(instance_service: InstanceService) -> None:
    await instance_service.upsert_seed_instance(
        kind=InstanceKind.VOLUNDR,
        slug="local",
        name="Local Forge",
        base_url="embedded://local-forge",
        visibility=InstanceVisibility.SYSTEM,
        enabled=True,
        is_default=True,
        config={"transport": "embedded"},
        tags=["local"],
    )


def create_app(
    settings: Settings | None = None,
    *,
    embedded_forge_app: ASGIApp | None = None,
) -> FastAPI:
    """Create the Guild FastAPI application."""
    loaded_settings = apply_service_database_settings(settings or _load_settings(), "guild")
    configure_logging(loaded_settings.logging)
    directory_cfg = loaded_settings.observatory.directory
    agent_directory = AgentDirectoryAggregationService(
        client=HttpAgentDirectoryClient(
            timeout_seconds=directory_cfg.guild_timeout_seconds,
        ),
        max_concurrency=directory_cfg.guild_max_concurrency,
    )
    app = FastAPI(
        title="Guild",
        description="Shared instance registry, discovery, and Forge runtime facade APIs.",
        version="0.1.0",
    )
    app.state.settings = loaded_settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        async with database_pool(loaded_settings.database) as pool:
            instance_repository = PostgresInstanceRepository(pool)
            await instance_repository.ensure_schema()
            instance_service = InstanceService(instance_repository)

            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(loaded_settings, pat_repository)

            app.state.instance_service = instance_service
            app.state.pat_validator = pat_validator
            app.state.workload_identity_service = create_workload_identity_service(
                loaded_settings.workload_identity
            )

            if loaded_settings.niuu.instances:
                await seed_configured_instances(
                    instance_service,
                    list(loaded_settings.niuu.instances),
                )
            elif embedded_forge_app is not None:
                await _seed_embedded_forge_instance(instance_service)

            fragment_inbox = ObservatoryFragmentInboxService(
                PostgresObservatoryFragmentRepository(pool),
                ttl_seconds=loaded_settings.observatory.fragments.ttl_seconds,
            )
            app.include_router(
                create_instances_router(
                    instance_service,
                    embedded_forge_app=embedded_forge_app,
                    agent_directory=agent_directory,
                    fragment_inbox=fragment_inbox,
                    topology=ObservatoryTopologyAggregationService(
                        client=HttpObservatoryTopologyClient(
                            timeout_seconds=directory_cfg.guild_timeout_seconds,
                        ),
                        max_concurrency=directory_cfg.guild_max_concurrency,
                        fragment_inbox=fragment_inbox,
                    ),
                )
            )
            app.include_router(
                create_volundr_router(
                    instance_service,
                    embedded_forge_app=embedded_forge_app,
                )
            )
            app.include_router(
                create_ravn_router(
                    instance_service,
                    embedded_forge_app=embedded_forge_app,
                )
            )
            app.include_router(
                create_ravn_session_proxy_router(
                    instance_service,
                    embedded_forge_app=embedded_forge_app,
                )
            )
            app.include_router(create_workload_identity_jwks_router())

            yield

    app.router.lifespan_context = lifespan

    apply_cors_middleware(app, loaded_settings.cors)
    app.add_middleware(PATRevocationMiddleware)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
