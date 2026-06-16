#!/usr/bin/env python
# ruff: noqa: E402,I001
"""Run the resident Valkyrie Environment MVP demo."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ravn.demo.valkyrie_environment import main


if __name__ == "__main__":
    raise SystemExit(main())
