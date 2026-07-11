"""Standalone Volundr catalog application."""

from __future__ import annotations

from fastapi import FastAPI

from niuu.cors import apply_cors_middleware
from niuu.service_runtime import configure_logging
from volundr.catalog.assembly import build_catalog
from volundr.config import Settings


def create_catalog_app(settings: Settings | None = None) -> FastAPI:
    """Create the standalone catalog app.

    This app serves only catalog routes under ``/api/v1/volundr``. It has no
    session runtime, pod manager, database, gateway, or secret injection.
    """
    settings = settings or Settings()
    configure_logging(settings.logging)

    app = FastAPI(
        title="Volundr Catalog",
        description="Volundr catalog: launch specs and session definitions.",
        version="0.1.0",
    )
    app.state.settings = settings
    apply_cors_middleware(app, settings.cors)

    catalog = build_catalog(settings)
    app.include_router(catalog.router)
    app.state.launch_spec_service = catalog.launch_spec_service

    @app.get("/health", tags=["Health"])
    @app.get("/api/v1/volundr/health", include_in_schema=False)
    async def health_check() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_catalog_app()
