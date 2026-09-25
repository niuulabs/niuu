"""Real API process restart + full lifespan must preserve live Skuld sessions.

No production service, database, credentials or model provider is used. These
tests include terminal DB rows with a genuinely live gateway/native fixture,
the precise mismatch that exposed the September 13 startup cleanup incident.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from websockets.sync.client import connect

from volundr.domain.models import LocalMountSource, Session, SessionStatus

_linux_only = pytest.mark.skipif(sys.platform != "linux", reason="Linux process identity proof")


def _terminate_if_alive(process):
    """Send SIGTERM to a still-live subprocess, tolerating an exit/signal race.

    ``process.poll() is None`` only proves liveness at that instant. The real
    process (uvicorn, here) can exit on its own — graceful shutdown, CI
    resource pressure — in the gap before the signal lands. ``Popen.terminate``
    re-checks liveness internally but still calls ``os.kill()`` on a pid that
    can vanish in that same window, raising ``ProcessLookupError`` for a
    process that is already exactly what we wanted: gone. This is the CI
    failure this helper fixes (ProcessLookupError from this exact teardown
    call), not a broadened catch — it guards only the terminate/wait pair.
    """
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=15)
    except ProcessLookupError:
        pass


def test_terminate_if_alive_tolerates_process_exiting_between_check_and_signal():
    """Deterministic reproduction of the teardown race that raised
    ``ProcessLookupError: [Errno 3] No such process`` in the Forge Stability
    gate: the liveness check (``poll() is None``) passes, then the process
    exits before the signal reaches it. Faked instead of raced against a real
    process so the interleaving is exact and platform-independent.
    """

    class ExitsBetweenCheckAndSignal:
        def poll(self):
            return None  # still alive at the liveness check

        def terminate(self):
            raise ProcessLookupError(3, "No such process")  # ...gone by the signal

        def wait(self, timeout=None):
            raise AssertionError("wait() must not run after terminate() raised")

    _terminate_if_alive(ExitsBetweenCheckAndSignal())  # must not raise


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    assert fields[0] != "Z", f"Process {pid} is a zombie"
    return {"pid": pid, "start_ticks": fields[19]}


def _wait_http(url, process, log_path):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, log_path.read_text()
        try:
            response = httpx.get(url, timeout=0.5, trust_env=False)
            if response.status_code == 200:
                return response.json()
        except httpx.TransportError:
            pass
        time.sleep(0.05)
    raise AssertionError(f"Readiness deadline exceeded: {url}")


def _receive_until(ws, needle):
    frames = []
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        frames.append(json.loads(ws.recv(timeout=max(0.01, deadline - time.monotonic()))))
        if needle in json.dumps(frames[-1]):
            return frames
    raise AssertionError(f"No {needle} in {frames}")


@_linux_only
@pytest.mark.parametrize(
    "status",
    [SessionStatus.RUNNING, SessionStatus.STOPPED, SessionStatus.ARCHIVED, SessionStatus.FAILED],
)
def test_full_api_restart_preserves_processes_turn_and_proxy_reconnect(tmp_path, status):
    root = Path(__file__).resolve().parents[2]
    # Explicit opt-in for testing an immutable candidate/rollback source with
    # this same fixture. No production config or state is ever reused.
    source_root = Path(os.environ.get("FORGE_RESTART_PROOF_SOURCE", str(root))).resolve()
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    api_port, gateway_port = _port(), _port()
    session = Session(
        id=uuid4(),
        name="owned-restart-proof",
        model="test-no-provider",
        status=status,
        source=LocalMountSource(local_path=str(workspace)),
    )
    # Allowlist environment: no live configuration, provider credentials, CLI
    # home, database, sockets or platform endpoints can leak into children.
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": f"{source_root / 'src'}:{root}",
        "NIUU_CONFIG": str(tmp_path / "absent.yaml"),
    }
    gateway_env = {
        **env,
        "SKULD__HOST": "127.0.0.1",
        "SKULD__PORT": str(gateway_port),
        "SKULD__SESSION__ID": str(session.id),
        "SKULD__SESSION__WORKSPACE_DIR": str(workspace),
        "SKULD__TRANSPORT_ADAPTER": "tests.support.forge.restart_fixture.HoldingTransport",
        "SKULD__VOLUNDR_API_URL": "",
        "SKULD__EVENT_LOG_ENABLED": "false",
        "SKULD__CHRONICLE_WATCHER_ENABLED": "false",
    }
    config = {
        "session": session.model_dump(mode="json"),
        "api_port": api_port,
        "gateway_port": gateway_port,
        "gateway_env": gateway_env,
        "gateway_log": str(tmp_path / "gateway.log"),
        "workspace": str(workspace),
        "state_file": str(tmp_path / "processes.json"),
    }
    config_path = tmp_path / "fixture.json"
    config_path.write_text(json.dumps(config))
    api = None
    gateway_pid = native_pid = None
    api_identities = []
    rest = f"http://127.0.0.1:{api_port}"
    ws_url = f"ws://127.0.0.1:{api_port}/s/{session.id}/session"
    with (tmp_path / "api.log").open("ab") as log:

        def launch():
            proc = subprocess.Popen(
                [sys.executable, "-m", "tests.support.forge.restart_fixture", str(config_path)],
                env=env,
                cwd=tmp_path,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            api_identities.append(_identity(proc.pid))
            return proc

        try:
            api = launch()
            initial = _wait_http(rest + "/fixture/status", api, tmp_path / "api.log")
            assert initial["backend"] == "process"
            assert initial["session"] == session.model_dump(mode="json")
            _wait_http(f"http://127.0.0.1:{gateway_port}/health", api, tmp_path / "api.log")
            gateway_pid = json.loads(Path(config["state_file"]).read_text())[str(session.id)]["pid"]
            gateway_identity = _identity(gateway_pid)
            with connect(ws_url, proxy=None) as first:
                _receive_until(first, "capabilities")
                first.send(json.dumps({"type": "message", "content": "hold this synthetic turn"}))
                _receive_until(first, "BEFORE_RESTART")
                native_pid = int((workspace / "native.pid").read_text())
                native_identity = _identity(native_pid)
                # Actual API SIGTERM while its proxied WS is attached. We do
                # not kill/restart the gateway or native fixture to get a pass.
                api.terminate()
                api.wait(timeout=15)
            assert _identity(gateway_pid) == gateway_identity
            assert _identity(native_pid) == native_identity
            api = launch()
            assert _wait_http(rest + "/fixture/status", api, tmp_path / "api.log") == initial
            with connect(ws_url, proxy=None) as second:
                _receive_until(second, "BEFORE_RESTART")
                # Also exercise abrupt API death: both restarts must rebuild
                # routing and preserve the same still-running synthetic turn.
                api.kill()
                api.wait(timeout=5)
            (workspace / "emit-offline").touch()
            deadline = time.monotonic() + 5
            while (workspace / "emit-offline").exists():
                assert time.monotonic() < deadline, "Owned turn did not progress during API outage"
                time.sleep(0.05)
            api = launch()
            assert _wait_http(rest + "/fixture/status", api, tmp_path / "api.log") == initial
            with connect(ws_url, proxy=None) as third:
                _receive_until(third, "DURING_API_OUTAGE")
                # Allow the real periodic startup-reconciliation loop to run
                # as well; an idle-but-live turn must not be reaped later.
                time.sleep(5.2)
                assert _identity(gateway_pid) == gateway_identity
                assert _identity(native_pid) == native_identity
                (workspace / "release-turn").touch()
                _receive_until(third, '"type": "result"')
            history = httpx.get(
                rest + f"/s/{session.id}/api/conversation/history", trust_env=False
            ).json()
            assert "BEFORE_RESTART" in json.dumps(history)
            assert "AFTER_RECONNECT" in json.dumps(history)
            assert "DURING_API_OUTAGE" in json.dumps(history)
            assert len((workspace / "sends.jsonl").read_text().splitlines()) == 1
            assert _identity(gateway_pid) == gateway_identity
            assert _identity(native_pid) == native_identity
            assert len({p["pid"] for p in api_identities}) == 3
            (tmp_path / "preservation-proof.json").write_text(
                json.dumps(
                    {
                        "source_root": str(source_root),
                        "stored_status": status.value,
                        "api_processes": api_identities,
                        "gateway": gateway_identity,
                        "native_fixture": native_identity,
                        "native_send_count": 1,
                        "same_inflight_turn_completed": True,
                        "output_during_api_outage_replayed": True,
                        "proxy_reconnections": 2,
                        "stored_status_unchanged": True,
                        "provider_calls": 0,
                        "database": "mocked test infrastructure",
                        "passed": True,
                    },
                    indent=2,
                )
            )
        finally:
            _terminate_if_alive(api)
            # Clean up only test-owned identities, even on assertion failure.
            owned = {}
            for kind in ("gateway", "native"):
                path = workspace / f"{kind}.identity.json"
                if path.exists():
                    item = json.loads(path.read_text())
                    owned[item["pid"]] = item
            gateway_path = workspace / "gateway.identity.json"
            if gateway_path.exists():
                gateway_pid = json.loads(gateway_path.read_text())["pid"]
                try:
                    if _identity(gateway_pid) == owned[gateway_pid]:
                        os.kill(gateway_pid, signal.SIGTERM)
                except (FileNotFoundError, ProcessLookupError, AssertionError):
                    pass
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    _identity(gateway_pid)
                except (FileNotFoundError, AssertionError):
                    break
                time.sleep(0.05)
            for pid, original in owned.items():
                try:
                    if _identity(pid) == original:
                        os.kill(pid, signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError, AssertionError):
                    pass
