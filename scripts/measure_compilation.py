"""Measure the real Nuitka invocation, preserving its output and exit status."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path


def measure(command: list[str], output: Path, metadata: dict) -> int:
    started = time.perf_counter()
    result = subprocess.run(command, check=False)
    record = {
        **metadata,
        "seconds": round(time.perf_counter() - started, 3),
        "exit_code": result.returncode,
        "command": command,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    return result.returncode


def summary(directory: Path) -> str:
    lines = [
        "## Nuitka compilation timings",
        "",
        "Compilation includes Python analysis, C compilation, linking and onefile packing. "
        "Dependency installation, web/PostgreSQL builds and smoke tests are excluded.",
        "",
        "| Target | Seconds | Exit code | Compiler cache restored | PostgreSQL cache hit |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    for path in sorted(directory.rglob("timing.json")):
        record = json.loads(path.read_text())
        lines.append(
            f"| {record['target']} | {record['seconds']:.3f} | {record['exit_code']} "
            f"| {bool(record['compiler_cache_match'])} | {record['postgres_cache_hit'] == 'true'} |"
        )
    lines.extend(
        ["", "See compilation artifacts for toolchain, cache keys and ccache hit/miss counts."]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    if sys.argv[1:2] == ["--summary"]:
        print(summary(Path(sys.argv[2])))
        return 0
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("usage: measure_compilation.py -- command [args ...]")
    return measure(
        command,
        Path("dist/timings/timing.json"),
        {
            "target": os.environ["BUILD_TARGET"],
            "commit": os.environ["GITHUB_SHA"],
            "run_id": os.environ["GITHUB_RUN_ID"],
            "run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
            "python": platform.python_version(),
            "compiler_cache_match": os.environ["COMPILER_CACHE_MATCH"],
            "postgres_cache_hit": os.environ["POSTGRES_CACHE_HIT"],
        },
    )


if __name__ == "__main__":
    raise SystemExit(main())
