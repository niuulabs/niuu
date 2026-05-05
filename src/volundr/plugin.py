"""VolundrPlugin — registers Volundr as a niuu CLI plugin.

Provides the ``sessions`` top-level command group, the Volundr API service,
and TUI pages for session management.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import typer

from niuu.cli_api_client import CLIAPIClient
from niuu.cli_output import print_json, print_success, print_table
from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin, TUIPageSpec
from volundr.tui.admin import AdminPage
from volundr.tui.chat import ChatPage
from volundr.tui.chronicles import ChroniclesPage
from volundr.tui.diffs import DiffsPage
from volundr.tui.sessions import SessionsPage
from volundr.tui.settings import SettingsPage
from volundr.tui.terminal import TerminalPage


class _VolundrStub(Service):
    """Stub — actual server is managed by the CLI root server."""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class VolundrPlugin(ServicePlugin):
    """Plugin for the Volundr development platform service."""

    @property
    def name(self) -> str:
        return "volundr"

    @property
    def description(self) -> str:
        return "AI-native development platform — sessions, chronicles, workspaces"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="volundr",
            description="AI-native development platform",
            factory=lambda: _VolundrStub(),
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8080,
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from volundr.main import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="audit-api",
                prefixes=("/api/v1/audit", "/audit"),
                description="Canonical audit log query routes.",
            ),
            APIRouteDomain(
                name="admin-api",
                prefixes=("/api/v1/volundr/admin",),
                description=(
                    "Administrative routes for users, settings, and global workspace management."
                ),
            ),
            APIRouteDomain(
                name="features-api",
                prefixes=("/api/v1/features",),
                description="Canonical feature catalog and preferences routes.",
            ),
            APIRouteDomain(
                name="features-legacy-api",
                prefixes=("/api/v1/volundr/features",),
                description="Legacy Volundr-scoped feature routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="credentials-api",
                prefixes=("/api/v1/credentials",),
                description="Canonical credential and secret-type routes.",
            ),
            APIRouteDomain(
                name="credentials-legacy-api",
                prefixes=(
                    "/api/v1/volundr/credentials",
                    "/api/v1/volundr/secrets",
                ),
                description="Legacy Volundr credential and secret-store compatibility routes.",
            ),
            APIRouteDomain(
                name="forge-api",
                prefixes=(
                    "/api/v1/forge/sessions",
                    "/api/v1/forge/chronicles",
                    "/api/v1/forge/events",
                    "/api/v1/forge/templates",
                    "/api/v1/forge/presets",
                    "/api/v1/forge/profiles",
                    "/api/v1/forge/session-definitions",
                    "/api/v1/forge/workspaces",
                    "/api/v1/forge/resources",
                    "/api/v1/forge/models",
                    "/api/v1/forge/stats",
                    "/api/v1/forge/prompts",
                    "/api/v1/forge/cluster",
                    "/api/v1/forge/mcp-servers",
                    "/api/v1/forge/git",
                ),
                description="Forge session, workspace, template, repo, and execution routes.",
            ),
            APIRouteDomain(
                name="forge-legacy-api",
                prefixes=(
                    "/api/v1/volundr/sessions",
                    "/api/v1/volundr/chronicles",
                    "/api/v1/volundr/events",
                    "/api/v1/volundr/templates",
                    "/api/v1/volundr/presets",
                    "/api/v1/volundr/profiles",
                    "/api/v1/volundr/session-definitions",
                    "/api/v1/volundr/workspaces",
                    "/api/v1/volundr/resources",
                    "/api/v1/volundr/models",
                    "/api/v1/volundr/stats",
                    "/api/v1/volundr/prompts",
                    "/api/v1/volundr/cluster",
                    "/api/v1/volundr/mcp-servers",
                    "/api/v1/volundr/git",
                ),
                description="Legacy Volundr-scoped Forge routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="session-api",
                prefixes=(
                    "/api/v1/forge/sessions",
                    "/api/v1/forge/chronicles",
                    "/api/v1/forge/events",
                ),
                description=(
                    "Session lifecycle, messaging, logs, chronicle, "
                    "and session-bound workflow routes."
                ),
            ),
            APIRouteDomain(
                name="session-legacy-api",
                prefixes=(
                    "/api/v1/volundr/sessions",
                    "/api/v1/volundr/chronicles",
                    "/api/v1/volundr/events",
                ),
                description="Legacy Volundr-scoped session and chronicle routes.",
            ),
            APIRouteDomain(
                name="workspace-api",
                prefixes=("/api/v1/forge/workspaces",),
                description="User workspace inventory and workspace deletion routes.",
            ),
            APIRouteDomain(
                name="workspace-legacy-api",
                prefixes=("/api/v1/volundr/workspaces",),
                description="Legacy Volundr workspace routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="catalog-api",
                prefixes=(
                    "/api/v1/forge/templates",
                    "/api/v1/forge/presets",
                    "/api/v1/forge/profiles",
                    "/api/v1/forge/session-definitions",
                    "/api/v1/forge/resources",
                    "/api/v1/forge/prompts",
                    "/api/v1/forge/mcp-servers",
                ),
                description=(
                    "Templates, presets, profiles, session definitions, prompts, "
                    "resource catalog, and MCP metadata routes."
                ),
            ),
            APIRouteDomain(
                name="catalog-legacy-api",
                prefixes=(
                    "/api/v1/volundr/templates",
                    "/api/v1/volundr/presets",
                    "/api/v1/volundr/profiles",
                    "/api/v1/volundr/session-definitions",
                    "/api/v1/volundr/resources",
                    "/api/v1/volundr/prompts",
                    "/api/v1/volundr/mcp-servers",
                ),
                description="Legacy Volundr catalog routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="git-api",
                prefixes=(
                    "/api/v1/forge/repos/branches",
                    "/api/v1/forge/repos/prs",
                    "/api/v1/forge/git",
                ),
                description="Git workflow routes without the deprecated repo-catalog surface.",
            ),
            APIRouteDomain(
                name="git-legacy-api",
                prefixes=(
                    "/api/v1/volundr/repos/branches",
                    "/api/v1/volundr/repos/prs",
                    "/api/v1/volundr/git",
                ),
                description="Legacy Volundr git workflow routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="volundr-api",
                prefixes=("/api/v1/volundr",),
                description="Legacy catch-all Volundr API surface kept for compatibility.",
            ),
            APIRouteDomain(
                name="identity-api",
                prefixes=("/api/v1/identity",),
                description="Canonical identity routes currently served by Volundr.",
            ),
            APIRouteDomain(
                name="identity-legacy-api",
                prefixes=("/api/v1/volundr/me", "/api/v1/volundr/identity"),
                description="Legacy Volundr-scoped identity routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="integrations-api",
                prefixes=("/api/v1/integrations",),
                description="Canonical integrations and OAuth routes currently served by Volundr.",
            ),
            APIRouteDomain(
                name="integrations-legacy-api",
                prefixes=("/api/v1/volundr/integrations",),
                description="Legacy Volundr-scoped integrations routes kept for compatibility.",
            ),
            APIRouteDomain(
                name="tenancy-api",
                prefixes=("/api/v1/volundr/tenants",),
                description="Tenant hierarchy, membership, and tenant reprovisioning routes.",
            ),
            APIRouteDomain(
                name="tracker-api",
                prefixes=(
                    "/api/v1/tracker/status",
                    "/api/v1/tracker/issues",
                    "/api/v1/tracker/repo-mappings",
                ),
                description="Canonical tracker issue, status, and repo mapping routes.",
            ),
            APIRouteDomain(
                name="tokens-api",
                prefixes=("/api/v1/tokens",),
                description="Canonical personal access token routes currently served by Volundr.",
            ),
            APIRouteDomain(
                name="tokens-legacy-api",
                prefixes=("/api/v1/users/tokens", "/api/v1/volundr/tokens"),
                description="Legacy token routes kept for compatibility during cutover.",
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Volundr")

    def tui_pages(self) -> Sequence[TUIPageSpec]:
        return [
            TUIPageSpec(name="Sessions", icon="◉", widget_class=SessionsPage),
            TUIPageSpec(name="Chat", icon="◈", widget_class=ChatPage),
            TUIPageSpec(name="Terminal", icon="▸", widget_class=TerminalPage),
            TUIPageSpec(name="Diffs", icon="◧", widget_class=DiffsPage),
            TUIPageSpec(name="Chronicles", icon="◷", widget_class=ChroniclesPage),
            TUIPageSpec(name="Settings", icon="◎", widget_class=SettingsPage),
            TUIPageSpec(name="Admin", icon="◈", widget_class=AdminPage),
        ]

    def register_commands(self, app: typer.Typer) -> None:
        """Mount workflow commands directly on the main app."""
        plugin = self

        sessions = typer.Typer(
            name="sessions",
            help="Manage coding sessions.",
            no_args_is_help=True,
        )

        @sessions.command("list")
        def list_sessions(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """List active sessions."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/forge/sessions")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            if not data:
                typer.echo("No active sessions.")
                return

            print_table(
                columns=[
                    ("id", "ID"),
                    ("name", "Name"),
                    ("status", "Status"),
                    ("model", "Model"),
                    ("tokens", "Tokens"),
                    ("created_at", "Created"),
                ],
                rows=data,
            )

        @sessions.command()
        def create(
            name: str = typer.Argument(help="Session name"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Create a new session."""
            client = plugin.create_api_client()
            resp = client.request_or_exit(
                "POST",
                "/api/v1/forge/sessions",
                json_body={"name": name},
            )
            data = resp.json()

            if json_output:
                print_json(data)
                return

            print_success(f"Session created: {data.get('id', 'unknown')}")

        @sessions.command()
        def stop(
            session_id: str = typer.Argument(help="Session ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Stop a running session."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/forge/sessions/{session_id}/stop")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "stopped"})
                return

            print_success(f"Session {session_id} stopped.")

        @sessions.command()
        def delete(
            session_id: str = typer.Argument(help="Session ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Delete a session."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("DELETE", f"/api/v1/forge/sessions/{session_id}")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "deleted"})
                return

            print_success(f"Session {session_id} deleted.")

        app.add_typer(sessions, name="sessions")
