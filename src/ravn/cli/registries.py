"""ravn personas / ravn profiles — discovery for the two registry flags.

``--persona`` and ``--profile`` are accepted by most Ravn commands, but until
now the only way to enumerate personas from a terminal was ``ravn flock list``
— filed under the wrong command — and profiles had no listing at all.  These
sub-apps make both flags discoverable.

Persona picks *who* a Ravn is (system prompt, tools, event subscriptions);
profile picks *how* it is deployed (location, MCP servers, Mímir mounts,
checkpointing).
"""

from __future__ import annotations

import typer

from ravn.adapters.personas.loader import FilesystemPersonaAdapter
from ravn.adapters.profiles.loader import ProfileLoader

personas_app = typer.Typer(
    name="personas",
    help="Inspect the personas available to --persona.",
    add_completion=False,
)

profiles_app = typer.Typer(
    name="profiles",
    help="Inspect the profiles available to --profile.",
    add_completion=False,
)


@personas_app.command("list")
def personas_list(
    builtin_only: bool = typer.Option(False, "--builtin", help="List only the bundled personas."),
) -> None:
    """List persona names resolvable by ``--persona``.

    \b
    Examples:
      ravn personas list            — every resolvable persona
      ravn personas list --builtin  — only the bundled set
    """
    loader = FilesystemPersonaAdapter()
    names = loader.list_builtin_names() if builtin_only else loader.list_names()
    if not names:
        typer.echo("No personas found.", err=True)
        raise typer.Exit(1)

    for name in names:
        typer.echo(f"{name:<40} {loader.source(name)}")


@profiles_app.command("list")
def profiles_list(
    builtin_only: bool = typer.Option(False, "--builtin", help="List only the built-in profiles."),
) -> None:
    """List profile names resolvable by ``--profile``.

    \b
    Examples:
      ravn profiles list            — every resolvable profile
      ravn profiles list --builtin  — only the built-in set
    """
    loader = ProfileLoader()
    names = loader.list_builtin_names() if builtin_only else loader.list_names()
    if not names:
        typer.echo("No profiles found.", err=True)
        raise typer.Exit(1)

    builtin = set(loader.list_builtin_names())
    for name in names:
        typer.echo(f"{name:<40} {'builtin' if name in builtin else 'user'}")
