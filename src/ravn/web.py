"""Standalone Ravn web server.

Spins up a lightweight FastAPI app serving the Ravn API (personas, agent
status) and the web UI static files.  No Volundr, Tyr, or PostgreSQL
required — persona storage is filesystem-based (YAML in ~/.ravn/personas/).

Usage::

    ravn web --port 7477
    ravn daemon --persona coding-agent --web --web-port 7477
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.api import create_app

logger = logging.getLogger(__name__)

# Default port for the standalone web UI
DEFAULT_WEB_PORT = 7477

# Config endpoint payload — tells the web UI which modules to show.
# In standalone mode only "ravn" is available.
_STANDALONE_CONFIG = {"modules": ["ravn"]}

# Locate the compiled web UI dist directory.
# web.py is at src/ravn/web.py → 3 parents reach the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WEB_DIST = _REPO_ROOT / "web" / "dist"


def create_standalone_app(persona_dirs: list[str] | None = None) -> FastAPI:
    """Create the standalone Ravn ASGI application.

    Args:
        persona_dirs: Optional list of filesystem directories to search for
            persona YAML files.  Defaults to the standard search path
            (project-local + user-global + built-ins).
    """
    persona_loader = FilesystemPersonaAdapter(persona_dirs=persona_dirs if persona_dirs else None)
    app = create_app(persona_loader=persona_loader)

    # Allow browser clients (CORS for standalone use)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/config.json")
    async def config() -> dict:
        """Return the module list for the web UI sidebar.

        Standalone Ravn only exposes the Ravn module.
        """
        return _STANDALONE_CONFIG

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {"status": "ok", "mode": "standalone"}

    # Mount static files when the compiled web dist exists.
    # Falls back gracefully when running without a build (e.g. in tests).
    if _WEB_DIST.is_dir():
        # SPA fallback: serve index.html for any unknown path so that
        # React Router handles client-side navigation.
        _dist = _WEB_DIST  # capture at construction time for closure stability
        _index = _dist / "index.html"

        app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

        _dist_resolved = _dist.resolve()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str) -> FileResponse:
            """Return index.html for all non-API routes (SPA fallback)."""
            candidate = (_dist / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(_dist_resolved):
                return FileResponse(str(candidate))
            return FileResponse(str(_index))

    else:
        logger.warning(
            "Web UI dist not found at %s — static file serving disabled. "
            "Run 'npm run build' in the web/ directory to enable the UI.",
            _WEB_DIST,
        )

    return app


def serve(
    host: str = "0.0.0.0",
    port: int = DEFAULT_WEB_PORT,
    persona_dirs: list[str] | None = None,
    reload: bool = False,
) -> None:
    """Start the standalone Ravn web server using uvicorn.

    Args:
        host: Bind address (default: 0.0.0.0).
        port: Listen port (default: 7477).
        persona_dirs: Custom persona search directories.
        reload: Enable auto-reload for development.
    """
    import uvicorn

    if reload:
        # reload mode requires an import string so uvicorn can re-import the
        # module on changes. Custom persona_dirs are not supported in this mode.
        uvicorn.run(
            "ravn.web:create_standalone_app",
            host=host,
            port=port,
            reload=True,
            factory=True,
        )
        return

    app = create_standalone_app(persona_dirs=persona_dirs)
    logger.info("Starting Ravn standalone web UI on http://%s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
