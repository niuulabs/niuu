"""Shared CLI output helpers — Rich tables, JSON, error panels.

Used by plugin commands to render API responses in both
human-readable (Rich table/panel) and machine-readable (JSON) formats.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(stderr=True)
stdout_console = Console()


def print_table(
    columns: list[tuple[str, str]],
    rows: list[dict[str, Any]],
) -> None:
    """Render a Rich table to stdout.

    Parameters
    ----------
    columns:
        List of (key, header_label) tuples.
    rows:
        List of dicts; each dict is one row keyed by the column keys.
    """
    table = Table(show_header=True, header_style="bold")
    for _key, label in columns:
        table.add_column(label)

    for row in rows:
        table.add_row(*(str(row.get(k, "")) for k, _ in columns))

    stdout_console.print(table)


def print_json(data: Any) -> None:
    """Print raw JSON to stdout."""
    sys.stdout.write(json.dumps(data, indent=2, default=str) + "\n")


def print_success(message: str) -> None:
    """Print a green success panel."""
    stdout_console.print(Panel(message, style="green"))


def print_error(message: str) -> None:
    """Print a red error panel to stderr."""
    console.print(Panel(message, style="red", title="Error"))


def format_api_error(status_code: int, detail: str) -> str:
    """Build a human-readable error message from an HTTP status."""
    match status_code:
        case 401:
            return "Authentication required. Run 'niuu login' first."
        case 403:
            return "Permission denied."
        case 404:
            return f"Not found: {detail}"
        case _:
            return f"API error {status_code}: {detail}"
    raise AssertionError("Unreachable format_api_error fallthrough")


def handle_api_error(exc: httpx.HTTPStatusError) -> None:
    """Extract detail from an HTTP error response, print it, and exit."""
    try:
        detail = exc.response.json().get("detail", exc.response.text)
    except (json.JSONDecodeError, ValueError):
        detail = exc.response.text
    print_error(format_api_error(exc.response.status_code, str(detail)))
    raise typer.Exit(1)


def handle_transport_error(service_name: str) -> None:
    """Print a connection/transport error panel and exit."""
    print_error(f"Could not connect to {service_name}. Is the platform running?")
    raise typer.Exit(1)
