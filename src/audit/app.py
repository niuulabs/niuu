"""Shared audit service app factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from niuu.cors import apply_cors_middleware
from niuu.service_database import database_pool
from niuu.service_runtime import configure_logging
from volundr.config import Settings
from niuu.utils import import_class
from sleipnir.adapters.audit_postgres import PostgresAuditRepository
from sleipnir.adapters.audit_subscriber import AuditSubscriber
from volundr.adapters.inbound.rest_audit import (
    create_audit_router,
    create_canonical_audit_router,
)

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone audit API application."""
    if settings is None:
        settings = Settings()

    configure_logging(settings.logging)

    app = FastAPI(
        title="Audit API",
        description="Audit query and event timeline surfaces.",
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
            audit_repository = PostgresAuditRepository(pool)
            audit_subscriber: AuditSubscriber | None = None

            if settings.sleipnir.enabled:
                try:
                    sleipnir_cls = import_class(settings.sleipnir.adapter)
                    sleipnir_bus = sleipnir_cls(**settings.sleipnir.kwargs)
                    audit_subscriber = AuditSubscriber(sleipnir_bus, audit_repository)
                    await audit_subscriber.start()
                except Exception:
                    logger.exception("Failed to start audit subscriber")

            app.state.audit_repository = audit_repository
            app.include_router(create_canonical_audit_router(audit_repository))
            app.include_router(create_audit_router(audit_repository))

            try:
                yield
            finally:
                if audit_subscriber is not None:
                    await audit_subscriber.stop()

    app.router.lifespan_context = lifespan
    apply_cors_middleware(app, settings.cors)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
