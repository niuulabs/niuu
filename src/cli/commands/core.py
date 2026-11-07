"""Core commands: version, config, context, login/logout/whoami.

These are always present regardless of which plugins are loaded.
Platform lifecycle (up, down, status, init) lives in platform.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from cli.config import CLISettings
    from cli.registry import PluginRegistry


def create_config_commands(
    registry: PluginRegistry,
    settings: CLISettings,
) -> typer.Typer:
    """Create the ``config`` command group."""
    config_app = typer.Typer(
        name="config",
        help="Show or update configuration.",
        no_args_is_help=True,
    )

    @config_app.command()
    def show() -> None:
        """Show current CLI configuration."""
        typer.echo(f"Mode: {settings.mode}")
        typer.echo(f"Server: {settings.server.host}:{settings.server.port}")
        typer.echo(f"Database: {settings.database.mode}")
        typer.echo(f"Pod manager: {settings.pod_manager.adapter}")
        from cli.context import ContextError, active

        try:
            typer.echo(f"Context: {settings.context} ({active(settings).url})")
        except ContextError:
            typer.echo(f"Context: {settings.context} (not configured: run `niuu context use`)")
        typer.echo(f"Theme: {settings.tui.theme}")
        plugins = registry.plugins
        disabled = registry.all_plugins.keys() - plugins.keys()
        typer.echo(f"Plugins enabled: {', '.join(sorted(plugins)) or 'none'}")
        if disabled:
            typer.echo(f"Plugins disabled: {', '.join(sorted(disabled))}")

    @config_app.command()
    def set(
        key: str = typer.Argument(help="Config key (e.g. server.port)"),
        value: str = typer.Argument(help="New value"),
    ) -> None:
        """Set a configuration value."""
        typer.echo(f"Set {key} = {value}  (persists to ~/.niuu/config.yaml)")

    return config_app


def create_context_commands(settings: CLISettings) -> typer.Typer:
    """Create the ``context`` command group.

    A context is a named Niuu host: its URL, and optionally the Forge to use on it and
    the OIDC issuer of its login. ``local`` (this machine's own server) is built in.
    Every command that talks to a Niuu host uses the active one.
    """
    context_app = typer.Typer(
        name="context",
        help="Manage server contexts (named Niuu hosts).",
        no_args_is_help=True,
    )

    def _fail(message: str) -> None:
        typer.echo(f"error: {message}", err=True)
        raise typer.Exit(1)

    @context_app.command("list")
    def list_contexts() -> None:
        """List all configured contexts; * marks the active one."""
        from cli.context import contexts

        rows = [["", "NAME", "URL", "FORGE", "ISSUER"]]
        for item in contexts(settings):
            rows.append(
                [
                    "*" if item.name == settings.context else "",
                    item.name,
                    f"{item.url} (this machine)" if item.local else item.url,
                    item.forge or "-",
                    item.issuer or "-",
                ]
            )
        widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
        for row in rows:
            typer.echo("  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip())
        if all(item.name != settings.context for item in contexts(settings)):
            _fail(
                f"the active context {settings.context!r} is not configured; "
                "run `niuu context use <name>`"
            )

    @context_app.command()
    def use(
        name: str = typer.Argument(help="Context name to activate"),
    ) -> None:
        """Switch to a context."""
        from cli.config import persist_contexts
        from cli.context import ContextError, find

        try:
            context = find(settings, name)
        except ContextError as exc:
            _fail(str(exc))
        persist_contexts(active=name)
        typer.echo(f"Switched to context '{name}' ({context.url}).")

    @context_app.command()
    def add(
        name: str = typer.Argument(help="Context name"),
        url: str = typer.Argument(help="Server URL"),
        forge: str = typer.Option(
            "", "--forge", help="The Forge (id, slug or name) to use there when it has several."
        ),
        issuer: str = typer.Option(
            "", "--issuer", help="OIDC issuer of its login (`niuu login` uses it)."
        ),
        activate: bool = typer.Option(False, "--use", help="Switch to it as well."),
    ) -> None:
        """Add a new server context."""
        from pydantic import ValidationError

        from cli.config import ContextConfig, persist_contexts, validate_context_name

        try:
            validate_context_name(name)
            context = ContextConfig(url=url, forge=forge, issuer=issuer)
        except ValidationError as exc:
            _fail("; ".join(str(err["msg"]).removeprefix("Value error, ") for err in exc.errors()))
        except ValueError as exc:
            _fail(str(exc))
        if name in settings.contexts:
            _fail(
                f"context '{name}' already exists; delete it first (`niuu context delete {name}`)"
            )
        persist_contexts(
            contexts={**settings.contexts, name: context},
            active=name if activate else None,
        )
        typer.echo(f"Added context '{name}' → {context.url}")
        if activate:
            typer.echo(f"Switched to context '{name}'.")

    @context_app.command()
    def delete(
        name: str = typer.Argument(help="Context name to remove"),
    ) -> None:
        """Remove a context."""
        from cli.config import LOCAL_CONTEXT, persist_contexts

        if name == LOCAL_CONTEXT:
            _fail(f"'{LOCAL_CONTEXT}' is this machine's own server and cannot be removed")
        if name not in settings.contexts:
            _fail(f"context '{name}' is not configured")
        if name == settings.context:
            _fail(
                f"'{name}' is the active context; switch first (`niuu context use {LOCAL_CONTEXT}`)"
            )
        persist_contexts(contexts={k: v for k, v in settings.contexts.items() if k != name})
        typer.echo(f"Removed context '{name}'.")

    return context_app
