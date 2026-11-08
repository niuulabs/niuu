"""Measure binary builds and first/warm launches without hiding failures."""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
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
        "## Binary build and startup timings",
        "",
        "Build time includes runtime assembly, wheel installation, archive creation and launcher "
        "compilation. Web/PostgreSQL builds and smoke tests are excluded. "
        "First start uses an empty installation cache; warm start is the median of three launches.",
        "",
        "| Target | Build (s) | First CLI (s) | Warm CLI (s) | Platform health (s) "
        "| Exit | Launcher cache |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for path in sorted(directory.rglob("timing.json")):
        record = json.loads(path.read_text())
        first = record.get("first_start_seconds", "—")
        warm = record.get("warm_start_median_seconds", "—")
        platform_file = path.with_name("platform.json")
        health = (
            json.loads(platform_file.read_text())["platform_startup_seconds"]
            if platform_file.exists()
            else "—"
        )
        lines.append(
            f"| {record['target']} | {record['seconds']:.3f} | {first} | {warm} | {health} "
            f"| {record['exit_code']} | {bool(record['compiler_cache_match'])} |"
        )
    lines.extend(
        [
            "",
            "### Warm command timings",
            "",
            "| Target | Command | Median (s) |",
            "| --- | --- | ---: |",
        ]
    )
    for path in sorted(directory.rglob("timing.json")):
        record = json.loads(path.read_text())
        for command, timing in record.get("warm_commands", {}).items():
            lines.append(f"| {record['target']} | `niuu {command}` | {timing['median_seconds']} |")
    lines.extend(
        ["", "See binary-build artifacts for raw measurements, toolchain and restored cache key."]
    )
    return "\n".join(lines) + "\n"


def measure_startup(binary: Path, output: Path) -> None:
    record = json.loads(output.read_text())
    with tempfile.TemporaryDirectory(prefix="niuu-startup-") as directory:
        env = dict(os.environ)
        env["NIUU_CONFIG"] = str(Path(directory) / "config.yaml")
        env["PATH"] = "/usr/bin:/bin"
        env["NO_COLOR"] = "1"
        env["TERM"] = "dumb"
        cache = Path(env.get("PYAPP_INSTALL_DIR_NIUU", str(Path(directory) / "runtime")))
        if cache.exists():
            raise ValueError(f"First-start measurement requires an empty cache: {cache}")
        env["PYAPP_INSTALL_DIR_NIUU"] = str(cache)
        elapsed = []
        for _ in range(4):
            started = time.perf_counter()
            result = subprocess.run(
                [str(binary.resolve()), "--version"],
                env=env,
                cwd=directory,
                text=True,
                capture_output=True,
                check=True,
            )
            elapsed.append(round(time.perf_counter() - started, 3))
            if not result.stdout.startswith("niuu "):
                raise RuntimeError(f"Unexpected binary identity: {result.stdout}")
        command_timings = {}
        for command in (
            ["platform", "--help"],
            ["platform", "up", "--help"],
            ["platform", "status"],
            ["sessions", "--help"],
        ):
            samples = []
            for _ in range(3):
                started = time.perf_counter()
                result = subprocess.run(
                    [str(binary.resolve()), *command],
                    env=env,
                    cwd=directory,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                samples.append(round(time.perf_counter() - started, 3))
                if "--help" in command and "Usage: niuu " not in result.stdout:
                    raise RuntimeError(
                        f"Subcommand help lost the niuu entry point: {result.stdout}"
                    )
            command_timings[" ".join(command)] = {
                "seconds": samples,
                "median_seconds": statistics.median(samples),
            }
    record.update(
        first_start_seconds=elapsed[0],
        warm_start_seconds=elapsed[1:],
        warm_start_median_seconds=statistics.median(elapsed[1:]),
        binary_size_bytes=binary.stat().st_size,
        warm_commands=command_timings,
    )
    output.write_text(json.dumps(record, indent=2) + "\n")


def main() -> int:
    if sys.argv[1:2] == ["--summary"]:
        print(summary(Path(sys.argv[2])))
        return 0
    if sys.argv[1:2] == ["--startup"]:
        measure_startup(Path(sys.argv[2]), Path("dist/timings/timing.json"))
        return 0
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise SystemExit("usage: measure_binary_build.py -- command [args ...]")
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
