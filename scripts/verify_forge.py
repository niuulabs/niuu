#!/usr/bin/env python3
"""Run reproducible Forge gates; fail on empty or silently skipped required lanes."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIT_PATHS = [
    "tests/test_skuld",
    "tests/test_services",
    "tests/test_adapters",
    "tests/test_domain",
    "tests/test_volundr",
    "tests/test_flock",
    "tests/test_rest.py",
    "tests/test_session_archive.py",
    "tests/test_log_aggregate.py",
    "tests/test_broadcaster.py",
    "tests/test_scripts/test_verify_forge.py",
    "tests/test_scripts/test_forge_live.py",
    "tests/test_scripts/test_forge_corpus.py",
    "tests/test_scripts/test_forge_text_projection.py",
    "tests/test_niuu/test_forge_replay_facade.py",
    "tests/test_niuu/test_transcript_result_failures.py",
    "tests/test_niuu/test_rest_volundr.py",
]
LANES = ("unit", "tmux", "database", "web", "live-grok", "live-muse")


def inspect_junit(path: Path, *, strict_skips: bool = False) -> dict:
    """An absent/empty/all-skipped suite cannot serve as a successful gate."""
    cases = list(ET.parse(path).iter("testcase"))
    if not cases:
        raise ValueError("the gate collected no test cases")
    result = {"tests": len(cases), "passed": 0, "failures": 0, "skipped": []}
    for case in cases:
        identity = f"{case.get('classname')}.{case.get('name')}"
        skip = case.find("skipped")
        if skip is not None:
            result["skipped"].append({"test": identity, "reason": skip.get("message", "")})
            if strict_skips:
                raise ValueError(f"required test did not execute: {identity}")
            continue
        if case.find("failure") is not None or case.find("error") is not None:
            result["failures"] += 1
            continue
        result["passed"] += 1
    if not result["passed"]:
        raise ValueError("the gate has no passing, executed tests")
    if result["failures"]:
        raise ValueError(f"the gate has {result['failures']} failed tests")
    return result


def command_for(lane: str, report: Path, *, coverage: bool = False) -> list[str]:
    if lane == "web":
        return [
            "pnpm",
            "exec",
            "vitest",
            "run",
            "packages/plugin-volundr/src",
            "--reporter=default",
            "--reporter=junit",
            f"--outputFile.junit={report}",
        ]
    command = [sys.executable, "-m", "pytest", "-o", "addopts=", "--strict-markers", "-W", "error"]
    if lane == "unit":
        command += UNIT_PATHS + [
            "-m",
            "not integration and not broker and not kind_integration and not live_cli",
        ]
        if coverage:
            command += [
                "--cov=src/skuld",
                "--cov-report=term-missing",
                f"--cov-report=xml:{report.with_suffix('.coverage.xml')}",
                "--cov-fail-under=85",
            ]
    elif lane == "tmux":
        command += ["tests/test_skuld", "-m", "tmux"]
    elif lane == "database":
        command += [
            "tests/integration/volundr",
            "tests/test_adapters/test_pg_history_import.py",
            "tests/test_adapters/test_message_delivery_postgres_integration.py",
            "tests/test_skuld/test_forge_nul_persistence.py::test_nul_entries_persist_to_real_pg_and_round_trip",
            "tests/test_skuld/test_forge_nul_persistence.py::test_real_pg_rejects_raw_nul_without_sanitization",
            "-m",
            "integration",
        ]
    else:
        engine = lane.removeprefix("live-")
        command += [f"tests/test_skuld/test_{engine}_e2e.py", "-m", "live_cli"]
    return command + ["-q", "--tb=short", "-ra", f"--junitxml={report}"]


def check_prerequisites(lane: str) -> None:
    binary = {"tmux": "tmux", "web": "pnpm", "live-grok": "grok", "live-muse": "muse"}.get(lane)
    if binary and not shutil.which(binary):
        raise ValueError(f"required executable is missing: {binary}")
    if lane.startswith("live-") and os.environ.get("FORGE_LIVE_CLI") != "1":
        raise ValueError("live provider tests require explicit FORGE_LIVE_CLI=1")
    if lane == "database":
        required = (
            "TEST_DATABASE_HOST",
            "TEST_DATABASE_PORT",
            "TEST_DATABASE_USER",
            "TEST_DATABASE_PASSWORD",
            "TEST_DATABASE_NAME",
        )
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise ValueError("configure a disposable test database: " + ", ".join(missing))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument(
        "--repeat", type=int, default=1, help="independent required runs, not retries"
    )
    parser.add_argument("--timeout", type=float, default=900, help="seconds per run")
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".forge-results")
    parser.add_argument(
        "--coverage", action="store_true", help="enforce 85%% Skuld coverage in unit lane"
    )
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        parser.error("repeat and timeout must be positive")
    if args.coverage and args.lane != "unit":
        parser.error("--coverage applies to the unit lane")
    directory = args.artifacts.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    manifest = {
        "lane": args.lane,
        "revision": revision,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "runs": [],
        "status": "failed",
    }
    manifest["working_tree_dirty"] = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    try:
        check_prerequisites(args.lane)
        for index in range(1, args.repeat + 1):
            report = directory / f"{args.lane}-{index}.xml"
            report.unlink(missing_ok=True)  # a stale green report must never satisfy this run
            command = command_for(args.lane, report, coverage=args.coverage)
            print(f"Forge {args.lane}: required run {index}/{args.repeat}", flush=True)
            started = time.monotonic()
            completed = subprocess.run(
                command,
                cwd=ROOT / "web-next" if args.lane == "web" else ROOT,
                timeout=args.timeout,
                check=False,
            )
            run = {"command": command, "seconds": round(time.monotonic() - started, 2)}
            manifest["runs"].append(run)
            run["exit_code"] = completed.returncode
            run["results"] = inspect_junit(
                report, strict_skips=args.lane in ("tmux", "database", "live-grok", "live-muse")
            )
            if completed.returncode:
                raise ValueError(f"test command failed with exit code {completed.returncode}")
        manifest["status"] = "passed"
        return 0
    except (ValueError, OSError, ET.ParseError, subprocess.TimeoutExpired) as exc:
        manifest["error"] = str(exc)
        print(f"Forge gate failed: {exc}", file=sys.stderr)
        return 1
    finally:
        (directory / f"{args.lane}-summary.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
