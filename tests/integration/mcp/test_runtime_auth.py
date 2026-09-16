"""Check actual runtime binaries without live provider accounts or model calls."""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_pinned_runtimes_read_rotating_mcp_credentials():
    image = os.environ.get("SKULD_RUNTIME_IMAGE")
    if not image:
        pytest.skip("Set SKULD_RUNTIME_IMAGE to the pinned deployment image")
    root = Path(__file__).resolve().parents[3]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/opt/venv/bin/python",
            "-v",
            f"{root / 'src'}:/testsrc:ro",
            "-v",
            f"{Path(__file__).with_name('runtime_probe.py')}:/tmp/check.py:ro",
            "-e",
            "PYTHONPATH=/testsrc",
            image,
            "/tmp/check.py",
        ],
        check=True,
        timeout=120,
        capture_output=True,
        text=True,
    )
