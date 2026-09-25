"""``niuu guild`` commands — operator-side Guild administration.

``niuu guild pair`` mints the single-use pairing code an operator hands to a
new machine for ``niuu join``. Joining and leaving a Guild from the new
machine's own side live at the top level (``niuu join`` / ``niuu leave`` in
``cli.app``) since they are what that machine does, not something it asks a
Guild administrator to do.
"""

from __future__ import annotations

import typer


def create_guild_commands() -> typer.Typer:
    """Create the ``guild`` command group."""
    guild_app = typer.Typer(
        name="guild",
        help="Administer a Guild instance registry.",
        no_args_is_help=True,
    )

    @guild_app.command()
    def pair(
        guild_url: str = typer.Argument(help="Base URL of the Guild to pair a new machine into."),
    ) -> None:
        """Mint a single-use pairing code for `niuu join` on another machine."""
        import asyncio

        from cli.api.guild import GuildAPIError, mint_pairing_code
        from cli.auth.credentials import CredentialStore

        tokens = CredentialStore().load()
        if tokens is None:
            typer.echo("Not authenticated. Run 'niuu login' first.")
            raise typer.Exit(1)

        try:
            minted = asyncio.run(mint_pairing_code(guild_url, access_token=tokens.access_token))
        except GuildAPIError as exc:
            typer.echo(f"Failed to mint a pairing code: {exc}")
            raise typer.Exit(1) from None

        typer.echo(f"Pairing code (expires {minted['expiresAt']}):")
        typer.echo(minted["code"])
        typer.echo("")
        typer.echo(f"On the new machine, run: niuu join {guild_url} --code <code above>")

    return guild_app
