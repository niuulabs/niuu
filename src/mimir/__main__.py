"""Entry point for the standalone Mímir service.

Usage::

    python -m mimir serve --path ~/.ravn/mimir --port 7477
    python -m mimir serve --name shared --role shared --announce-url https://mimir.odin.niuu.world
    python -m mimir mcp --path ~/.ravn/mimir
    python -m mimir eval --json
    python -m mimir eval replay --capture ~/.ravn/mimir/evals/queries-2026-W24.jsonl
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
import uvicorn

from mimir.app import create_app
from mimir.config import MimirServiceConfig

app = typer.Typer(name="mimir", help="Standalone Mímir knowledge service.")

_DEFAULT_EVAL_CORPUS = "tests/test_mimir/evals/corpus"
_DEFAULT_EVAL_GOLDEN = "tests/test_mimir/evals/golden.yaml"


@app.command()
def serve(
    path: str = typer.Option("~/.ravn/mimir", help="Root directory for the Mímir store."),
    host: str = typer.Option("0.0.0.0", help="Host address to bind to."),
    port: int = typer.Option(7477, help="Port to bind to."),
    name: str = typer.Option("local", help="Instance name for Sleipnir announce."),
    role: str = typer.Option("local", help="Instance role: shared, local, or domain."),
    announce_url: str | None = typer.Option(None, help="Public URL to announce on Sleipnir."),
    eval_capture: bool = typer.Option(
        False,
        help="Capture every search query to <path>/evals/ for offline replay.",
    ),
) -> None:
    """Serve the Mímir knowledge base over HTTP."""
    config = MimirServiceConfig(
        path=path,
        host=host,
        port=port,
        name=name,
        role=role,
        announce_url=announce_url,
        eval_capture=eval_capture,
    )
    fastapi_app = create_app(config)
    uvicorn.run(fastapi_app, host=host, port=port)


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
    from mimir.adapters.markdown import MarkdownMimirAdapter
    from mimir.mcp import MimirMcpServer

    adapter = MarkdownMimirAdapter(root=path)
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
        help="sentence-transformers model for hybrid search. None = FTS-only.",
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
    """Run the golden-set retrieval eval (default when no subcommand given)."""
    if ctx.invoked_subcommand is not None:
        return

    from mimir.eval import EvalReport, compare_reports, run_eval

    report = asyncio.run(run_eval(Path(corpus), Path(golden), embedding_model=embedding_model))

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
