"""TyrPlugin — registers Tyr as a niuu CLI plugin.

Provides ``sagas`` and ``raids`` top-level command groups, the Tyr
coordinator service, and TUI pages for saga management.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from niuu.cli_api_client import CLIAPIClient
from niuu.cli_output import print_json, print_success, print_table
from niuu.ports.plugin import APIRouteDomain, Service, ServiceDefinition, ServicePlugin, TUIPageSpec

if TYPE_CHECKING:
    from collections.abc import Sequence


class _TyrService(Service):
    """Tyr lifecycle managed by the Volundr unified server.

    Tyr is mounted into the Volundr FastAPI app on the same port,
    so this service only tracks that Volundr has started.
    """

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def health_check(self) -> bool:
        return True


class TyrPlugin(ServicePlugin):
    """Plugin for the Tyr saga coordinator service."""

    @property
    def name(self) -> str:
        return "tyr"

    @property
    def description(self) -> str:
        return "Autonomous saga coordinator — reviews, dispatch, raids"

    def register_service(self) -> ServiceDefinition:
        return ServiceDefinition(
            name="tyr",
            description="Autonomous saga coordinator",
            factory=_TyrService,
            default_enabled=True,
            depends_on=["postgres", "volundr"],
        )

    def create_service(self) -> Service:
        return self.register_service().factory()

    def create_api_app(self) -> Any:
        from tyr.main import create_app

        return create_app()

    def api_route_domains(self) -> tuple[APIRouteDomain, ...]:
        return (
            APIRouteDomain(
                name="tracker-api",
                prefixes=(
                    "/api/v1/tracker/projects",
                    "/api/v1/tracker/import",
                ),
                description="Canonical tracker project browsing and import routes.",
            ),
            APIRouteDomain(
                name="saga-api",
                prefixes=("/api/v1/tyr/sagas",),
                description="Saga planning and saga lifecycle routes.",
            ),
            APIRouteDomain(
                name="review-api",
                prefixes=(
                    "/api/v1/tyr/raids",
                    "/api/v1/tyr/sessions",
                ),
                description="Raid review, raid messaging, and approval-session routes.",
            ),
            APIRouteDomain(
                name="dispatch-api",
                prefixes=(
                    "/api/v1/tyr/dispatch",
                    "/api/v1/tyr/dispatcher",
                ),
                description="Dispatch queue, approvals, dispatcher state, and activity log routes.",
            ),
            APIRouteDomain(
                name="workflow-api",
                prefixes=(
                    "/api/v1/tyr/flock",
                    "/api/v1/tyr/flock_flows",
                    "/api/v1/tyr/pipelines",
                ),
                description="Flock configuration, flow library, and pipeline execution routes.",
            ),
            APIRouteDomain(
                name="settings-api",
                prefixes=("/api/v1/tyr/settings",),
                description="Tyr settings for flock, dispatch defaults, and notifications.",
            ),
            APIRouteDomain(
                name="integrations-api",
                prefixes=(
                    "/api/v1/tyr/integrations",
                    "/api/v1/tyr/telegram",
                ),
                description="Tyr integration management and Telegram setup routes.",
            ),
            APIRouteDomain(
                name="event-api",
                prefixes=("/api/v1/tyr/events",),
                description="Tyr SSE event stream routes.",
            ),
            APIRouteDomain(
                name="tyr-api",
                prefixes=("/api/v1/tyr",),
                description="Tyr coordination and raid routes.",
            ),
        )

    def create_api_client(self) -> Any:
        return CLIAPIClient(base_url="http://localhost:8080", service_name="Tyr")

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
            resp = client.request_or_exit("GET", "/api/v1/tyr/sagas")
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
                    ("raid_count", "Raids"),
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
                "/api/v1/tyr/sagas/commit",
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
                "/api/v1/tyr/dispatch/approve",
                json_body={"saga_id": saga_id},
            )
            data = resp.json()

            if json_output:
                print_json(data)
                return

            print_success(f"Saga {saga_id} dispatched.")

        # ── Raids ──────────────────────────────────────────────────── #

        raids = typer.Typer(
            name="raids",
            help="Manage raids.",
            no_args_is_help=True,
        )

        @raids.command()
        def active(
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """List active raids."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("GET", "/api/v1/tyr/raids/active")
            data = resp.json()

            if json_output:
                print_json(data)
                return

            if not data:
                typer.echo("No active raids.")
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

        @raids.command()
        def approve(
            raid_id: str = typer.Argument(help="Raid ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Approve a pending raid."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/tyr/raids/{raid_id}/approve")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "approved"})
                return

            print_success(f"Raid {raid_id} approved.")

        @raids.command()
        def reject(
            raid_id: str = typer.Argument(help="Raid ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Reject a pending raid."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/tyr/raids/{raid_id}/reject")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "rejected"})
                return

            print_success(f"Raid {raid_id} rejected.")

        @raids.command()
        def retry(
            raid_id: str = typer.Argument(help="Raid ID"),
            json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
        ) -> None:
            """Retry a failed raid."""
            client = plugin.create_api_client()
            resp = client.request_or_exit("POST", f"/api/v1/tyr/raids/{raid_id}/retry")

            if json_output:
                print_json(resp.json() if resp.text else {"status": "retrying"})
                return

            print_success(f"Raid {raid_id} retry initiated.")

        app.add_typer(sagas, name="sagas")
        app.add_typer(raids, name="raids")

    def tui_pages(self) -> Sequence[TUIPageSpec]:
        """Return TUI page specs for the Textual app."""
        from tyr.tui.pages.dispatch import DispatchPage
        from tyr.tui.pages.raids import RaidsPage
        from tyr.tui.pages.review import ReviewPage
        from tyr.tui.pages.sagas import SagasPage

        return [
            TUIPageSpec(name="Sagas", icon="⚡", widget_class=SagasPage),
            TUIPageSpec(name="Raids", icon="⚔", widget_class=RaidsPage),
            TUIPageSpec(name="Dispatch", icon="🚀", widget_class=DispatchPage),
            TUIPageSpec(name="Review", icon="👁", widget_class=ReviewPage),
        ]
