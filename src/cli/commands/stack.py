"""Docker-mode lifecycle: ``niuu up`` / ``down`` / ``status`` / ``doctor`` on one Docker host."""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from cli.services.compose_bundle import (
    STACK_OVERRIDES_FILE,
    bundle_paths,
    compose_dir,
    data_dir,
    detect_lan_ip,
    merge_settings,
    pull_applier_image,
    read_stack_overrides,
    remove_session_containers,
    run_compose,
    setup_url,
    wait_for_health,
    write_bundle,
    write_stack_file,
)
from cli.services.docker_host import (
    DockerPreflightConfig,
    HostFacts,
    collect_host_facts,
    preflight_results_as_facts,
    run_docker_preflight_checks,
)
from cli.services.preflight import PreflightResult, format_results, has_failures

if TYPE_CHECKING:
    from cli.config import CLISettings

GIB = 1024**3


def docker_preflight_config(settings: CLISettings) -> DockerPreflightConfig:
    """Build the Docker-host preflight configuration from CLI settings."""
    return DockerPreflightConfig(
        data_dir=settings.docker.data_dir,
        ports=[settings.server.port],
        min_disk_space_bytes=settings.docker.min_disk_space_gib * GIB,
        require_gpu=settings.docker.require_gpu,
    )


def _echo_results(results: list[PreflightResult]) -> None:
    typer.echo(format_results(results))


def _echo_host_summary(facts: HostFacts) -> None:
    gpu = ", ".join(f"{g.name} ({g.memory_total_mib // 1024} GiB)" for g in facts.gpus) or "none"
    typer.echo(f"  Host:    {facts.hostname} · {facts.os_name} {facts.os_version} · {facts.arch}")
    typer.echo(
        f"  Docker:  {facts.docker_version or 'unavailable'}"
        f" · compose {facts.compose_version or 'unavailable'}"
    )
    typer.echo(f"  GPU:     {gpu}")
    typer.echo(
        f"  Data:    {facts.data_dir} · {facts.disk_free_bytes / GIB:.0f} GiB free of "
        f"{facts.disk_total_bytes / GIB:.0f} GiB"
    )


def run_doctor(settings: CLISettings) -> bool:
    """Run and print the host checks for the configured mode; True when healthy."""
    if settings.mode != "docker":
        from cli.commands.platform import _build_preflight_config
        from cli.services.preflight import run_preflight_checks

        typer.echo(f"Checking host for {settings.mode} mode...")
        results = run_preflight_checks(_build_preflight_config(settings))
        _echo_results(results)
        return not has_failures(results)

    config = docker_preflight_config(settings)
    typer.echo("Checking host for docker mode...")
    _echo_host_summary(collect_host_facts(config))
    typer.echo()
    results = run_docker_preflight_checks(config)
    _echo_results(results)
    return not has_failures(results)


def stack_up(settings: CLISettings, *, skip_preflight: bool = False) -> None:
    """Preflight, render the compose bundle, start it, and wait for health."""
    # Changes applied through the setup wizard on an earlier run win over
    # config.yaml, so a restart from the CLI never silently reverts them.
    overrides = read_stack_overrides(data_dir(settings) / STACK_OVERRIDES_FILE)
    if overrides:
        settings = merge_settings(settings, overrides)
        typer.echo(f"Applying wizard overrides from {STACK_OVERRIDES_FILE}")
    config = docker_preflight_config(settings)
    # The checks always run: the wizard shows them. --skip-preflight only
    # means a failure does not stop the start.
    results = run_docker_preflight_checks(config)
    if not skip_preflight:
        typer.echo("Running preflight checks...")
        _echo_results(results)
        if has_failures(results):
            typer.echo("\nPreflight checks failed. Fix the issues above and retry.")
            raise typer.Exit(1)
        typer.echo()

    external_host = settings.server.external_host.strip() or detect_lan_ip()
    facts = collect_host_facts(
        config,
        bind_host=settings.docker.bind_host,
        external_host=external_host,
        port=settings.server.port,
        skuld_image=settings.docker.skuld_image,
        checks=preflight_results_as_facts(results),
    )
    paths = write_bundle(settings, host_facts=facts, external_host=external_host)
    write_stack_file(settings, data_dir(settings))
    typer.echo(f"Compose bundle written to {paths.compose_dir}")
    pull_error = pull_applier_image(settings)
    if pull_error:
        typer.echo(
            f"Could not pull {settings.docker.applier_image} ({pull_error}); the wizard will "
            "pull it when a stack change is applied."
        )

    typer.echo("Starting the Niuu stack (this pulls images on first run)...")
    code = run_compose(settings, "up", "--detach", "--remove-orphans")
    if code != 0:
        typer.echo(f"\n`docker compose up` failed with exit code {code}.")
        raise typer.Exit(code)

    typer.echo("Waiting for the platform to become healthy...", nl=False)
    if not wait_for_health(settings, timeout_seconds=settings.docker.startup_timeout_seconds):
        typer.echo(" timed out")
        typer.echo(
            f"The platform did not answer within {settings.docker.startup_timeout_seconds:g}s. "
            "Inspect it with `niuu status` and `docker compose -p "
            f"{settings.docker.project_name} logs niuu`."
        )
        raise typer.Exit(1)
    typer.echo(" ok")

    typer.echo()
    typer.echo(f"Open {setup_url(settings, external_host)} to finish setup.")
    typer.echo(
        f"  Local:  http://127.0.0.1:{settings.server.port}/ · "
        f"logs: docker compose -p {settings.docker.project_name} logs -f"
    )


def stack_down(settings: CLISettings) -> None:
    """Stop the compose bundle (data on disk is kept)."""
    if not bundle_paths(settings).compose_file.exists():
        typer.echo(f"No compose bundle at {compose_dir(settings)}; nothing to stop.")
        return
    removed = remove_session_containers(settings)
    if removed:
        typer.echo(f"Removed {removed} session container(s).")
    code = run_compose(settings, "down")
    if code != 0:
        raise typer.Exit(code)
    typer.echo("Niuu stack stopped. Data is kept under " + settings.docker.data_dir + ".")


def stack_status(settings: CLISettings) -> None:
    """Show compose service status."""
    typer.echo("Mode: docker")
    typer.echo(f"Bundle: {compose_dir(settings)}")
    typer.echo(f"Data:   {settings.docker.data_dir}")
    typer.echo()
    if not bundle_paths(settings).compose_file.exists():
        typer.echo("Not started yet. Run `niuu up`.")
        return
    code = run_compose(settings, "ps")
    if code != 0:
        raise typer.Exit(code)
