"""``niuu guild`` commands — operator-side Guild administration, and this
host's own periodic heartbeat once it has joined.

``niuu guild pair`` mints the single-use pairing code an operator hands to a
new machine for ``niuu join``. Joining and leaving a Guild from the new
machine's own side live at the top level (``niuu join`` / ``niuu leave`` in
``cli.app``) since they are what that machine does, not something it asks a
Guild administrator to do.
"""

from __future__ import annotations

import typer

from cli.config import CLISettings


def create_guild_commands(settings: CLISettings) -> typer.Typer:
    """Create the ``guild`` command group."""
    guild_app = typer.Typer(
        name="guild",
        help="Administer a Guild instance registry.",
        no_args_is_help=True,
    )

    @guild_app.command()
    def pair(
        guild_url: str = typer.Argument(help="Base URL of the Guild to pair a new machine into."),
        allow_plaintext: bool = typer.Option(
            False,
            "--allow-plaintext",
            help="Let the joining node register a plaintext (http://) instance URL.",
        ),
        allow_untrusted_node_auth: bool = typer.Option(
            False,
            "--allow-untrusted-node-auth",
            help=(
                "Let a node with host_auth.mode: none join even though this Guild runs "
                "host_auth.mode: oidc (Guild would forward real user bearer tokens to it)."
            ),
        ),
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
            minted = asyncio.run(
                mint_pairing_code(
                    guild_url,
                    access_token=tokens.access_token,
                    allow_plaintext=allow_plaintext,
                    allow_untrusted_node_auth=allow_untrusted_node_auth,
                )
            )
        except GuildAPIError as exc:
            typer.echo(f"Failed to mint a pairing code: {exc}")
            raise typer.Exit(1) from None

        typer.echo(f"Pairing code (expires {minted['expiresAt']}):")
        typer.echo(minted["code"])
        typer.echo("")
        typer.echo(f"On the new machine, run: niuu join {guild_url} --code <code above>")

    @guild_app.command()
    def heartbeat(
        once: bool = typer.Option(
            False, "--once", help="Send a single heartbeat and exit instead of looping."
        ),
    ) -> None:
        """Send this host's signed presence heartbeat to the Guild it joined.

        Run under a supervisor for continuous presence; `--once` sends a
        single heartbeat (e.g. for cron or manual checks).
        """
        import asyncio

        from cli.auth.node_key import NodeKeyMissingError
        from cli.services.guild_heartbeat import NotJoinedError, run_heartbeat_loop

        try:
            asyncio.run(run_heartbeat_loop(settings, iterations=1 if once else None))
        except (NotJoinedError, NodeKeyMissingError) as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        except KeyboardInterrupt:
            raise typer.Exit(0) from None

    return guild_app
