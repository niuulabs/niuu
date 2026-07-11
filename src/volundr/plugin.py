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
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin, TUIPageSpec
from volundr.tui.admin import AdminPage
from volundr.tui.chat import ChatPage
from volundr.tui.chronicles import ChroniclesPage
from volundr.tui.diffs import DiffsPage
from volundr.tui.sessions import SessionsPage
from volundr.tui.settings import SettingsPage
from volundr.tui.terminal import TerminalPage


class VolundrPlugin(ServicePlugin):
    """Plugin for the Volundr development platform service."""

    @property
    def name(self) -> str:
        return "volundr"

    @property
    def description(self) -> str:
        return "AI-native development platform — sessions, chronicles, workspaces"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="volundr",
            description="AI-native development platform",
            default_enabled=True,
            depends_on=["postgres"],
            default_port=8080,
        )

    def create_api_app(self, public_origin: str = "http://localhost:8080") -> Any:
        from volundr.main import create_app

        return create_app(public_origin=public_origin)

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="admin-api",
                prefixes=(
                    "/api/v1/forge/admin",
                    "/api/v1/forge/settings",
                ),
                description=(
                    "Administrative routes for Forge host settings and global workspace management."
                ),
            ),
            APIRouteDomain(
                name="catalog-api",
                prefixes=(
                    "/api/v1/volundr/launch-specs",
                    "/api/v1/volundr/session-definitions",
                    "/api/v1/volundr/resources",
                    "/api/v1/volundr/prompts",
                ),
                description=(
                    "Launch specs, session definitions, prompts, and resource catalog routes."
                ),
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
