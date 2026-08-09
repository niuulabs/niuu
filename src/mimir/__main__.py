"""Entry point for the standalone Mímir service.

Usage::

    python -m mimir serve --path ~/.ravn/mimir --port 7477
    python -m mimir serve --name shared --role shared --announce-url https://mimir.odin.niuu.world
    python -m mimir mcp --path ~/.ravn/mimir
    python -m mimir doctor --path ~/.ravn/mimir [--fix] [--json]
    python -m mimir eval --json
    python -m mimir eval replay --capture ~/.ravn/mimir/evals/queries-2026-W24.jsonl
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer
import uvicorn

from mimir.app import create_app
from mimir.config import MimirServiceConfig

app = typer.Typer(name="mimir", help="Standalone Mímir knowledge service.")

_DEFAULT_EVAL_CORPUS = "tests/test_mimir/evals/corpus"
_DEFAULT_EVAL_GOLDEN = "tests/test_mimir/evals/golden.yaml"


def _configured_mimir_adapter():
    if not os.environ.get("RAVN_CONFIG", "").strip():
        return None
    from ravn.cli.commands import _build_mimir
    from ravn.config import Settings

    return _build_mimir(Settings())


def _mcp_adapter(path: str):
    configured = _configured_mimir_adapter()
    if configured is not None:
        return configured
    from mimir.adapters.markdown import MarkdownMimirAdapter

    return MarkdownMimirAdapter(root=path)


@app.command()
def serve(
    path: str | None = typer.Option(None, help="Root directory for the Mímir store."),
    host: str | None = typer.Option(None, help="Host address to bind to."),
    port: int | None = typer.Option(None, help="Port to bind to."),
    name: str | None = typer.Option(None, help="Instance name for Sleipnir announce."),
    role: str | None = typer.Option(None, help="Instance role: shared, local, or domain."),
    announce_url: str | None = typer.Option(None, help="Public URL to announce on Sleipnir."),
    embedding_model: str | None = typer.Option(
        None,
        help="sentence-transformers model for hybrid search (e.g. all-MiniLM-L6-v2).",
    ),
    categories: str | None = typer.Option(
        None,
        help="Comma-separated category filter for domain-scoped instances.",
    ),
    search_db: str | None = typer.Option(None, help="Path for the SQLite search index."),
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
            "embedding_model": embedding_model,
            "search_db": search_db,
            "eval_capture": eval_capture,
        }.items()
        if value is not None
    }
    if categories is not None:
        overrides["categories"] = [c.strip() for c in categories.split(",") if c.strip()]
    config = MimirServiceConfig(**overrides)
    fastapi_app = create_app(config)
    uvicorn.run(fastapi_app, host=config.host, port=config.port)


@app.command()
def mcp(
    path: str = typer.Option("~/.ravn/mimir", help="Root directory for the Mímir store."),
    name: str = typer.Option("local", help="Instance name reported in the MCP handshake."),
) -> None:
    """Run the Mímir MCP server in stdio mode (no running Mímir service required).

    Configure in .mcp.json::

        {
          "mcpServers": {
            "mimir": {
              "type": "stdio",
              "command": "python3",
              "args": ["-m", "mimir", "mcp", "--path", "~/.ravn/mimir"]
            }
          }
        }
    """
    from mimir.mcp import MimirMcpServer

    adapter = _mcp_adapter(path)
    server = MimirMcpServer(adapter=adapter, name=name)
    asyncio.run(server.run_stdio())


@app.command()
def doctor(
    path: str = typer.Option("~/.ravn/mimir", help="Root directory for the Mímir store."),
    fix: bool = typer.Option(False, "--fix", help="Apply safe auto-fixes before reporting."),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Run health checks (D01–D08) against a Mímir root; exit 0/1/2 on pass/warn/fail."""
    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.doctor import DoctorReport, run_doctor, run_fixes
    from mimir.registry import MimirRegistryStore
    from niuu.adapters.search.sqlite import SqliteSearchAdapter

    root = Path(path).expanduser()
    search_db = root / "search.db"
    adapter = MarkdownMimirAdapter(root=root, search_port=SqliteSearchAdapter(path=str(search_db)))
    registry_store = MimirRegistryStore(root / ".mimir-registry.json")

    async def _run() -> tuple[list[str], DoctorReport]:
        fixes = await run_fixes(adapter) if fix else []
        report = await run_doctor(
            adapter,
            root,
            registry_store=registry_store,
            search_db=search_db,
        )
        return fixes, report

    fixes, report = asyncio.run(_run())

    if json_output:
        payload = report.to_dict()
        payload["fixes_applied"] = fixes
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(code=report.exit_code)

    for applied in fixes:
        typer.echo(f"fixed: {applied}")
    if fixes:
        typer.echo("")
    typer.echo(report.format_text())
    raise typer.Exit(code=report.exit_code)


eval_app = typer.Typer(
    name="eval",
    help="Retrieval-quality evaluation (golden-set eval and capture replay).",
    invoke_without_command=True,
)
app.add_typer(eval_app)


@eval_app.callback()
def eval_run(
    ctx: typer.Context,
    corpus: str = typer.Option(
        _DEFAULT_EVAL_CORPUS,
        help="Directory of fixture wiki pages (relative to the repo root).",
    ),
    golden: str = typer.Option(
        _DEFAULT_EVAL_GOLDEN,
        help="Golden-set YAML mapping queries to expected page paths.",
    ),
    embedding_model: str | None = typer.Option(
        None,
        help="Embedding model for hybrid search. None = FTS-only.",
    ),
    embedding_base_url: str = typer.Option(
        "",
        help="OpenAI-compatible embedding endpoint (e.g. a vLLM /v1). "
        "Empty = load the model locally via sentence-transformers.",
    ),
    embedding_api_key: str = typer.Option("", help="Bearer token for --embedding-base-url."),
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
    """Run the golden-set retrieval eval (default when no subcommand given)."""
    if ctx.invoked_subcommand is not None:
        return

    from mimir.eval import EvalReport, compare_reports, run_eval

    report = asyncio.run(
        run_eval(
            Path(corpus),
            Path(golden),
            embedding_model=embedding_model,
            embedding_base_url=embedding_base_url,
            embedding_api_key=embedding_api_key,
        )
    )

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
    path: str = typer.Option("~/.ravn/mimir", help="Mímir root whose wiki to replay against."),
    embedding_model: str | None = typer.Option(
        None,
        help="sentence-transformers model for hybrid search. None = FTS-only.",
    ),
    json_output: bool = typer.Option(False, "--json", help="Emit the report as JSON."),
) -> None:
    """Re-run captured production queries and report ranking drift."""
    from mimir.eval import load_capture, replay_capture

    captures = load_capture(Path(capture).expanduser())
    if not captures:
        typer.echo(f"No valid captures found in {capture}")
        raise typer.Exit(code=1)

    report = asyncio.run(
        replay_capture(
            Path(path).expanduser(),
            captures,
            embedding_model=embedding_model,
        )
    )
    if json_output:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        typer.echo(report.format_text())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
