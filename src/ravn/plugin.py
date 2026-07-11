"""RavnPlugin — registers Ravn as a niuu CLI plugin.

Provides the ``ravn`` top-level command group, the Ravn agent service,
and a TUI page for active session management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from niuu.cli_api_client import CLIAPIClient
from niuu.cli_output import print_json, print_success, print_table
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin, TUIPageSpec

if TYPE_CHECKING:
    from collections.abc import Sequence


class RavnPlugin(ServicePlugin):
    """Plugin for the Ravn AI agent service."""

    @property
    def name(self) -> str:
        return "ravn"

    @property
    def description(self) -> str:
        return "AI agent with tool calling — sessions, platform tools, gateway"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="ravn",
            description="Agent runtime and session management",
            default_enabled=True,
            depends_on=["postgres"],
        )

    def create_api_app(self) -> Any:
        from ravn.api import create_app

        return create_app()

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Ravn")

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="ravn-runtime-api",
                prefixes=(
                    "/api/v1/ravn/ravens",
                    "/api/v1/ravn/sessions",
                ),
                description="Ravn runtime fleet and session routes.",
            ),
            APIRouteDomain(
                name="ravn-trigger-api",
                prefixes=("/api/v1/ravn/triggers",),
                description="Ravn trigger definition routes.",
            ),
            APIRouteDomain(
                name="ravn-budget-api",
                prefixes=("/api/v1/ravn/budget",),
                description="Ravn per-agent and fleet budget routes.",
            ),
            APIRouteDomain(
                name="ravn-valkyrie-api",
                prefixes=("/api/v1/ravn/valkyrie",),
                description="Resident Valkyrie dashboard, huddle, learning, and signal routes.",
            ),
            APIRouteDomain(
                name="ravn-odin-api",
                prefixes=("/api/v1/ravn/odin",),
                description="Central ODIN review queue: every decision awaiting an operator.",
            ),
            APIRouteDomain(
                name="ravn-session-api",
                prefixes=(
                    "/api/v1/ravn/status",
                    "/api/v1/ravn/sessions",
                ),
                description="Ravn session inventory and platform status routes.",
            ),
            APIRouteDomain(
                name="ravn-api",
                prefixes=("/api/v1/ravn",),
                description="All currently mounted Ravn API routes.",
            ),
        )

    def register_commands(self, app: typer.Typer) -> None:
        """Mount ravn commands on the main app."""
        plugin = self

        ravn_app = typer.Typer(
            name="ravn",
            help="Manage Ravn AI agent sessions.",
            no_args_is_help=True,
        )

        @ravn_app.command("list")
        def list_sessions(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """List active agent sessions."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/ravn/sessions")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            if not data:
                typer.echo("No active agent sessions.")
                return

            print_table(
                columns=[
                    ("id", "ID"),
                    ("status", "Status"),
                    ("model", "Model"),
                    ("created_at", "Created"),
                ],
                rows=data,
            )

        @ravn_app.command("stop")
        def stop_session(
            session_id: str = typer.Argument(help="Session ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Stop a running agent session."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/ravn/sessions/{session_id}/stop")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "stopped"})
                return

            print_success(f"Session {session_id} stopped.")

        @ravn_app.command("status")
        def platform_status(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Show Ravn platform status."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/ravn/status")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            session_count = data.get("session_count", 0)
            typer.echo(f"Ravn — {session_count} active session(s)")

        app.add_typer(ravn_app, name="ravn")

    def tui_pages(self) -> Sequence[TUIPageSpec]:
        from ravn.tui.agents import AgentsPage

        return [
            TUIPageSpec(name="Agents", icon="◉", widget_class=AgentsPage),
        ]
