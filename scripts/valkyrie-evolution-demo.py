#!/usr/bin/env python3
"""Run the Valkyrie evolution proof through Ravn's uv entry point."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    cmd = [
        "uv",
        "run",
        "--project",
        str(repo_root),
        "python",
        "-m",
        "ravn",
        "valkyrie-evolution-proof",
        *sys.argv[1:],
    ]
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
