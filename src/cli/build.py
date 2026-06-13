"""Nuitka build configuration for the Niuu single-binary distribution.

Compiles all Python packages (cli, niuu, volundr, ting, skuld) plus
bundled web UI assets, migration SQL files, and PostgreSQL binaries
into a single portable ``--onefile`` binary.

Usage::

    python -m cli.build                 # default: niuu binary
    python -m cli.build --name niuu-agent --entry src/cli/__main__.py

Parameterised via BINARY_NAME / ENTRY_POINT for future targets
(niuu-agent, niuu-reviewer, etc.).
"""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from pathlib import Path

import typer

REPO_ROOT = Path(__file__).resolve().parents[2]

# Packages to include in the binary
INCLUDE_PACKAGES = [
    "audit",
    "cli",
    "credentials",
    "features",
    "identity",
    "integrations",
    "niuu",
    "tracker",
    "volundr",
    "ting",
    "skuld",
    # FastAPI / uvicorn ecosystem (dynamically imported)
    "uvicorn",
    "uvicorn.lifespan",
    "uvicorn.loops",
    "uvicorn.protocols",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "anyio",
    "websockets",
    # Rich unicode data modules are dynamically imported by version string
    "rich._unicode_data",
]

# Package data to bundle (CSS, SQL, etc.)
INCLUDE_PACKAGE_DATA = [
    "cli",
    "rich",
    "textual",
]

# Data directories mapped into the binary
DATA_DIR_MAPPINGS = [
    # Web UI assets → cli/web/dist inside the binary
    (REPO_ROOT / "src" / "cli" / "web" / "dist", "cli/web/dist"),
    # Volundr migrations
    (REPO_ROOT / "src" / "cli" / "migrations" / "volundr", "cli/migrations/volundr"),
    # Ting migrations
    (REPO_ROOT / "src" / "cli" / "migrations" / "ting", "cli/migrations/ting"),
    # PostgreSQL binaries built from source
    (REPO_ROOT / "build" / "pginstall", "niuu/pginstall"),
]

# Imports to exclude from the binary
NOFOLLOW_IMPORTS = [
    "pytest",
    "_pytest",
    "ruff",
    "respx",
]

DEFAULT_BINARY_NAME = "niuu"
DEFAULT_ENTRY_POINT = str(REPO_ROOT / "src" / "cli" / "__main__.py")
DEFAULT_OUTPUT_DIR = str(REPO_ROOT / "dist")


def platform_suffix() -> str:
    """Return ``{os}-{arch}`` suffix for the current platform."""
    os_name = platform.system().lower()
    machine = platform.machine().lower()
    # Normalise architecture names
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine, machine)
    return f"{os_name}-{arch}"


def linux_arm64() -> bool:
    machine = platform.machine().lower()
    return platform.system().lower() == "linux" and machine in {"aarch64", "arm64"}


def nuitka_toolchain_flags() -> list[str]:
    flags: list[str] = []
    if linux_arm64():
        flags.extend(["--clang", "--lto=yes", "--static-libpython=no"])

    extra_args = os.environ.get("NIUU_NUITKA_EXTRA_ARGS")
    if extra_args:
        flags.extend(shlex.split(extra_args))

    return flags


def build_command(
    binary_name: str = DEFAULT_BINARY_NAME,
    entry_point: str = DEFAULT_ENTRY_POINT,
    output_dir: str = DEFAULT_OUTPUT_DIR,
) -> list[str]:
    """Assemble the Nuitka compilation command."""
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
    cmd.extend(nuitka_toolchain_flags())

    for pkg in INCLUDE_PACKAGES:
        cmd.append(f"--include-package={pkg}")

    for pkg in INCLUDE_PACKAGE_DATA:
        cmd.append(f"--include-package-data={pkg}")

    for src_dir, dest in DATA_DIR_MAPPINGS:
        if src_dir.is_dir() and any(src_dir.iterdir()):
            cmd.append(f"--include-data-dir={src_dir}={dest}")

    nofollow = ",".join(NOFOLLOW_IMPORTS)
    cmd.append(f"--nofollow-import-to={nofollow}")

    cmd.append(entry_point)
    return cmd


build_cli = typer.Typer()


@build_cli.command()
def main(
    name: str = typer.Option(
        DEFAULT_BINARY_NAME, help=f"Binary name (default: {DEFAULT_BINARY_NAME})"
    ),
    entry: str = typer.Option(DEFAULT_ENTRY_POINT, help="Entry point module"),
    output_dir: str = typer.Option(DEFAULT_OUTPUT_DIR, "--output-dir", help="Output directory"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print command without running"),
) -> None:
    """Build Niuu single-binary distribution."""
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
