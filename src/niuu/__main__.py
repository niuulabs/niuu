"""Niuu package entry point for the CLI."""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    """Build the Typer app and run it."""
    if "NIUU_CONFIG" not in os.environ:
        default = Path.home() / ".niuu" / "config.yaml"
        if default.exists():
            os.environ["NIUU_CONFIG"] = str(default)

    from cli.app import build_app

    app = build_app()
    app()


if __name__ == "__main__":
    main()
