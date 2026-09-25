"""Typer app factory — discovers plugins and mounts commands.

New command tree (NIU-405):

  Platform:
    platform up|down|status|init    — lifecycle (dynamic service flags)
    up|down|status                  — shortcuts that forward to `platform ...`
                                      with default flags; `up` drives the Docker
                                      compose bundle when mode is `docker`
    doctor                          — host checks for the configured mode

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


def _register_lifecycle_aliases(
    app: typer.Typer,
    platform_app: typer.Typer,
    settings: CLISettings,
) -> None:
    """Top-level ``up``/``down``/``status``/``doctor`` shortcuts.

    ``niuu up`` is what the installer runs; in docker mode it drives the compose
    bundle, otherwise it forwards to ``niuu platform up`` with default flags.
    """
    platform_click = typer.main.get_command(platform_app)

    def _effective(mode: str) -> CLISettings:
        if not mode or mode == settings.mode:
            return settings
        return settings.model_copy(update={"mode": mode})

    def _forward(name: str, args: list[str]) -> None:
        # Re-enter the platform subcommand with its own argument parsing so the
        # dynamic per-service flags keep their defaults.
        platform_click.commands[name].main(
            args=args,
            prog_name=f"niuu platform {name}",
            standalone_mode=False,
        )

    @app.command()
    def up(
        mode: str = typer.Option(
            "",
            "--mode",
            help="Override the configured mode for this run (mini, openshell, cluster, docker).",
        ),
        skip_preflight: bool = typer.Option(False, help="Skip the host preflight checks."),
    ) -> None:
        """Start the platform.

        Shortcut for `niuu platform up` with default service flags. In docker
        mode it renders the compose bundle under ~/.niuu/docker, runs
        `docker compose up -d` and waits for the health endpoint; use
        `niuu platform up --<service>` flags when you need per-service control.
        """
        effective = _effective(mode)
        if effective.mode == "docker":
            from cli.commands.stack import stack_up

            stack_up(effective, skip_preflight=skip_preflight)
            return
        _forward("up", ["--skip-preflight"] if skip_preflight else [])

    @app.command()
    def down() -> None:
        """Stop the platform. Same as `niuu platform down`."""
        _forward("down", [])

    @app.command()
    def status() -> None:
        """Show platform status. Same as `niuu platform status`."""
        _forward("status", [])

    @app.command()
    def doctor(
        mode: str = typer.Option(
            "",
            "--mode",
            help="Check the host for this mode instead of the configured one.",
        ),
    ) -> None:
        """Check this host can run the platform in the configured mode.

        Prints the same preflight `niuu up` runs (Docker, GPU, disk, ports, ...
        in docker mode; claude binary, embedded database, ... in mini mode)
        without starting anything. Exit code 1 when a check fails.
        """
        from cli.commands.stack import run_doctor

        if not run_doctor(_effective(mode)):
            raise typer.Exit(1)


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

    @app.command()
    def join(
        guild_url: str = typer.Argument(help="Base URL of the Guild to join."),
        code: str = typer.Option(..., "--code", help="Pairing code from 'niuu guild pair'."),
        name: str = typer.Option(
            "", "--name", help="Display name for this node (default: this host's hostname)."
        ),
    ) -> None:
        """Join this machine to a Guild using a pairing code from an operator."""
        import asyncio
        import os
        from pathlib import Path

        from cli.api.guild import GuildAPIError
        from cli.api.guild import join as guild_join
        from cli.auth.node_key import DEFAULT_NODE_KEY_FILENAME, NodeIdentity
        from cli.commands.node_instances import UnreachableHostError, offered_instances_for_host
        from cli.config import DEFAULT_CONFIG_DIR, persist_guild_join

        node_name = name.strip() or os.uname().nodename
        try:
            offered = offered_instances_for_host(settings)
        except UnreachableHostError as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        identity = NodeIdentity.load_or_create(Path(DEFAULT_CONFIG_DIR) / DEFAULT_NODE_KEY_FILENAME)
        try:
            result = asyncio.run(
                guild_join(
                    guild_url,
                    code=code,
                    node_name=node_name,
                    public_key=identity.public_key_b64,
                    node_auth_mode=settings.host_auth.mode,
                    instances=offered,
                )
            )
        except GuildAPIError as exc:
            typer.echo(f"Failed to join {guild_url}: {exc}")
            raise typer.Exit(1) from None

        # Adopt Guild's own identity trust — this host now verifies the same
        # OIDC issuer(s) Guild does (or, under host_auth.mode: none, none at
        # all) rather than leaving the returned config unapplied.
        persist_guild_join(
            url=guild_url, node_id=result["nodeId"], identity_trust=result["identity"]
        )
        typer.echo(f"Joined {guild_url} as node {result['nodeId']} ({node_name}).")
        typer.echo(
            "Run `niuu guild heartbeat` (e.g. under a supervisor) to keep this node's "
            "presence and offered instances current."
        )

    @app.command()
    def leave() -> None:
        """Remove this machine from the Guild it previously joined."""
        import asyncio
        from pathlib import Path

        from cli.api.guild import GuildAPIError
        from cli.api.guild import leave as guild_leave
        from cli.auth.node_key import DEFAULT_NODE_KEY_FILENAME, NodeIdentity, NodeKeyMissingError
        from cli.config import DEFAULT_CONFIG_DIR, clear_guild_join

        if not settings.guild.url or not settings.guild.node_id:
            typer.echo("This host has not joined a Guild.")
            raise typer.Exit(1)

        try:
            identity = NodeIdentity.load(Path(DEFAULT_CONFIG_DIR) / DEFAULT_NODE_KEY_FILENAME)
        except NodeKeyMissingError as exc:
            typer.echo(str(exc))
            typer.echo(
                "Refusing to generate a new key: it would not match what Guild has on "
                "file. Ask an admin to revoke this node instead "
                "(DELETE /api/v1/niuu/guild/nodes/{id})."
            )
            raise typer.Exit(1) from None
        try:
            asyncio.run(
                guild_leave(settings.guild.url, node_id=settings.guild.node_id, identity=identity)
            )
        except GuildAPIError as exc:
            typer.echo(f"Failed to leave {settings.guild.url}: {exc}")
            raise typer.Exit(1) from None

        clear_guild_join()
        typer.echo(f"Left {settings.guild.url}.")

    from cli.commands.guild import create_guild_commands

    app.add_typer(create_guild_commands(settings), name="guild")

    platform_app = create_platform_commands(registry, settings, manager)
    app.add_typer(platform_app, name="platform")
    _register_lifecycle_aliases(app, platform_app, settings)

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
