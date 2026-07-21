"""Composition checks for an ordinary, non-collaborative Skuld session."""

from __future__ import annotations

import subprocess
import sys


def test_default_broker_does_not_load_collaboration_surface(tmp_path) -> None:
    script = f"""
import sys
from skuld.broker import Broker
from skuld.config import SkuldSettings

settings = SkuldSettings(workspace_path={str(tmp_path)!r})
broker = Broker(settings)
assert broker._room_bridge is None
assert broker._collaboration_mesh_bridge is None
assert broker._observation_relay is None
for module in (
    "niuu.collaboration.room",
    "niuu.collaboration.mesh",
    "niuu.collaboration.observation_relay",
    "skuld.collaboration_adapter",
):
    assert module not in sys.modules, module
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
