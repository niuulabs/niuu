"""Application factory for the Guild API."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.types import ASGIApp

from niuu.adapters.inbound.rest_guild_join import create_guild_join_router
from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.adapters.inbound.rest_pats import create_workload_identity_jwks_router
from niuu.adapters.inbound.rest_ravn import (
    create_ravn_router,
    create_ravn_session_proxy_router,
)
from niuu.adapters.inbound.rest_volundr import create_volundr_router
from niuu.adapters.node_signature import Ed25519NodeVerifier
from niuu.adapters.outbound import guild_transport
from niuu.adapters.outbound.http_agent_directory import HttpAgentDirectoryClient
from niuu.adapters.outbound.http_observatory_topology import (
    HttpObservatoryTopologyClient,
)
from niuu.adapters.pat_revocation_middleware import PATRevocationMiddleware
from niuu.adapters.postgres_instances import PostgresInstanceRepository
from niuu.adapters.postgres_nodes import PostgresNodeRepository
from niuu.adapters.postgres_observatory_fragments import (
    PostgresObservatoryFragmentRepository,
)
from niuu.adapters.postgres_pairing_codes import PostgresPairingCodeRepository
from niuu.adapters.postgres_pats import PostgresPATRepository
from niuu.config import InstanceProbeConfig, InstanceSeedConfig
from niuu.cors import apply_cors_middleware
from niuu.domain import transport_security
from niuu.domain.models import InstanceKind, InstanceVisibility
from niuu.domain.services.agent_directory import AgentDirectoryAggregationService
from niuu.domain.services.guild_join import GuildJoinService, IdentityTrustConfig
from niuu.domain.services.instance_health import InstanceHealthChecker
from niuu.domain.services.instances import InstanceService
from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService
from niuu.domain.services.observatory_topology import (
    ObservatoryTopologyAggregationService,
)
from niuu.ports.instance_probe import InstanceProbePort
from niuu.service_databases import apply_service_database_settings, database_pool
from niuu.service_instances import seed_configured_instances
from niuu.service_runtime import (
    configure_logging,
    create_authorization_adapter,
    create_identity_adapter,
    create_pat_validator,
    create_workload_identity_service,
)
from niuu.utils import import_class
from volundr.adapters.outbound.postgres_users import PostgresUserRepository
from volundr.config import Settings


def _identity_trust_config(settings: Settings) -> IdentityTrustConfig:
    """What a newly joined node should trust — Guild's own auth_mode/issuers.

    Joining never invents a second human-auth path: under ``oidc`` the node
    adopts the exact issuer list Guild itself verifies against; under
    ``none`` that is reported as an explicit, honest empty trust list rather
    than silently defaulting to something that looks configured.
    """
    issuers = list(getattr(settings.identity, "kwargs", {}).get("issuers", []) or [])
    return IdentityTrustConfig(mode=settings.auth_mode, issuers=issuers)


def _load_settings() -> Settings:
    """Load Guild settings from YAML and environment."""
    return Settings()


async def _seed_embedded_forge_instance(
    instance_service: InstanceService,
    seeded_instances: list[InstanceSeedConfig],
) -> None:
    # A configured Volundr instance that asked to be the default must win —
    # list_visible sorts default-before-name, so an unconditional
    # is_default=True here would let the embedded seed silently outrank an
    # operator's explicit choice every time the app restarts.
    await instance_service.upsert_seed_instance(
        kind=InstanceKind.VOLUNDR,
        slug="local",
        name="Local Forge",
        base_url="embedded://local-forge",
        visibility=InstanceVisibility.SYSTEM,
        enabled=True,
        is_default=not _has_configured_default_volundr(seeded_instances),
        config={"transport": "embedded"},
        tags=["local"],
    )


def _has_configured_local_forge(seeded_instances: list[InstanceSeedConfig]) -> bool:
    """Whether the operator explicitly configured their own ``local`` Volundr slug.

    Registering a remote instance must not make the embedded Local Forge
    disappear on a fresh database — the two are independent unless the
    operator deliberately names their own entry ``local``.
    """
    return any(
        item.kind == InstanceKind.VOLUNDR and item.slug.strip() == "local"
        for item in seeded_instances
    )


def _has_configured_default_volundr(seeded_instances: list[InstanceSeedConfig]) -> bool:
    """Whether any configured Volundr seed already claims is_default."""
    return any(item.kind == InstanceKind.VOLUNDR and item.is_default for item in seeded_instances)


def _build_instance_probe(
    config: InstanceProbeConfig,
    *,
    embedded_app: ASGIApp | None,
) -> InstanceProbePort:
    """Instantiate the configured InstanceProbePort (dynamic adapter + kwargs).

    ``embedded_app`` is the one exception to "every key is a kwarg": it is a
    live ASGI object the composition root holds, not something a config file
    can express, so it is injected here rather than read from ``config``
    (see .claude/rules/dynamic-adapters.md).
    """
    probe_cls = import_class(config.adapter)
    kwargs = config.model_dump(exclude={"adapter"})
    return probe_cls(embedded_app=embedded_app, **kwargs)


def create_app(
    settings: Settings | None = None,
    *,
    embedded_forge_app: ASGIApp | None = None,
) -> FastAPI:
    """Create the Guild FastAPI application.

    Every Guild route may proxy to another machine, so it never has a local
    dev-identity mode of its own: the Ravn session proxy and every other
    aggregate route forward only the caller's bearer token to a remote
    instance (see ``niuu.adapters.inbound.remote_urls.forward_identity_headers``),
    regardless of how this host authenticates locally.
    """
    loaded_settings = apply_service_database_settings(settings or _load_settings(), "guild")
    configure_logging(loaded_settings.logging)
    guild_transport.configure_default_timeouts(
        connect_timeout_ceiling_seconds=loaded_settings.guild_transport_connect_timeout_seconds
    )
    transport_security.configure_trusted_plaintext_host_suffixes(
        loaded_settings.guild_transport_trusted_plaintext_host_suffixes
    )
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
            app.state.identity = create_identity_adapter(
                loaded_settings, PostgresUserRepository(pool)
            )
            instance_repository = PostgresInstanceRepository(pool)
            await instance_repository.ensure_schema()
            instance_service = InstanceService(
                instance_repository, authorization=create_authorization_adapter(loaded_settings)
            )

            pat_repository = PostgresPATRepository(pool)
            pat_validator = create_pat_validator(loaded_settings, pat_repository)

            app.state.instance_service = instance_service
            app.state.pat_validator = pat_validator
            workload_identity_service = create_workload_identity_service(
                loaded_settings.workload_identity
            )
            app.state.workload_identity_service = workload_identity_service

            node_repository = PostgresNodeRepository(pool)
            guild_join_service = GuildJoinService(
                pairing_codes=PostgresPairingCodeRepository(pool),
                nodes=node_repository,
                instance_service=instance_service,
                instance_repository=instance_repository,
                workload_identity=workload_identity_service,
                identity_trust=_identity_trust_config(loaded_settings),
            )
            node_verifier = Ed25519NodeVerifier(
                node_repository,
                clock_skew_seconds=loaded_settings.niuu.node_join.clock_skew_seconds,
            )
            app.state.guild_join_service = guild_join_service

            if loaded_settings.niuu.instances:
                await seed_configured_instances(
                    instance_service,
                    list(loaded_settings.niuu.instances),
                )
            # Independent of the branch above: a configured remote instance
            # must not make the embedded Local Forge disappear on a fresh
            # database (see .claude/rules/no-fallbacks.md — "configured but
            # impossible" is not the case here; the two are unrelated seeds).
            if embedded_forge_app is not None and not _has_configured_local_forge(
                list(loaded_settings.niuu.instances)
            ):
                await _seed_embedded_forge_instance(
                    instance_service, list(loaded_settings.niuu.instances)
                )

            fragment_inbox = ObservatoryFragmentInboxService(
                PostgresObservatoryFragmentRepository(pool),
                ttl_seconds=loaded_settings.observatory.fragments.ttl_seconds,
                authorization=create_authorization_adapter(loaded_settings),
            )
            health_checker = InstanceHealthChecker(
                repository=instance_repository,
                probe=_build_instance_probe(
                    loaded_settings.niuu.health.probe, embedded_app=embedded_forge_app
                ),
                interval_seconds=loaded_settings.niuu.health.interval_seconds,
            )
            app.state.instance_health_checker = health_checker
            # The try/finally starts immediately after start(): if any of the
            # router construction below raises, the task must still be
            # stopped rather than leaked as an orphaned background sweep.
            health_checker.start()
            try:
                app.include_router(
                    create_instances_router(
                        instance_service,
                        health_checker=health_checker,
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
                        forge_stream_remote_timeout_seconds=(
                            loaded_settings.forge_stream_remote_timeout_seconds
                        ),
                        forge_stream_remote_connect_timeout_seconds=(
                            loaded_settings.forge_stream_remote_connect_timeout_seconds
                        ),
                        forge_stream_retry_seconds=loaded_settings.forge_stream_retry_seconds,
                        forge_stream_keepalive_seconds=(
                            loaded_settings.forge_stream_keepalive_seconds
                        ),
                        forge_stream_queue_maxsize=loaded_settings.forge_stream_queue_maxsize,
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
                        owner_probe_timeout_seconds=(
                            loaded_settings.guild_owner_probe_timeout_seconds
                        ),
                    )
                )
                app.include_router(create_workload_identity_jwks_router())
                app.include_router(
                    create_guild_join_router(guild_join_service, node_verifier=node_verifier)
                )

                yield
            finally:
                await health_checker.stop()

    app.router.lifespan_context = lifespan

    app.add_middleware(
        PATRevocationMiddleware,
        authenticate_http=True,
        websocket_check_interval=loaded_settings.pat.websocket_check_interval,
    )
    apply_cors_middleware(app, loaded_settings.cors)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()
