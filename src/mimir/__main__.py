"""Entry point for the standalone Mímir service.

Usage::

    python -m mimir serve --backend '{"adapter": "mimir.adapters.gbrain.GBrainMimirAdapter", ...}'
    python -m mimir serve --name shared --role shared --announce-url https://mimir.odin.niuu.world
    python -m mimir mcp --adapter-config ./instance.yaml
    python -m mimir eval --backend '{"adapter": "...", ...}' --json
    python -m mimir eval replay --backend '{"adapter": "...", ...}' \\
        --capture ~/.ravn/mimir/evals/queries-2026-W24.jsonl
    python -m mimir ingest ./architecture.md --title "Architecture" --mimir local
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import uvicorn

from mimir.app import create_app
from mimir.config import MimirServiceConfig

app = typer.Typer(name="mimir", help="Standalone Mímir knowledge service.")

_DEFAULT_EVAL_GOLDEN = "tests/test_mimir/evals/golden.yaml"


def _configured_mimir_adapter():
    if not os.environ.get("RAVN_CONFIG", "").strip():
        return None
    from mimir.builders import build_mimir
    from mimir.client_settings import MimirClientSettings

    return build_mimir(MimirClientSettings())


def _backend_option(raw: str) -> dict[str, Any]:
    """Parse a ``--backend`` dynamic-adapter spec: a JSON object naming a MimirPort class."""
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"--backend must be a JSON object: {exc}") from exc
    if not isinstance(spec, dict):
        raise typer.BadParameter(
            '--backend must be a JSON object such as {"adapter": "<MimirPort class>", ...}'
        )
    return spec


@app.command()
def serve(
    backend: str | None = typer.Option(
        None,
        help=(
            "JSON dynamic-adapter spec of the store to serve: "
            '{"adapter": "<MimirPort class>", ...constructor kwargs}. Required, here or as '
            "`backend:` in the config / MIMIR__BACKEND."
        ),
    ),
    path: str | None = typer.Option(
        None, help="Directory for the service's registry and eval captures."
    ),
    host: str | None = typer.Option(None, help="Host address to bind to."),
    port: int | None = typer.Option(None, help="Port to bind to."),
    name: str | None = typer.Option(None, help="Instance name in the mount registry."),
    role: str | None = typer.Option(None, help="Instance role: shared, local, or domain."),
    announce_url: str | None = typer.Option(None, help="Public URL this instance is reachable at."),
    categories: str | None = typer.Option(
        None,
        help="Comma-separated category filter for domain-scoped instances.",
    ),
    eval_capture: bool | None = typer.Option(
        None,
        "--eval-capture/--no-eval-capture",
        help="Capture every search query to <path>/evals/ (default on).",
    ),
) -> None:
    """Serve the Mímir knowledge base over HTTP.

    Flags override the YAML config (./mimir.yaml, /etc/mimir/config.yaml or
    $MIMIR_CONFIG) and MIMIR__* environment variables; anything not given on
    the command line falls through to those sources, then to the defaults.
    """
    overrides: dict[str, object] = {
        key: value
        for key, value in {
            "path": path,
            "host": host,
            "port": port,
            "name": name,
            "role": role,
            "announce_url": announce_url,
            "eval_capture": eval_capture,
        }.items()
        if value is not None
    }
    if backend is not None:
        overrides["backend"] = _backend_option(backend)
    if categories is not None:
        overrides["categories"] = [c.strip() for c in categories.split(",") if c.strip()]
    config = MimirServiceConfig(**overrides)
    fastapi_app = create_app(config)
    uvicorn.run(fastapi_app, host=config.host, port=config.port)


def _with_write_policy(adapter, name: str, read_only: bool, write_prefix: list[str]):
    """``adapter`` behind a mount that refuses the writes the flags do not allow.

    The composite is where a mount's write policy is held: it refuses the write, names
    why, and counts the refusal, whatever adapter sits behind it. With no flag the adapter
    is returned as it is.
    """
    if read_only and write_prefix:
        raise typer.BadParameter("--read-only and --write-prefix exclude each other")
    if not (read_only or write_prefix):
        return adapter
    from mimir.adapters.composite import CompositeMimirAdapter
    from mimir.domain import MimirMount, WriteRouting

    return CompositeMimirAdapter(
        mounts=[
            MimirMount(
                name=name,
                port=adapter,
                role="local",
                read_only=read_only,
                write_prefixes=tuple(write_prefix) if write_prefix else None,
            )
        ],
        write_routing=WriteRouting(default=[name]),
    )


@app.command()
def mcp(
    name: str = typer.Option("local", help="Instance name reported in the MCP handshake."),
    adapter_config: str | None = typer.Option(
        None,
        help=(
            "Backend instance YAML (a MimirInstanceConfig: adapter + kwargs, or url). "
            "Without it, the Mímir configured by RAVN_CONFIG is served."
        ),
    ),
    read_only: bool = typer.Option(
        False, "--read-only", help="Refuse every write to this mount (the tool returns an error)."
    ),
    write_prefix: list[str] = typer.Option(
        [],
        "--write-prefix",
        help="Page path prefix this mount may write (repeatable); other writes are refused.",
    ),
) -> None:
    """Run the Mímir MCP server in stdio mode (no running Mímir service required).

    Configure in .mcp.json::

        {
          "mcpServers": {
            "mimir": {
              "type": "stdio",
              "command": "python3",
              "args": ["-m", "mimir", "mcp", "--adapter-config", "./instance.yaml"]
            }
          }
        }
    """
    from mimir.mcp import MimirMcpServer

    if adapter_config:
        import yaml

        from mimir.builders import build_mimir
        from mimir.client_config import MimirConfig, MimirInstanceConfig
        from mimir.client_settings import MimirClientSettings

        instance = yaml.safe_load(Path(adapter_config).read_text())
        instance["name"] = name
        adapter = build_mimir(
            MimirClientSettings(
                mimir=MimirConfig(enabled=True, instances=[MimirInstanceConfig(**instance)])
            )
        )
        if adapter is None:
            raise ValueError("No knowledge adapter configured")
    else:
        adapter = _configured_mimir_adapter()
        if adapter is None:
            raise typer.BadParameter(
                "no Mímir to serve: pass --adapter-config <instance.yaml>, or set RAVN_CONFIG to "
                "a config whose mimir.instances names the store (and mimir.enabled is true)"
            )
    adapter = _with_write_policy(adapter, name, read_only, write_prefix)
    server = MimirMcpServer(adapter=adapter, name=name)
    asyncio.run(server.run_stdio())


eval_app = typer.Typer(
    name="eval",
    help="Retrieval-quality evaluation (golden-set eval and capture replay).",
    invoke_without_command=True,
)
app.add_typer(eval_app)


@eval_app.callback()
def eval_run(
    ctx: typer.Context,
    backend: str | None = typer.Option(
        None,
        help=(
            "JSON dynamic-adapter spec of the already-populated store to score (required "
            "unless a subcommand is given): "
            '{"adapter": "<MimirPort class>", ...constructor kwargs}.'
        ),
    ),
    golden: str = typer.Option(
        _DEFAULT_EVAL_GOLDEN,
        help="Golden-set YAML mapping queries to expected page paths.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
    out: str | None = typer.Option(None, help="Also write the JSON report to this file."),
    against: str | None = typer.Option(
        None,
        help="Baseline report JSON to compare against (from a previous --out).",
    ),
    fail_on_regression: bool = typer.Option(
        False,
        help="Exit non-zero when overall P@5 regresses vs --against baseline.",
    ),
) -> None:
    """Score a populated store against the golden set (default when no subcommand given)."""
    if ctx.invoked_subcommand is not None:
        return

    from mimir.eval import EvalReport, compare_reports, run_eval

    if backend is None:
        raise typer.BadParameter("--backend is required to score a store", param_hint="--backend")
    report = asyncio.run(run_eval(_backend_option(backend), Path(golden)))

    if out is not None:
        Path(out).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(report.format_text())

    if against is None:
        return

    baseline = EvalReport.from_dict(json.loads(Path(against).read_text(encoding="utf-8")))
    comparison = compare_reports(report, baseline)
    typer.echo("")
    typer.echo(comparison.format_text())
    if fail_on_regression and comparison.has_regression():
        raise typer.Exit(code=1)


@eval_app.command()
def replay(
    capture: str = typer.Option(..., help="Capture JSONL file written by eval_capture."),
    backend: str = typer.Option(
        ...,
        help=(
            "JSON dynamic-adapter spec of the store to replay against: "
            '{"adapter": "<MimirPort class>", ...constructor kwargs}.'
        ),
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Re-run captured production queries and report ranking drift."""
    from mimir.backend import build_backend
    from mimir.eval import load_capture, release_adapter, replay_capture

    spec = _backend_option(backend)
    captures = load_capture(Path(capture).expanduser())
    if not captures:
        typer.echo(f"No valid captures found in {capture}")
        raise typer.Exit(code=1)

    async def _replay():
        adapter = build_backend(spec, setting="--backend")
        try:
            return await replay_capture(adapter, captures, backend=str(spec["adapter"]))
        finally:
            await release_adapter(adapter)

    report = asyncio.run(_replay())
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(report.format_text())


def _configure_logging(settings) -> None:
    """Apply the logging section of the client settings."""
    level = getattr(logging, settings.logging.level.upper(), logging.WARNING)
    if settings.observability.enabled:
        from niuu.observability import enable_trace_log_correlation

        enable_trace_log_correlation()
    if settings.logging.format == "text":
        fmt = "%(asctime)s %(name)s %(levelname)s"
        if settings.observability.enabled:
            fmt += " trace_id=%(trace_id)s span_id=%(span_id)s"
        fmt += " %(message)s"
    elif settings.observability.enabled:
        fmt = '{"message":"%(message)s","trace_id":"%(trace_id)s","span_id":"%(span_id)s"}'
    else:
        fmt = "%(message)s"
    logging.basicConfig(level=level, format=fmt, force=True)


@app.command("ingest")
def ingest(
    path: str = typer.Argument(..., help="Path to a file to ingest, or '-' to read from stdin."),
    title: str = typer.Option("", "--title", "-t", help="Title override (defaults to filename)."),
    source_type: str = typer.Option(
        "document",
        "--type",
        help="Source type: document, web, research, conversation, tool_output.",
    ),
    origin_url: str = typer.Option("", "--url", "-u", help="Original URL (optional metadata)."),
    target: str = typer.Option(
        "",
        "--mimir",
        "-m",
        help=(
            "Named Mímir instance to ingest into (e.g. 'local', 'shared'). "
            "Defaults to the one instance write routing selects."
        ),
    ),
    config: str = typer.Option(
        "", "--config", "-c", help="Path to the client config YAML (RAVN_CONFIG)."
    ),
) -> None:
    """Ingest a file or stdin into the Mímir knowledge base.

    Works with both local (filesystem) and remote (HTTP) Mímir adapters —
    the adapter is selected from the Ravn config file, no explicit URL needed.

    Examples::

        python -m mimir ingest ./architecture.md
        python -m mimir ingest ./notes.txt --title "Sprint retro notes" --type research
        python -m mimir ingest ./doc.md --mimir local
        python -m mimir ingest ./doc.md --mimir shared
        cat doc.md | python -m mimir ingest -
    """
    from mimir.client_settings import MimirClientSettings

    if config:
        os.environ["RAVN_CONFIG"] = config

    settings = MimirClientSettings()
    _configure_logging(settings)

    if not settings.mimir.enabled:
        typer.echo("Mímir is disabled in config (mimir.enabled = false).", err=True)
        raise typer.Exit(1)

    asyncio.run(
        _run_mimir_ingest(settings, path, title, source_type, origin_url or None, target or None)
    )


def _build_single_mimir(settings, name: str) -> Any:
    """Build a single named Mímir adapter by instance name.

    Searches ``settings.mimir.instances`` for *name*; an unknown name exits
    listing the configured ones.
    """
    from mimir.builders import build_mimir_auth

    for inst in settings.mimir.instances:
        if inst.name != name:
            continue
        from mimir.connections import resolve_mimir_connection

        return resolve_mimir_connection(
            adapter=inst.adapter,
            kwargs=inst.kwargs,
            secret_kwargs_env=inst.secret_kwargs_env,
            url=inst.url,
            environment_id=settings.environment.id,
            auth=build_mimir_auth(settings, inst.auth) if inst.auth is not None else None,
        )

    available = [inst.name for inst in settings.mimir.instances] or ["(none configured)"]
    typer.echo(f"Unknown Mímir instance {name!r}. Available: {', '.join(available)}", err=True)
    raise typer.Exit(1)


async def _run_mimir_ingest(
    settings,
    path: str,
    title: str,
    source_type: str,
    origin_url: str | None,
    target: str | None = None,
) -> None:
    from mimir.builders import build_mimir
    from niuu.domain.mimir import MimirSource, compute_content_hash

    mimir = _build_single_mimir(settings, target) if target else build_mimir(settings)
    if mimir is None:
        typer.echo("Failed to build Mímir adapter — check config.", err=True)
        raise typer.Exit(1)

    if path == "-":
        content = sys.stdin.read()
        resolved_title = title or "stdin"
        resolved_path = "stdin"
    else:
        file_path = Path(path).expanduser()
        if not file_path.exists():
            typer.echo(f"File not found: {file_path}", err=True)
            raise typer.Exit(1)
        content = file_path.read_text(encoding="utf-8", errors="replace")
        resolved_title = title or file_path.stem.replace("-", " ").replace("_", " ").title()
        resolved_path = str(file_path)

    if not content.strip():
        typer.echo("Content is empty — nothing to ingest.", err=True)
        raise typer.Exit(1)

    content_hash = compute_content_hash(content)
    source_id = "src_" + content_hash[:16]

    source = MimirSource(
        source_id=source_id,
        title=resolved_title,
        content=content,
        source_type=source_type,  # type: ignore[arg-type]
        origin_url=origin_url,
        content_hash=content_hash,
        ingested_at=datetime.now(UTC),
    )

    await mimir.ingest(source)
    typer.echo(f"Ingested: {resolved_title!r}")
    typer.echo(f"source_id: {source_id}")
    typer.echo(f"file:      {resolved_path}")
    typer.echo(f"target:    {target or 'all'}")
    typer.echo("The source is stored; write its wiki pages with mimir_write.")


def _memory_system(tenant: str):
    """The configured memory systems and the one holding *tenant*'s layers."""
    from mimir.memory.systems import MemorySystems, TenantNotConfiguredError

    config = MimirServiceConfig()
    systems = MemorySystems.from_config(config.memory.systems, config.memory.tenants)
    try:
        _, backend = systems.for_tenant(tenant)
    except TenantNotConfiguredError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    return systems, backend


def _target_layer(layer: str):
    from mimir.memory.models import Layer, LayerKind, MemoryValidationError

    try:
        target = Layer.parse(layer)
    except MemoryValidationError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if target.kind not in (LayerKind.ORG, LayerKind.PROJECT):
        raise typer.BadParameter("name the org layer (org) or a project layer (project:<id>)")
    return target


@app.command("import-wiki")
def import_wiki_command(
    root: Path = typer.Option(..., "--root", help="The old wiki root, holding wiki/ and raw/."),
    tenant: str = typer.Option(..., "--tenant", help="The org whose memory system receives it."),
    layer: str = typer.Option(..., "--layer", help="org, or project:<project id>."),
    operator: str = typer.Option(
        "wiki-import",
        "--operator",
        help="Who runs the import; its records are attributed to operator:<this>.",
    ),
) -> None:
    """Import a retired Markdown wiki into a memory layer at operator trust.

    Uses the `memory:` section of the service configuration. A page that cannot be
    imported stops the import, naming the file.
    """
    from mimir.memory.importer import WikiImportError, import_wiki
    from niuu.domain.mimir_facts import AuthorKind, FactAuthor, FactCaller

    target = _target_layer(layer)
    systems, backend = _memory_system(tenant)
    caller = FactCaller(FactAuthor(AuthorKind.OPERATOR, operator))

    async def _run():
        try:
            return await import_wiki(backend, root.expanduser(), target, caller)
        finally:
            await systems.close()

    try:
        report = asyncio.run(_run())
    except WikiImportError as exc:
        typer.echo(f"import failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Imported into {target.id} of tenant {tenant}: {report.summary()}")
    for path, reason in report.not_imported:
        typer.echo(f"  not imported: {path} ({reason})")


@app.command("list-proposals")
def list_proposals_command(
    tenant: str = typer.Option(..., "--tenant", help="The org whose memory system holds them."),
    layer: str = typer.Option(..., "--layer", help="org, or project:<project id>."),
    limit: int = typer.Option(..., "--limit", min=1, help="How many proposals to list."),
) -> None:
    """List the pending proposals for a layer, for its owner (decide them over the API)."""
    from mimir.memory.gateway import list_proposals

    target = _target_layer(layer)
    systems, backend = _memory_system(tenant)

    async def _run():
        try:
            return await list_proposals(backend, target, limit=limit)
        finally:
            await systems.close()

    proposals = asyncio.run(_run())
    if not proposals:
        typer.echo(f"No pending proposals for {target.id}.")
    for proposal in proposals:
        flag = "" if proposal.attested else " [UNATTESTED: not written by the gateway]"
        typer.echo(
            f"{proposal.id}  {proposal.action.value}  from session {proposal.proposer_session} "
            f"(owner {proposal.session_owner}) at {proposal.created_at.isoformat()}{flag}\n"
            f"    why: {proposal.reason}\n"
            f"    change: {json.dumps(proposal.change, ensure_ascii=False)}\n"
            f"    evidence: {', '.join(e.ref for e in proposal.evidence)}"
        )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
