#!/usr/bin/env python
"""Compatibility wrapper for the daemon resident autonomy proof.

The proof path is intentionally the production daemon plus configuration:

    ravn daemon --config scripts/setups/configs/resident-daemon-autonomy-proof.yaml

Keep resident setup, wake logic, delegation, and persistence in Ravn's daemon
composition root and YAML configuration rather than in this script.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_CONFIG = Path("scripts/setups/configs/resident-daemon-autonomy-proof.yaml")


def main() -> int:
    config = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONFIG
    return subprocess.call(
        [
            "ravn",
            "daemon",
            "--config",
            str(config),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
