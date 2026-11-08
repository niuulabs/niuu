"""Timing reports must retain failed builds and measure the actual child process."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/measure_compilation.py"
spec = importlib.util.spec_from_file_location("measure_compilation", SCRIPT)
measurement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measurement)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_measure_preserves_exit_status_and_reports_duration(tmp_path, exit_code):
    output = tmp_path / "linux-amd64" / "timing.json"
    metadata = {
        "target": "linux-amd64",
        "compiler_cache_match": "cache-key",
        "postgres_cache_hit": "true",
    }
    command = [sys.executable, "-c", f"raise SystemExit({exit_code})"]
    assert measurement.measure(command, output, metadata) == exit_code
    record = json.loads(output.read_text())
    assert record["exit_code"] == exit_code
    assert record["seconds"] > 0
    assert record["command"] == command
    report = measurement.summary(tmp_path)
    assert f"| linux-amd64 | {record['seconds']:.3f} | {exit_code} | True | true |" in report
