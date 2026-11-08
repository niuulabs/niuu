"""Timing reports must retain failed builds and measure the actual child process."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/measure_binary_build.py"
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
    assert f"| linux-amd64 | {record['seconds']:.3f} | — | — | — | {exit_code} | True |" in report


def test_startup_uses_one_fresh_cache_and_forwards_subcommands(tmp_path, monkeypatch):
    binary = tmp_path / "niuu"
    binary.write_bytes(b"test executable")
    output = tmp_path / "timing.json"
    output.write_text("{}")
    cache = tmp_path / "runtime"
    monkeypatch.setenv("PYAPP_INSTALL_DIR_NIUU", str(cache))
    timestamps = iter(
        [0, 1, 1, 1.2, 2, 2.4, 3, 3.3] + [value for i in range(12) for value in (i, i + 0.25)]
    )
    monkeypatch.setattr(measurement.time, "perf_counter", lambda: next(timestamps))
    commands = []

    def run(command, **kwargs):
        assert kwargs["env"]["PYAPP_INSTALL_DIR_NIUU"] == str(cache)
        assert not Path(kwargs["env"]["NIUU_CONFIG"]).exists()
        cache.mkdir(exist_ok=True)
        commands.append(command[1:])
        text = "niuu 0.1.0\n" if command[1:] == ["--version"] else "Usage: niuu platform [OPTIONS]"
        return subprocess.CompletedProcess(command, 0, stdout=text)

    monkeypatch.setattr(measurement.subprocess, "run", run)
    measurement.measure_startup(binary, output)
    record = json.loads(output.read_text())
    assert record["first_start_seconds"] == 1
    assert record["warm_start_seconds"] == [0.2, 0.4, 0.3]
    assert record["warm_start_median_seconds"] == 0.3
    assert commands == [["--version"]] * 4 + [
        command
        for command in (
            ["platform", "--help"],
            ["platform", "up", "--help"],
            ["platform", "status"],
            ["sessions", "--help"],
        )
        for _ in range(3)
    ]
    assert record["warm_commands"]["platform status"]["median_seconds"] == 0.25
    assert record["warm_commands"]["sessions --help"]["seconds"] == [0.25] * 3


def test_startup_does_not_reuse_or_delete_an_existing_cache(tmp_path, monkeypatch):
    output = tmp_path / "timing.json"
    output.write_text("{}")
    monkeypatch.setenv("PYAPP_INSTALL_DIR_NIUU", str(tmp_path))
    with pytest.raises(ValueError, match="empty cache"):
        measurement.measure_startup(tmp_path / "niuu", output)
    assert output.exists()
