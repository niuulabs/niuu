"""Nuitka build configuration for the Ravn single-binary distribution.

Compiles ravn, bifrost and their dependencies into a single portable
``--onefile`` binary.  Bundles the TUI (textual), skill markdown files,
and the example config template.  No PostgreSQL or web UI assets.

Usage::

    python -m ravn.build                 # default: ravn binary
    python -m ravn.build --dry-run       # print Nuitka command without running
"""

from __future__ import annotations

import platform
import shlex
import subprocess
import sys
from pathlib import Path

import typer

REPO_ROOT = Path(__file__).resolve().parents[2]

INCLUDE_PACKAGES = [
    "ravn",
    "bifrost",
    # TUI
    "textual",
    # CLI framework
    "typer",
    "click",
    # Rich (textual dependency; unicode data is dynamically imported)
    "rich",
    "rich._unicode_data",
    # Networking
    "websockets",
    "anyio",
    "httpx",
    # Serialisation
    "pydantic",
    "pydantic_core",
]

INCLUDE_PACKAGE_DATA = [
    "ravn",
    "textual",
    "rich",
]

DATA_DIR_MAPPINGS = [
    # Skill markdown templates
    (REPO_ROOT / "src" / "ravn" / "skills", "ravn/skills"),
]

DATA_FILE_MAPPINGS = [
    # Example TUI config
    (REPO_ROOT / "ravn.tui.example.yaml", "ravn/config/ravn.tui.example.yaml"),
]

NOFOLLOW_IMPORTS = [
    "pytest",
    "_pytest",
    "ruff",
    "respx",
    "nuitka",
    # Heavy optional deps not needed in the binary
    "torch",
    "sentence_transformers",
]

DEFAULT_BINARY_NAME = "ravn"
DEFAULT_ENTRY_POINT = str(REPO_ROOT / "src" / "ravn" / "__main__.py")
DEFAULT_OUTPUT_DIR = str(REPO_ROOT / "dist")


def platform_suffix() -> str:
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine, machine)
    return f"{os_name}-{arch}"


def build_command(
    binary_name: str = DEFAULT_BINARY_NAME,
    entry_point: str = DEFAULT_ENTRY_POINT,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--onefile",
        "--follow-imports",
        f"--output-dir={output_dir}",
        f"--output-filename={binary_name}-{platform_suffix()}",
        "--enable-plugin=no-qt",
    ]

    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    for pkg in INCLUDE_PACKAGE_DATA:
        cmd.append(f"--include-package-data={pkg}")

    for src_dir, dest in DATA_DIR_MAPPINGS:
        if src_dir.is_dir() and any(src_dir.iterdir()):
            cmd.append(f"--include-data-dir={src_dir}={dest}")

    for src_file, dest in DATA_FILE_MAPPINGS:
        if src_file.is_file():
            cmd.append(f"--include-data-files={src_file}={dest}")

    nofollow = ",".join(NOFOLLOW_IMPORTS)
    cmd.append(f"--nofollow-import-to={nofollow}")

    cmd.append(entry_point)
    return cmd


build_cli = typer.Typer()


@build_cli.command()
def main(
    name: str = typer.Option(DEFAULT_BINARY_NAME, help="Binary name"),
    entry: str = typer.Option(DEFAULT_ENTRY_POINT, help="Entry point module"),
    output_dir: str = typer.Option(DEFAULT_OUTPUT_DIR, "--output-dir", help="Output directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print command without running"),
) -> None:
    """Build Ravn single-binary distribution."""
    cmd = build_command(binary_name=name, entry_point=entry, output_dir=output_dir)

    if dry_run:
        typer.echo(shlex.join(cmd))
        raise typer.Exit()

    typer.echo(f"Building {name} binary …")
    typer.echo(f"Command: {shlex.join(cmd)}")
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)

    raise typer.Exit(0)


if __name__ == "__main__":
    build_cli()
