"""TingPlugin — registers Ting as a niuu CLI plugin.

Provides ``sagas`` and ``runs`` top-level command groups, the Ting
coordinator service, and TUI pages for saga management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from niuu.cli_api_client import CLIAPIClient
from niuu.cli_output import print_json, print_success, print_table
from niuu.ports.plugin import APIRouteDomain, ServiceDefinition, ServicePlugin, TUIPageSpec

if TYPE_CHECKING:
    from collections.abc import Sequence


class TingPlugin(ServicePlugin):
    """Plugin for the Ting saga coordinator service."""

    @property
    def name(self) -> str:
        return "ting"

    @property
    def description(self) -> str:
        return "Autonomous saga coordinator — reviews, dispatch, runs"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition.hosted(
            name="ting",
            description="Autonomous saga coordinator",
            default_enabled=True,
            depends_on=["postgres", "volundr"],
        )

    def create_api_app(self, public_origin: str | None = None) -> Any:
        from ting.main import create_app

        return create_app(public_origin=public_origin)

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="tracker-intake-api",
                prefixes=(
                    "/api/v1/tracker/projects",
                    "/api/v1/tracker/import",
                ),
                description=(
                    "Ting-owned tracker intake routes for browsing "
                    "external projects and importing them into sagas."
                ),
            ),
            APIRouteDomain(
                name="saga-api",
                prefixes=("/api/v1/ting/sagas",),
                description="Saga planning and saga lifecycle routes.",
            ),
            APIRouteDomain(
                name="review-api",
                prefixes=(
                    "/api/v1/ting/runs",
                    "/api/v1/ting/sessions",
                ),
                description="Run review, run messaging, and approval-session routes.",
            ),
            APIRouteDomain(
                name="dispatch-api",
                prefixes=(
                    "/api/v1/ting/dispatch",
                    "/api/v1/ting/dispatcher",
                ),
                description="Dispatch queue, approvals, dispatcher state, and activity log routes.",
            ),
            APIRouteDomain(
                name="workflow-api",
                prefixes=(
                    "/api/v1/ting/flock",
                    "/api/v1/ting/flock_flows",
                    "/api/v1/ting/research",
                    "/api/v1/ting/specs",
                ),
                description="Flock configuration and flow library routes.",
            ),
            APIRouteDomain(
                name="settings-api",
                prefixes=("/api/v1/ting/settings",),
                description="Ting settings for flock, dispatch defaults, and notifications.",
            ),
            APIRouteDomain(
                name="ting-channel-api",
                prefixes=("/api/v1/ting/telegram",),
                description=(
                    "Ting operator-channel routes for Telegram setup and webhook ingress."
                ),
            ),
            APIRouteDomain(
                name="event-api",
                prefixes=("/api/v1/ting/events",),
                description="Ting SSE event stream routes.",
            ),
            APIRouteDomain(
                name="a2a-card-api",
                prefixes=("/.well-known/agent-card.json",),
                description=(
                    "A2A agent-card discovery document served from the origin "
                    "root, projecting system workflows as skills."
                ),
            ),
            APIRouteDomain(
                name="ting-api",
                prefixes=("/api/v1/ting",),
                description="Ting coordination and run routes.",
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Ting")

    def register_commands(self, app: typer.Typer) -> None:
        """Mount workflow commands directly on the main app."""
        plugin = self

        # ── Sagas ──────────────────────────────────────────────────── #

        sagas = typer.Typer(
            name="sagas",
            help="Manage sagas.",
            no_args_is_help=True,
        )

        @sagas.command("list")
        def list_sagas(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """List active sagas."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/ting/sagas")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            if not data:
                typer.echo("No active sagas.")
                return

            print_table(
                columns=[
                    ("id", "ID"),
                    ("name", "Name"),
                    ("status", "Status"),
                    ("progress", "Progress"),
                    ("run_count", "Runs"),
                ],
                rows=data,
            )

        @sagas.command()
        def create(
            name: str = typer.Argument(help="Saga name"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Create a new saga."""
            client = plugin.create_api_client()
            resp = client.request_or_exit(
                "POST",
                "/api/v1/ting/sagas/commit",
                json_body={"name": name},
            )
            data = resp.json()

            if json_output:
                print_json(data)
                return

            print_success(f"Saga created: {data.get('id', 'unknown')}")

        @sagas.command()
        def dispatch(
            saga_id: str = typer.Argument(help="Saga ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Dispatch a saga for execution."""
            client = plugin.create_api_client()
            resp = client.request_or_exit(
                "POST",
                "/api/v1/ting/dispatch/approve",
                json_body={"saga_id": saga_id},
            )
            data = resp.json()

            if json_output:
                print_json(data)
                return

            print_success(f"Saga {saga_id} dispatched.")

        # ── Runs ──────────────────────────────────────────────────── #

        runs = typer.Typer(
            name="runs",
            help="Manage runs.",
            no_args_is_help=True,
        )

        @runs.command()
        def active(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """List active runs."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/ting/runs/active")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            if not data:
                typer.echo("No active runs.")
                return

            print_table(
                columns=[
                    ("id", "ID"),
                    ("name", "Name"),
                    ("status", "Status"),
                    ("confidence", "Confidence"),
                    ("session", "Session"),
                ],
                rows=data,
            )

        @runs.command()
        def approve(
            run_id: str = typer.Argument(help="Run ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Approve a pending run."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/ting/runs/{run_id}/approve")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "approved"})
                return

            print_success(f"Run {run_id} approved.")

        @runs.command()
        def reject(
            run_id: str = typer.Argument(help="Run ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Reject a pending run."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/ting/runs/{run_id}/reject")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "rejected"})
                return

            print_success(f"Run {run_id} rejected.")

        @runs.command()
        def retry(
            run_id: str = typer.Argument(help="Run ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Retry a failed run."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/ting/runs/{run_id}/retry")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "retrying"})
                return

            print_success(f"Run {run_id} retry initiated.")

        app.add_typer(sagas, name="sagas")
        app.add_typer(runs, name="runs")

    def tui_pages(self) -> Sequence[TUIPageSpec]:
        """Return TUI page specs for the Textual app."""
        from ting.tui.pages.dispatch import DispatchPage
        from ting.tui.pages.review import ReviewPage
        from ting.tui.pages.runs import RunsPage
        from ting.tui.pages.sagas import SagasPage

        return [
            TUIPageSpec(name="Sagas", icon="⚡", widget_class=SagasPage),
            TUIPageSpec(name="Runs", icon="⚔", widget_class=RunsPage),
            TUIPageSpec(name="Dispatch", icon="🚀", widget_class=DispatchPage),
            TUIPageSpec(name="Review", icon="👁", widget_class=ReviewPage),
        ]
