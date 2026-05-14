"""Typer app factory — discovers plugins and mounts commands.

New command tree (NIU-405):

  Platform:
    platform up|down|status|init    — lifecycle (dynamic service flags)

  Workflow (registered by plugins at top level):
    sessions list|create|stop|delete
    sagas    list|create|dispatch
    runs    active|approve|reject|retry

  Identity:
    login / logout / whoami

  Configuration:
    config  show|set
    context list|use|add|delete

  Other:
    tui
    version
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import click
import typer

from cli.config import CLISettings
from cli.registry import PluginRegistry
from cli.services.manager import ServiceManager


class _SortedGroup(typer.core.TyperGroup):
    """TyperGroup that lists commands in alphabetical order."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(super().list_commands(ctx))


logger = logging.getLogger(__name__)


def build_app(
    settings: CLISettings | None = None,
    registry: PluginRegistry | None = None,
) -> typer.Typer:
    """Build the niuu CLI app with plugin discovery.

    Parameters are injectable for testing. When None, defaults are created.
    """
    if settings is None:
        settings = CLISettings()

    if registry is None:
        registry = PluginRegistry()
        registry.discover_entry_points()
        registry.discover_config(settings.plugins.extra)
        registry.apply_config(settings.plugins.enabled)

    from cli.commands.platform import _print_status

    manager = ServiceManager(
        registry=registry,
        health_check_interval=settings.services.health_check_interval_seconds,
        health_check_timeout=settings.services.health_check_timeout_seconds,
        health_check_max_retries=settings.services.health_check_max_retries,
        on_status_change=_print_status,
    )

    app = typer.Typer(
        name="niuu",
        help="The Niuu platform CLI.",
        no_args_is_help=True,
        invoke_without_command=True,
        rich_markup_mode=None,
        cls=_SortedGroup,
    )

    # ------------------------------------------------------------------ #
    # Global flags                                                         #
    # ------------------------------------------------------------------ #
    def _version_callback(value: bool) -> None:
        if value:
            typer.echo(f"niuu {settings.version}")
            raise typer.Exit()

    def _home_callback(value: str) -> None:
        if value:
            os.environ["NIUU_HOME"] = value
            config_path = str(Path(value) / "config.yaml")
            os.environ["NIUU_CONFIG"] = config_path

    def _config_callback(value: str) -> None:
        if value:
            os.environ["NIUU_CONFIG"] = value

    @app.callback()
    def main(
        version: bool = typer.Option(
            False,
            "--version",
            "-V",
            help="Print version and exit.",
            callback=_version_callback,
            is_eager=True,
        ),
        home: str = typer.Option(
            "",
            "--home",
            help="Config directory (default: ~/.niuu, env NIUU_HOME).",
            callback=_home_callback,
            is_eager=True,
            envvar="NIUU_HOME",
        ),
        config: str = typer.Option(
            "",
            "--config",
            help="Config file path (default: ~/.niuu/config.yaml).",
            callback=_config_callback,
            is_eager=True,
        ),
    ) -> None:
        """The Niuu platform CLI."""

    # ------------------------------------------------------------------ #
    # Commands are registered in alphabetical order so that --help        #
    # output is sorted.                                                   #
    # ------------------------------------------------------------------ #
    from cli.commands.core import create_config_commands, create_context_commands
    from cli.commands.platform import create_platform_commands

    app.add_typer(create_config_commands(registry, settings), name="config")
    app.add_typer(create_context_commands(settings), name="context")

    @app.command()
    def login(
        issuer: str = typer.Option(
            "",
            "--issuer",
            "-i",
            help="OIDC issuer URL.",
            envvar="NIUU_OIDC_ISSUER",
        ),
        client_id: str = typer.Option(
            "niuu-cli",
            "--client-id",
            help="OIDC client ID.",
            envvar="NIUU_OIDC_CLIENT_ID",
        ),
    ) -> None:
        """Authenticate with the Niuu platform."""
        if not issuer:
            typer.echo("Error: --issuer is required (or set NIUU_OIDC_ISSUER).")
            raise typer.Exit(1)
        import asyncio

        from cli.auth.oidc import OIDCClient

        oidc = OIDCClient(issuer=issuer, client_id=client_id)
        typer.echo("Opening browser for authentication...")
        try:
            tokens = asyncio.get_event_loop().run_until_complete(oidc.login())
            typer.echo(f"Authenticated successfully (token type: {tokens.token_type}).")
        except Exception as exc:
            typer.echo(f"Authentication failed: {exc}")
            raise typer.Exit(1) from None

    @app.command()
    def logout() -> None:
        """Clear stored credentials."""
        from cli.auth.credentials import CredentialStore

        CredentialStore().clear()
        typer.echo("Logged out.")

    app.add_typer(create_platform_commands(registry, settings, manager), name="platform")

    # Plugin workflow commands (top-level, registered by each plugin)
    for name, plugin in sorted(registry.plugins.items()):
        try:
            plugin.register_commands(app)
        except Exception:
            logger.exception("failed to register commands for plugin: %s", name)

    @app.command()
    def tui() -> None:
        """Launch the interactive TUI."""
        from cli.tui.app import build_tui

        tui_app = build_tui(registry=registry, theme=settings.tui.theme)
        tui_app.run()

    @app.command()
    def version() -> None:
        """Print the niuu CLI version."""
        typer.echo(f"niuu {settings.version}")

    @app.command()
    def whoami() -> None:
        """Show the currently authenticated user."""
        from cli.auth.oidc import OIDCClient

        oidc = OIDCClient(issuer="", client_id="")
        claims = oidc.whoami()
        if not claims:
            typer.echo("Not authenticated. Run 'niuu login' first.")
            raise typer.Exit(1)
        name = claims.get("name", claims.get("preferred_username", "unknown"))
        email = claims.get("email", "")
        sub = claims.get("sub", "")
        typer.echo(f"User:  {name}")
        if email:
            typer.echo(f"Email: {email}")
        typer.echo(f"Sub:   {sub}")

    return app
