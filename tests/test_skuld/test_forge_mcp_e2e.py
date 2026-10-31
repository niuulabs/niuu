"""Process-level proof: agent-side stdio MCP → real broker process → durable log → Forge.

A real ``python -m skuld`` broker starts against a fake Forge (log head, log append,
session read). The real ``python -m skuld.forge_mcp`` stdio server is spawned the
way an agent CLI spawns it (the exact command the broker injects) and speaks MCP
JSON-RPC to it. ``notify`` must land in Forge's durable log as the shared-contract
notification turn and come back ``committed``.

Marked ``integration`` like the other Forge process suites (deselected by default):
``uv run pytest -m integration tests/test_skuld/test_forge_mcp_e2e.py -rs``.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from niuu.domain.notifications import draft_from_log_payload

pytestmark = pytest.mark.integration

SESSION_ID = str(uuid.uuid4())
SRC = Path(__file__).resolve().parents[2] / "src"
BOOT_TIMEOUT_S = 60.0


class FakeForge:
    """Just enough of /api/v1/forge for a broker's durable log and the MCP reads."""

    def __init__(self) -> None:
        self.logged: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        forge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _send(self, status: int, body: Any) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802
                base = f"/api/v1/forge/sessions/{SESSION_ID}"
                if self.path == f"{base}/log/head":
                    self._send(200, {"latest_seq": 0})
                elif self.path.split("?")[0] == base:
                    self._send(
                        200,
                        {
                            "id": SESSION_ID,
                            "instance_id": "e2e-node",
                            "coordination": {"project_id": "proj-e2e"},
                        },
                    )
                else:
                    self._send(404, {"detail": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == f"/api/v1/forge/sessions/{SESSION_ID}/log":
                    with forge.lock:
                        forge.logged.extend(body["entries"])
                    seqs = [entry["seq"] for entry in body["entries"]]
                    self._send(201, {"submitted": len(seqs), "latest_seq": max(seqs)})
                    return
                self._send(200, {})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def notification_turns(self) -> list[dict[str, Any]]:
        with self.lock:
            return [e for e in self.logged if draft_from_log_payload(e["payload"]) is not None]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _clean_env() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SKULD__", "VOLUNDR", "FORGE_", "NIUU_")) and key != "PYTHONPATH"
    }
    env["PYTHONPATH"] = str(SRC)
    return env


@pytest.fixture
def forge() -> Iterator[FakeForge]:
    fake = FakeForge()
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def broker(tmp_path: Path, forge: FakeForge) -> Iterator[dict[str, Any]]:
    port = _free_port()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = tmp_path / "runtime"
    env = _clean_env()
    env.update(
        {
            "SKULD__SESSION__ID": SESSION_ID,
            "SKULD__SESSION__NAME": "mcp-e2e",
            "SKULD__SESSION__WORKSPACE_DIR": str(workspace),
            "SKULD__HOST": "127.0.0.1",
            "SKULD__PORT": str(port),
            "SKULD__VOLUNDR_API_URL": forge.url,
            "SKULD__TRANSPORT_ADAPTER": "skuld.transports.subprocess.SubprocessTransport",
            "SKULD__EVENT_LOG_FLUSH_INTERVAL_MS": "50",
            "SKULD__FORGE_MCP__RUNTIME_DIR": str(runtime),
            "SKULD__EXTERNAL_API_TOKEN": "svc-e2e-credential",
        }
    )
    log = (tmp_path / "broker.log").open("w")
    process = subprocess.Popen(
        [sys.executable, "-m", "skuld"],
        cwd=workspace,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    token_file = runtime / "forge-mcp.token"
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    import urllib.request

    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError((tmp_path / "broker.log").read_text())
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1):
                if token_file.exists():
                    break
        except OSError:
            pass
        time.sleep(0.1)
    else:
        process.kill()
        raise AssertionError("broker did not become healthy")
    try:
        yield {"port": port, "token_file": token_file, "env": env, "log": tmp_path / "broker.log"}
    finally:
        process.terminate()
        process.wait(timeout=30)
        log.close()


def _rpc(process: subprocess.Popen[bytes], request: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(request).encode() + b"\n")
    process.stdin.flush()
    line = process.stdout.readline()
    assert line, "stdio server closed stdout"
    return json.loads(line)


def test_notify_round_trips_through_the_real_broker(broker, forge) -> None:
    from skuld.forge_mcp.integration import forge_mcp_command

    command = [
        *forge_mcp_command(),
        "--broker-url",
        f"http://127.0.0.1:{broker['port']}",
        "--token-file",
        str(broker["token_file"]),
        "--timeout",
        "30",
        "--max-concurrency",
        "2",
    ]
    stdio = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_clean_env(),
    )
    try:
        init = _rpc(
            stdio,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
            },
        )
        assert init["result"]["serverInfo"]["name"] == "forge"
        tools = _rpc(stdio, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in tools["result"]["tools"]]
        assert {"notify", "environment", "list_sessions", "send_message"} <= set(names)

        env = _rpc(
            stdio,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "environment", "arguments": {}},
            },
        )["result"]
        assert env["isError"] is False
        assert env["structuredContent"]["session"]["id"] == SESSION_ID
        assert env["structuredContent"]["project_id"] == "proj-e2e"
        assert "svc-e2e-credential" not in json.dumps(env)

        notify = _rpc(
            stdio,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "notify",
                    "arguments": {"kind": "milestone", "title": "E2E milestone reached"},
                },
            },
        )["result"]
        assert notify["isError"] is False, notify
        receipt = notify["structuredContent"]
        assert receipt["state"] == "committed"

        denied = _rpc(
            stdio,
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "send_message",
                    "arguments": {"session_id": str(uuid.uuid4()), "content": "hi"},
                },
            },
        )["result"]
        assert denied["isError"] is True and "'message' grant" in denied["content"][0]["text"]
    finally:
        stdio.stdin.close()
        stdio.wait(timeout=30)

    [entry] = forge.notification_turns()
    assert entry["seq"] == receipt["session_seq"]
    turn_id, draft = draft_from_log_payload(entry["payload"])
    assert turn_id == receipt["turn_id"] and draft.title == "E2E milestone reached"

    # The loopback routes refuse callers without this start's secret.
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{broker['port']}/api/forge-mcp/tools", method="GET"
    )
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(request, timeout=5)
    assert excinfo.value.code == 401
