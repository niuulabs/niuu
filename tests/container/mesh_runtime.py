"""Exercise native NNG IPC across processes in the installed runtime image."""

import subprocess
import sys
import tempfile
from pathlib import Path

import pynng

CHILD = """
import sys
import pynng

with pynng.Pair0(dial=sys.argv[1], recv_timeout=3000, send_timeout=3000) as peer:
    peer.send(b"mesh-runtime-request")
    assert peer.recv() == b"mesh-runtime-response"
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="niuu-mesh-") as directory:
        address = f"ipc://{Path(directory) / 'check.sock'}"
        with pynng.Pair0(listen=address, recv_timeout=3000, send_timeout=3000) as peer:
            child = subprocess.Popen([sys.executable, "-c", CHILD, address])
            try:
                assert peer.recv() == b"mesh-runtime-request"
                peer.send(b"mesh-runtime-response")
                assert child.wait(timeout=5) == 0
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
    print("Native mesh IPC exchange passed")


if __name__ == "__main__":
    main()
