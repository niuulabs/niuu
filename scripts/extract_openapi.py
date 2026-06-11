"""Extract the OpenAPI spec from Volundr without starting the server.

Creates the FastAPI app with mock service dependencies, includes all routers,
and writes the OpenAPI JSON schema to stdout or a file.

Usage:
    uv run --extra dev python scripts/extract_openapi.py > docs/site/openapi.json
    uv run --extra dev python scripts/extract_openapi.py -o docs/site/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib.metadata import metadata
from unittest.mock import MagicMock

from fastapi import FastAPI

from volundr.adapters.inbound.rest import create_router
from volundr.adapters.inbound.rest_admin_settings import create_admin_settings_router
from volundr.adapters.inbound.rest_credentials import create_credentials_router
from volundr.adapters.inbound.rest_events import create_events_router
from volundr.adapters.inbound.rest_git import create_git_router
from volundr.adapters.inbound.rest_integrations import create_integrations_router
from volundr.adapters.inbound.rest_launch_specs import create_launch_specs_router
from volundr.adapters.inbound.rest_prompts import create_prompts_router
from volundr.adapters.inbound.rest_resources import create_resources_router
from volundr.adapters.inbound.rest_secrets import create_secrets_router
from volundr.adapters.inbound.rest_tenants import create_tenants_router
from volundr.adapters.inbound.rest_tracker import create_tracker_router


def build_openapi_app() -> FastAPI:
    """Build a FastAPI app with all routers for OpenAPI extraction."""
    _meta = metadata("volundr")

    app = FastAPI(
        title="Volundr",
        description=_meta["Summary"],
        version=_meta["Version"],
        openapi_tags=[
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
                "description": "Available LLM models, per-session token usage, "
                "and aggregate statistics.",
            },
            {
                "name": "Repositories",
                "description": "Git repository registry — sources available for "
                "cloning into sessions.",
            },
            {
                "name": "Profiles",
                "description": "Forge profiles — resource and workload configuration "
                "presets (read-only, config-driven).",
            },
            {
                "name": "Templates",
                "description": "Workspace templates — multi-repo workspace layouts "
                "with setup scripts (read-only, config-driven).",
            },
            {
                "name": "Git Workflow",
                "description": "Git workflow operations — create PRs from sessions, "
                "merge, check CI status, and calculate merge confidence.",
            },
            {
                "name": "MCP Servers",
                "description": "MCP server catalogue — available Model Context Protocol servers.",
            },
            {
                "name": "Secrets",
                "description": "Kubernetes secret management — list and create "
                "mountable secrets for sessions.",
            },
            {
                "name": "Presets",
                "description": "Runtime configuration presets — portable, DB-stored "
                "bundles of model, MCP servers, resources, and environment config.",
            },
            {
                "name": "Issue Tracker",
                "description": "External issue tracker integration — search issues, "
                "update status, and manage repo-to-project mappings.",
            },
            {
                "name": "Tenants & Users",
                "description": "Multi-tenant management — tenants, user memberships, "
                "and role assignments.",
            },
            {
                "name": "Credentials",
                "description": "User credential management — store and retrieve "
                "secrets for session injection.",
            },
            {
                "name": "Events",
                "description": "Server-Sent Events stream for real-time session updates.",
            },
            {
                "name": "Integrations",
                "description": "External service integrations — configure and test "
                "connections to third-party tools.",
            },
            {
                "name": "Admin",
                "description": "Administrative settings — runtime-toggleable "
                "configuration for storage, features, and policies.",
            },
            {
                "name": "Resources",
                "description": "Cluster resource discovery — available CPU, memory, "
                "and GPU capacity.",
            },
        ],
    )

    mock = MagicMock()

    app.include_router(
        create_router(mock, mock, mock, mock, mock, mock, mock, external_session_service=mock)
    )
    app.include_router(create_launch_specs_router(mock, mock))
    app.include_router(create_resources_router(mock))
    app.include_router(create_secrets_router(mock, mock))
    app.include_router(create_prompts_router(mock))
    app.include_router(create_git_router(mock))
    app.include_router(create_tenants_router(mock))
    app.include_router(create_admin_settings_router())
    app.include_router(create_credentials_router(mock))
    app.include_router(create_events_router(mock, mock))
    app.include_router(
        create_integrations_router(
            mock,
            mock,
            registry=mock,
        )
    )
    app.include_router(create_tracker_router(mock))

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Volundr OpenAPI spec")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    args = parser.parse_args()

    app = build_openapi_app()
    spec = app.openapi()

    output = json.dumps(spec, indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
            f.write("\n")
        print(f"OpenAPI spec written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
