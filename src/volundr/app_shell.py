"""FastAPI shell construction for Volundr."""

from __future__ import annotations

import logging
from importlib.metadata import metadata

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from niuu.service_runtime import configure_logging
from volundr.config import Settings

logger = logging.getLogger(__name__)

OPENAPI_TAGS = [
    {
        "name": "Sessions",
        "description": "Session lifecycle management — create, start, stop, "
        "delete sessions and report token usage.",
    },
    {
        "name": "Chronicles",
        "description": "Session history records — snapshots of completed or "
        "in-progress sessions, reforge chains, and broker reports.",
    },
    {
        "name": "Timeline",
        "description": "Granular event timelines within a chronicle — "
        "messages, file edits, git commits, and terminal activity.",
    },
    {
        "name": "Models & Stats",
        "description": "Available LLM models and aggregate usage statistics.",
    },
    {
        "name": "Repositories",
        "description": "Git providers and repository discovery.",
    },
    {
        "name": "Launch Specs",
        "description": "Launch specs — the unified session blueprint "
        "(system-scope config-seeded + user-scope DB-stored).",
    },
    {
        "name": "Session Definitions",
        "description": "Session definitions — the runtime types a launch spec runs on.",
    },
    {
        "name": "Git Workflow",
        "description": "Git workflow operations — create PRs from sessions, "
        "merge, check CI status, and calculate merge confidence.",
    },
    {
        "name": "MCP Servers",
        "description": "Available MCP server configurations for session setup.",
    },
    {
        "name": "Secrets",
        "description": "Kubernetes secret management — list and create "
        "mountable secrets for sessions.",
    },
    {
        "name": "Issue Tracker",
        "description": "External issue tracker integration — search issues, "
        "update status, and manage repo-to-project mappings.",
    },
]


def build_app_shell(settings: Settings) -> FastAPI:
    """Build the HTTP shell; runtime adapters remain wired in ``main.create_app``."""
    configure_logging(settings.logging)
    package_metadata = metadata("volundr")
    app = FastAPI(
        title="Volundr",
        description=package_metadata["Summary"],
        version=package_metadata["Version"],
        openapi_tags=OPENAPI_TAGS,
    )
    app.state.settings = settings
    app.state.admin_settings = {"storage": {"home_enabled": True}}

    @app.exception_handler(RequestValidationError)
    async def log_request_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        try:
            raw_body = (await request.body())[:2000]
            body_preview = raw_body.decode("utf-8", "replace")
        except Exception:
            body_preview = "<unreadable>"
        logger.warning(
            "422 request validation: %s %s errors=%s body=%s",
            request.method,
            request.url.path,
            exc.errors(),
            body_preview,
        )
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    return app
