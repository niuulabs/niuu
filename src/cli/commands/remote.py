"""``niuu remote`` — join machines to a Forge as registered hosts, from the operator's machine.

The operator's own login reaches the Forge through the Niuu host's facade; the operator's
own ``ssh`` reaches the machine. See ``cli.services.remote_hosts`` and the design's
"From the operator's machine: `niuu remote`".
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable, Coroutine
from typing import Any

import typer

from cli.config import CLISettings

ISOLATION_MODES = ("measured", "systemd", "cgroupfs", "none")
#: The exit status of a command stopped with Ctrl-C (128 + SIGINT).
EXIT_INTERRUPTED = 130


def _echo(line: str) -> None:
    typer.echo(line)


def _fail(message: str) -> None:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def build_api(settings: CLISettings, url: str):  # noqa: ANN201 - lazy import keeps --help fast
    """The Niuu host's compute-host API as the operator who ran `niuu login`."""
    from cli.api.client import APIClient
    from cli.api.compute_hosts import ComputeHostsAPI
    from cli.auth.credentials import CredentialStore
    from cli.auth.oidc import OIDCClient

    base = url or settings.remote.url or f"http://{settings.server.host}:{settings.server.port}"
    store = CredentialStore()
    tokens = store.load()
    refresh = None
    if tokens and tokens.refresh_token and tokens.issuer and tokens.client_id:
        refresh = OIDCClient(
            issuer=tokens.issuer, client_id=tokens.client_id, credential_store=store
        ).refresh
    client = APIClient(
        base, access_token=tokens.access_token if tokens else None, refresh_token_fn=refresh
    )
    return ComputeHostsAPI(client)


def _parse_slots(raw: str) -> tuple[int | None, bool]:
    """``--slots``: a count, ``suggested``, or empty (the host waits for one)."""
    value = raw.strip()
    if not value:
        return None, False
    if value == "suggested":
        return None, True
    if not value.isdigit() or int(value) < 1:
        raise typer.BadParameter("a positive slot count, or 'suggested'", param_hint="--slots")
    return int(value), False


def _run(main: Callable[[], Coroutine[Any, Any, None]]) -> None:
    from cli.api.compute_hosts import ForgeAPIError
    from cli.services.remote_hosts import RemoteError

    try:
        asyncio.run(main())
    except (RemoteError, ForgeAPIError) as exc:
        _fail(str(exc))


def create_remote_commands(settings: CLISettings) -> typer.Typer:
    """Create the ``remote`` command group."""
    remote_app = typer.Typer(
        name="remote",
        help="Join other machines to a Forge as compute hosts, over your own SSH.",
        no_args_is_help=True,
    )

    url_option = typer.Option(
        "", "--url", help="The Niuu host (default: remote.url, else this machine's server)."
    )
    forge_option = typer.Option(
        "", "--forge", help="The Forge's id, slug or name (needed when there are several)."
    )
    port_option = typer.Option(None, "--port", "-p", help="SSH port on the machine.")
    jump_option = typer.Option("", "--jump", "-J", help="SSH jump host, as `ssh -J` takes it.")

    @remote_app.command()
    def join(
        destination: str = typer.Argument(
            help="The machine: user@host, an ~/.ssh/config alias, or ssh://user@host:port."
        ),
        port: int | None = port_option,
        jump: str = jump_option,
        url: str = url_option,
        forge: str = forge_option,
        execution: str = typer.Option(
            "", "--execution", help="The host's runtime (default: the Forge's only one)."
        ),
        connector: str = typer.Option(
            "", "--connector", help="How Forge reaches the host (default: its only one)."
        ),
        tag: list[str] = typer.Option([], "--tag", help="A host tag; repeat for several."),
        slots: str = typer.Option(
            "", "--slots", help="Slot count, or 'suggested' to take Forge's after the audit."
        ),
        isolation: str = typer.Option(
            "",
            "--isolation",
            help="Slot isolation: measured (Forge's default), systemd, cgroupfs or none.",
        ),
        no_follow: bool = typer.Option(
            False, "--no-follow", help="Stop once the host registered; do not follow it."
        ),
        timeout: float | None = typer.Option(
            None, "--timeout", help="Seconds to follow to ready (remote.follow_timeout_seconds)."
        ),
    ) -> None:
        """Mint a code, run join.sh on the machine over SSH, and follow it until ready.

        Exit 0 when the host is ready or waits for its slot count; 1 when the join,
        the host's audit or its connection fails, or it is not ready within --timeout.
        """
        from cli.services.remote_hosts import RemoteError, parse_target

        if isolation and isolation not in ISOLATION_MODES:
            raise typer.BadParameter(
                f"one of {', '.join(ISOLATION_MODES)}", param_hint="--isolation"
            )
        count, suggested = _parse_slots(slots)
        try:
            target = parse_target(destination, port=port, jump=jump)
        except RemoteError as exc:
            raise typer.BadParameter(str(exc), param_hint="DESTINATION") from exc
        follow_timeout = settings.remote.follow_timeout_seconds if timeout is None else timeout

        async def main() -> None:
            await _join(
                settings,
                target,
                url=url,
                forge=forge,
                execution=execution,
                connector=connector,
                tags=list(tag),
                slots=count,
                slots_suggested=suggested,
                isolation=isolation,
                follow=not no_follow,
                timeout_seconds=follow_timeout,
            )

        try:
            _run(main)
        except KeyboardInterrupt:
            typer.echo("\nStopped following. Forge goes on; see `niuu remote hosts`.", err=True)
            raise typer.Exit(EXIT_INTERRUPTED) from None

    @remote_app.command()
    def hosts(
        host: str = typer.Argument("", help="One host's name or id, to show its steps."),
        url: str = url_option,
        forge: str = forge_option,
        json_output: bool = typer.Option(False, "--json", help="Print Forge's answer as JSON."),
    ) -> None:
        """List the Forge's registered hosts, or show one host's join steps and findings."""

        async def main() -> None:
            from cli.services.remote_hosts import find_host, resolve_forge

            api = build_api(settings, url)
            instance = resolve_forge(await api.forges(), forge)
            overview = await api.overview(instance["id"])
            if host:
                shown = find_host(overview.get("hosts") or [], host)
                if json_output:
                    typer.echo(json.dumps(shown, indent=2))
                    return
                _print_host(shown)
                return
            if json_output:
                typer.echo(json.dumps(overview, indent=2))
                return
            _print_pool(instance, overview)

        _run(main)

    @remote_app.command()
    def slots(
        host: str = typer.Argument(help="The host's name or id."),
        count: int = typer.Argument(min=1, help="Slot count."),
        url: str = url_option,
        forge: str = forge_option,
    ) -> None:
        """Set a host's slot count (what the console's *Put in service* sends)."""

        async def main() -> None:
            from cli.services.remote_hosts import find_host, resolve_forge

            api = build_api(settings, url)
            instance = resolve_forge(await api.forges(), forge)
            found = find_host((await api.overview(instance["id"])).get("hosts") or [], host)
            updated = await api.set_slots(instance["id"], found["id"], count)
            typer.echo(f"✓ {updated['name']}: {updated['slots']} slots, {updated['state']}")

        _run(main)

    @remote_app.command()
    def leave(
        host: str = typer.Argument(help="The host's name or id (see `niuu remote hosts`)."),
        ssh: str = typer.Option(
            "", "--ssh", help="SSH destination of the machine (default: the host's name)."
        ),
        port: int | None = port_option,
        jump: str = jump_option,
        url: str = url_option,
        forge: str = forge_option,
        timeout: float | None = typer.Option(
            None,
            "--timeout",
            help="Seconds to wait for its sessions to end (remote.leave_timeout_seconds).",
        ),
    ) -> None:
        """Drain and remove a host, then run niuu-host-leave on it over SSH."""
        leave_timeout = settings.remote.leave_timeout_seconds if timeout is None else timeout

        async def main() -> None:
            await _leave(
                settings,
                host,
                ssh=ssh,
                port=port,
                jump=jump,
                url=url,
                forge=forge,
                timeout_seconds=leave_timeout,
            )

        try:
            _run(main)
        except KeyboardInterrupt:
            typer.echo(
                "\nStopped waiting. Forge still removes the host once it is empty.", err=True
            )
            raise typer.Exit(EXIT_INTERRUPTED) from None

    return remote_app


async def _join(
    settings: CLISettings,
    target: Any,
    *,
    url: str,
    forge: str,
    execution: str,
    connector: str,
    tags: list[str],
    slots: int | None,
    slots_suggested: bool,
    isolation: str,
    follow: bool,
    timeout_seconds: float,
) -> None:
    from cli.services.remote_hosts import (
        RemoteError,
        follow_join,
        mint_body,
        new_host,
        remote_sh,
        resolve_forge,
        run_streaming,
    )

    api = build_api(settings, url)
    instance = resolve_forge(await api.forges(), forge)
    overview = await api.overview(instance["id"])
    body = mint_body(
        overview,
        execution=execution,
        connector=connector,
        tags=tags,
        slots=slots,
        isolation=isolation,
    )
    before = {h["id"] for h in overview.get("hosts") or []}
    minted = await api.mint(instance["id"], body)
    forge_name = instance.get("name") or instance.get("slug")
    _echo(
        f"✓ minted pairing code for {body['execution']} on {forge_name} "
        f"(expires {minted['expires_at']})"
    )
    _echo(f"→ {target.destination}: running join.sh")
    run = run_streaming(target.argv(remote_sh(minted["command"]), tty=sys.stdin.isatty()))
    hosts = (await api.overview(instance["id"])).get("hosts") or []
    if run.returncode != 0:
        joined = [h for h in hosts if h["id"] not in before]
        if not joined:
            await _revoke_unused(api, instance["id"], minted["id"])
        what = "ssh" if run.returncode == 255 else "join.sh"
        raise RemoteError(
            f"{what} failed on {target.destination} (exit {run.returncode}): "
            f"{run.last_line or 'no output'}"
        )
    host = new_host(before, hosts, target.destination)
    if not follow:
        _echo(f"✓ joined       {host['name']} ({host['id']})")
        _echo(f"Follow it with `niuu remote hosts {host['name']}`.")
        return
    result = await follow_join(
        api,
        instance["id"],
        host["id"],
        slots_suggested=slots_suggested,
        timeout_seconds=timeout_seconds,
        poll_seconds=settings.remote.poll_interval_seconds,
        echo=_echo,
    )
    name = result.host["name"]
    if result.outcome == "needs_capacity":
        suggestion = result.host.get("suggested_slots")
        _echo(f"{name} is audited and waits for its slot count.")
        if suggestion:
            _echo(f"Forge suggests {suggestion['slots']}:")
            for line in suggestion.get("lines") or []:
                _echo(f"  {line}")
            _echo(f"Set it: niuu remote slots {name} {suggestion['slots']}")
            return
        _echo(f"Set it: niuu remote slots {name} <n>")
        return
    _echo(f"Next: `niuu remote hosts {name}` · leave with `niuu remote leave {name}`")


async def _revoke_unused(api: Any, instance_id: str, code_id: str) -> None:
    """Revoke a code the failed run did not use; a failed revoke is reported, not hidden."""
    from cli.api.compute_hosts import ForgeAPIError

    try:
        await api.revoke(instance_id, code_id)
    except ForgeAPIError as exc:
        _echo(f"warning: the unused pairing code {code_id} was not revoked: {exc}")
        return
    _echo(f"  revoked the unused pairing code {code_id}")


async def _leave(
    settings: CLISettings,
    ref: str,
    *,
    ssh: str,
    port: int | None,
    jump: str,
    url: str,
    forge: str,
    timeout_seconds: float,
) -> None:
    from cli.services.remote_hosts import (
        RemoteError,
        find_host,
        leave_script,
        parse_target,
        remote_sh,
        resolve_forge,
        run_streaming,
        wait_removed,
    )

    api = build_api(settings, url)
    instance = resolve_forge(await api.forges(), forge)
    host = find_host((await api.overview(instance["id"])).get("hosts") or [], ref)
    target = parse_target(ssh or host["name"], port=port, jump=jump)
    await api.drain(instance["id"], host["id"], remove=True)
    _echo(f"✓ {host['name']}: drain and remove")
    await wait_removed(
        api,
        instance["id"],
        host["id"],
        timeout_seconds=timeout_seconds,
        poll_seconds=settings.remote.poll_interval_seconds,
        echo=_echo,
    )
    _echo(f"✓ Forge forgot {host['name']}")
    _echo(f"→ {target.destination}: niuu-host-leave")
    run = run_streaming(target.argv(remote_sh(leave_script(host["id"])), tty=sys.stdin.isatty()))
    if run.returncode != 0:
        raise RemoteError(
            f"niuu-host-leave failed on {target.destination} (exit {run.returncode}): "
            f"{run.last_line or 'no output'}. Forge has already removed the host; run "
            "`sudo niuu-host-leave` there."
        )
    _echo(f"✓ {host['name']} left")


# ── output ───────────────────────────────────────────────────────────────────


def _slots_cell(host: dict[str, Any]) -> str:
    used = len(host.get("sessions") or [])
    total = host.get("slots")
    return f"{used}/{total}" if total is not None else f"{used}/?"


def _connection_cell(host: dict[str, Any]) -> str:
    connection = host.get("connection") or {}
    if not connection:
        return "-"
    mark = "up" if connection.get("connected") else "down"
    return f"{connection.get('kind')} {connection.get('target')} ({mark})"


def _table(rows: list[list[str]]) -> list[str]:
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return [
        "  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip()
        for row in rows
    ]


def _print_pool(instance: dict[str, Any], overview: dict[str, Any]) -> None:
    summary = overview.get("summary") or {}
    hosts = overview.get("hosts") or []
    typer.echo(
        f"{instance.get('name') or instance.get('slug')} · pool {overview.get('pool_id')} · "
        f"{summary.get('hosts', len(hosts))} hosts · "
        f"{summary.get('slots_free', 0)}/{summary.get('slots_total', 0)} slots free"
    )
    if summary.get("last_error"):
        typer.echo(f"error: {summary['last_error']}")
    if not hosts:
        typer.echo("No hosts. Join one with `niuu remote join <ssh destination>`.")
        return
    rows = [["NAME", "STATE", "ISOLATION", "SLOTS", "TAGS", "RUNTIME", "CONNECTION", "CHECKED"]]
    rows += [
        [
            str(h.get("name")),
            str(h.get("state")),
            str(h.get("isolation") or "-"),
            _slots_cell(h),
            ",".join(h.get("tags") or []) or "-",
            str(h.get("execution")),
            _connection_cell(h),
            str(h.get("last_checked_at") or "never"),
        ]
        for h in hosts
    ]
    header, *lines = _table(rows)
    typer.echo(header)
    for line, h in zip(lines, hosts, strict=True):
        typer.echo(line)
        for finding in h.get("findings") or []:
            typer.echo(f"  ! {finding}")
    codes = overview.get("pairing_codes") or []
    if codes:
        typer.echo(f"{len(codes)} unused pairing code{'s' if len(codes) != 1 else ''}")


def _print_host(host: dict[str, Any]) -> None:
    from cli.services.remote_hosts import join_steps, step_line

    typer.echo(f"{host.get('name')} ({host.get('id')}) · {host.get('state')}")
    for step in join_steps(host):
        typer.echo(step_line(step))
    for finding in host.get("findings") or []:
        typer.echo(f"! {finding}")
    suggestion = host.get("suggested_slots")
    if host.get("slots") is None and suggestion:
        typer.echo(f"Forge suggests {suggestion['slots']} slots:")
        for line in suggestion.get("lines") or []:
            typer.echo(f"  {line}")
