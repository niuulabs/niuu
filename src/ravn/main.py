"""Standalone Ravn API service entrypoint."""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from niuu.service_runtime import run_service_app
from ravn.api import create_app as create_ravn_app


def create_app() -> FastAPI:
    """Create the standalone Ravn API service app."""
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=getattr(logging, level_name, logging.INFO), force=True)

    app = create_ravn_app()

    @app.get("/health", tags=["Health"])
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    return app


app = create_app()


def main() -> None:
    """Run the standalone Ravn API service."""
    run_service_app("ravn.main:app", 8084)


if __name__ == "__main__":
    main()
