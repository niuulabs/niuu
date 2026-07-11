"""Persisted warden command group for the Ravn CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml

warden_app = typer.Typer(
    name="warden",
    help="Manage persisted long-lived Ravn wardens.",
    add_completion=False,
)


def _warden_store():
    from ravn.warden import build_warden_store

    return build_warden_store()


def _parse_deployment_kwargs(values: list[str] | None) -> dict[str, object]:
    """Parse repeated ``key=value`` deployment options."""
    parsed: dict[str, object] = {}
    for item in values or []:
        key, sep, raw_value = item.partition("=")
        if not sep or not key.strip():
            msg = f"Invalid deployment arg {item!r}; expected key=value"
            raise typer.BadParameter(msg)
        parsed[key.strip()] = yaml.safe_load(raw_value)
    return parsed


@warden_app.command("list")
def warden_list(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """List persisted wardens."""
    store = _warden_store()
    wardens = store.list()
    payload = [warden.model_dump(mode="json") for warden in wardens]

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    if not wardens:
        typer.echo("No wardens found.")
        return

    for warden in wardens:
        mounts = ", ".join(warden.mimir.mount_names) if warden.mimir.mount_names else "none"
        typer.echo(
            f"{warden.id}  persona={warden.persona}  "
            f"write_mount={warden.mimir.write_mount or '-'}  mounts={mounts}"
        )


@warden_app.command("show")
def warden_show(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show one persisted warden."""
    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = warden.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"id: {warden.id}")
    typer.echo(f"name: {warden.name}")
    typer.echo(f"persona: {warden.persona}")
    typer.echo(f"profile: {warden.profile or '-'}")
    typer.echo(f"deployment: {warden.deployment}")
    typer.echo(f"write_mount: {warden.mimir.write_mount or '-'}")
    typer.echo(
        "mounts: " + (", ".join(warden.mimir.mount_names) if warden.mimir.mount_names else "none")
    )
    typer.echo(f"autostart: {str(warden.autostart).lower()}")
    typer.echo(f"installed: {str(warden.supervisor.installed).lower()}")
    typer.echo(f"state: {warden.runtime.state}")
    if warden.supervisor.service_file:
        typer.echo(f"service_file: {warden.supervisor.service_file}")
    if warden.supervisor.config_file:
        typer.echo(f"config_file: {warden.supervisor.config_file}")


@warden_app.command("create")
def warden_create(
    name: str = typer.Argument(help="Human-friendly warden name."),
    persona: str = typer.Option(
        "research-and-distill",
        "--persona",
        help="Default persona used by this warden.",
    ),
    profile: str = typer.Option("", "--profile", help="Optional Ravn profile name."),
    deployment: str = typer.Option(
        "launchd",
        "--deployment",
        help=(
            "Deployment backend shorthand, for example launchd, systemd, k8s-apply, or k8s-gitops."
        ),
    ),
    deployment_arg: list[str] = typer.Option(
        None,
        "--deployment-arg",
        help="Repeat key=value pairs for deployment backend configuration.",
    ),
    mount: list[str] = typer.Option(
        None,
        "--mount",
        help="Repeat to attach one or more Mimir mounts.",
    ),
    write_mount: str = typer.Option("", "--write-mount", help="Default Mimir write mount."),
    autostart: bool = typer.Option(False, "--autostart", help="Mark this warden for autostart."),
    created_by: str = typer.Option("cli", "--created-by", help="Creator label for provenance."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Create and persist a new warden spec."""
    from ravn.warden import WardenSpec, resolve_deployment_adapter

    mounts = mount or []
    deployment_kwargs = _parse_deployment_kwargs(deployment_arg)
    spec = WardenSpec(
        id="",
        name=name,
        persona=persona,
        profile=profile,
        deployment=deployment,
        deployment_adapter=resolve_deployment_adapter(deployment),
        deployment_kwargs=deployment_kwargs,
        mimir={
            "mount_names": mounts,
            "write_mount": write_mount,
        },
        autostart=autostart,
        created_by=created_by,
    )

    store = _warden_store()
    created = store.create(spec)
    payload = created.model_dump(mode="json")

    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Created warden {created.id}")
    typer.echo(f"  persona: {created.persona}")
    typer.echo(f"  write_mount: {created.mimir.write_mount or '-'}")
    typer.echo(
        "  mounts: "
        + (", ".join(created.mimir.mount_names) if created.mimir.mount_names else "none")
    )


@warden_app.command("install")
def warden_install(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Generate local service artifacts for one warden."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    try:
        installed = store.install(warden_id, workspace_root=Path.cwd())
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to install warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if installed is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = installed.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Installed warden {installed.id}")
    typer.echo(f"  service: {installed.supervisor.service_label}")
    typer.echo(f"  service_file: {installed.supervisor.service_file}")
    typer.echo(f"  config_file: {installed.supervisor.config_file}")


@warden_app.command("start")
def warden_start(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Mark an installed warden as started."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)
    if not warden.supervisor.installed:
        typer.echo(f"Warden must be installed before it can be started: {warden_id}", err=True)
        raise typer.Exit(1)

    try:
        started = store.start(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to start warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if started is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = started.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Started warden {started.id}")
    typer.echo(f"  state: {started.runtime.state}")


@warden_app.command("stop")
def warden_stop(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Stop an installed warden."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    warden = store.get(warden_id)
    if warden is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)
    if not warden.supervisor.installed:
        typer.echo(f"Warden must be installed before it can be stopped: {warden_id}", err=True)
        raise typer.Exit(1)

    try:
        stopped = store.stop(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to stop warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if stopped is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = stopped.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Stopped warden {stopped.id}")
    typer.echo(f"  state: {stopped.runtime.state}")


@warden_app.command("uninstall")
def warden_uninstall(
    warden_id: str = typer.Argument(help="Warden id."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Uninstall a warden deployment while keeping the persisted spec."""
    from ravn.ports.warden_deployer import WardenDeploymentError

    store = _warden_store()
    try:
        uninstalled = store.uninstall(warden_id)
    except WardenDeploymentError as exc:
        typer.echo(f"Failed to uninstall warden {warden_id}: {exc}", err=True)
        raise typer.Exit(1) from exc
    if uninstalled is None:
        typer.echo(f"Warden not found: {warden_id}", err=True)
        raise typer.Exit(1)

    payload = uninstalled.model_dump(mode="json")
    if json_output:
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"Uninstalled warden {uninstalled.id}")
    typer.echo(f"  installed: {str(uninstalled.supervisor.installed).lower()}")
    typer.echo(f"  state: {uninstalled.runtime.state}")
