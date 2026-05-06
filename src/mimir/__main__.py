"""Entry point for the standalone Mímir service.

Usage::

    python -m mimir serve --path ~/.ravn/mimir --port 7477
    python -m mimir serve --name shared --role shared --announce-url https://mimir.odin.niuu.world
    python -m mimir mcp --path ~/.ravn/mimir
"""

from __future__ import annotations

import asyncio

import typer
import uvicorn

from mimir.app import create_app
from mimir.config import MimirServiceConfig

app = typer.Typer(name="mimir", help="Standalone Mímir knowledge service.")


@app.command()
def serve(
    path: str = typer.Option("~/.ravn/mimir", help="Root directory for the Mímir store."),
    host: str = typer.Option("0.0.0.0", help="Host address to bind to."),
    port: int = typer.Option(7477, help="Port to bind to."),
    name: str = typer.Option("local", help="Instance name for Sleipnir announce."),
    role: str = typer.Option("local", help="Instance role: shared, local, or domain."),
    announce_url: str | None = typer.Option(None, help="Public URL to announce on Sleipnir."),
) -> None:
    """Serve the Mímir knowledge base over HTTP."""
    config = MimirServiceConfig(
        path=path,
        host=host,
        port=port,
        name=name,
        role=role,
        announce_url=announce_url,
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
