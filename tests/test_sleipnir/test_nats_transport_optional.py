"""Regression coverage for installations without the optional NATS client."""

from __future__ import annotations

import subprocess
import sys


def test_nats_transport_module_imports_without_nats() -> None:
    script = """
import sys

class BlockNats:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "nats" or fullname.startswith("nats."):
            raise ImportError("blocked optional dependency")
        return None

sys.meta_path.insert(0, BlockNats())
from sleipnir.adapters.nats_transport import _HttpConnectNatsClient, nats_available

assert not nats_available()
try:
    _HttpConnectNatsClient("http://127.0.0.1:3128")
except ImportError:
    pass
else:
    raise AssertionError("client construction must require nats-py")
"""

    subprocess.run([sys.executable, "-c", script], check=True)
