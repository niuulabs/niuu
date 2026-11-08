"""The ``skuld.forge_mcp`` stdio server: MCP JSON-RPC over stdio, forwarded to the broker."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skuld.forge_mcp.stdio import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    LATEST_PROTOCOL_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    BrokerClient,
    StdioServer,
    main,
    negotiate_protocol_version,
)

SECRET = "loopback-secret"
TOOLS = [{"name": "notify", "description": "d", "inputSchema": {"type": "object"}}]


class FakeBroker:
    """Serves /api/forge-mcp/tools like the broker, checking the loopback secret."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.delay_s = 0.0
        broker = self

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

            def _authorized(self) -> bool:
                if self.headers.get("authorization") == f"Bearer {SECRET}":
                    return True
                self._send(401, {"detail": "missing or invalid broker loopback token"})
                return False

            def do_GET(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                self._send(200, {"tools": TOOLS})

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    return
                length = int(self.headers.get("content-length") or 0)
                arguments = json.loads(self.rfile.read(length) or b"{}")
                name = self.path.rsplit("/", 1)[-1]
                broker.calls.append((name, arguments, self.headers.get("authorization")))
                if broker.delay_s:
                    time.sleep(broker.delay_s)
                if name != "notify":
                    self._send(404, {"detail": f"unknown tool: {name}"})
                    return
                self._send(
                    200,
                    {
                        "content": [{"type": "text", "text": "ok"}],
                        "structuredContent": {"state": "committed", "echo": arguments},
                        "isError": False,
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_broker() -> Iterator[FakeBroker]:
    broker = FakeBroker()
    yield broker
    broker.close()


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "forge-mcp.token"
    path.write_text(SECRET + "\n")
    path.chmod(0o600)
    return path


def _serve(client: BrokerClient, *messages: object, raw: bytes = b"") -> list[Any]:
    stdin = io.BytesIO(raw + b"".join(json.dumps(m).encode() + b"\n" for m in messages))
    stdout = io.BytesIO()
    StdioServer(client, stdin=stdin, stdout=stdout, max_concurrency=2).serve()
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def _by_id(frames: list[Any]) -> dict[Any, Any]:
    return {frame.get("id"): frame for frame in frames if isinstance(frame, dict)}


INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


class TestInProcess:
    def test_handshake_list_and_call(self, fake_broker, token_file) -> None:
        client = BrokerClient(fake_broker.url, str(token_file), timeout_s=5)
        frames = _serve(
            client,
            INIT,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "notify", "arguments": {"kind": "info", "title": "hi"}},
            },
        )
        responses = _by_id(frames)
        assert len(frames) == 4  # the notification gets no response
        init = responses[1]["result"]
        assert init["protocolVersion"] == "2025-06-18"
        assert init["capabilities"] == {"tools": {"listChanged": False}}
        assert init["serverInfo"]["name"] == "forge" and "notify" in init["instructions"]
        assert responses[2]["result"] == {}
        assert responses[3]["result"]["tools"] == TOOLS
        call = responses[4]["result"]
        assert call["isError"] is False
        assert call["structuredContent"]["echo"] == {"kind": "info", "title": "hi"}
        assert fake_broker.calls == [
            ("notify", {"kind": "info", "title": "hi"}, f"Bearer {SECRET}")
        ]

    def test_protocol_errors(self, fake_broker, token_file) -> None:
        client = BrokerClient(fake_broker.url, str(token_file), timeout_s=5)
        frames = _serve(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "nope"}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "notify", "arguments": [1]},
            },
            {"jsonrpc": "2.0", "id": 5, "method": "ping", "params": [1]},
            {"jsonrpc": "1.0", "id": 6, "method": "ping"},
            {"jsonrpc": "2.0", "id": 7, "method": 5},
            {"jsonrpc": "2.0", "id": 99, "result": {}},  # a stray client response: ignored
            raw=b"{not json\n\n",
        )
        codes = {frame["id"]: frame["error"]["code"] for frame in frames if "error" in frame}
        assert codes[None] in (PARSE_ERROR, INVALID_REQUEST)
        assert codes[1] == METHOD_NOT_FOUND
        assert codes[2] == INVALID_PARAMS
        assert codes[3] == INVALID_PARAMS
        assert codes[4] == INVALID_PARAMS
        assert codes[5] == INVALID_PARAMS
        assert codes[7] == INVALID_REQUEST
        assert 99 not in codes
        parse_errors = [f for f in frames if f.get("error", {}).get("code") == PARSE_ERROR]
        assert len(parse_errors) == 1

    def test_batch_is_answered_as_an_array(self, fake_broker, token_file) -> None:
        client = BrokerClient(fake_broker.url, str(token_file), timeout_s=5)
        stdin = io.BytesIO(
            json.dumps(
                [
                    {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    {"jsonrpc": "2.0", "method": "notifications/initialized"},
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                ]
            ).encode()
            + b"\n"
        )
        stdout = io.BytesIO()
        StdioServer(client, stdin=stdin, stdout=stdout, max_concurrency=1).serve()
        [batch] = [json.loads(line) for line in stdout.getvalue().splitlines()]
        assert [frame["id"] for frame in batch] == [1, 2]

    def test_broker_failures(self, fake_broker, tmp_path) -> None:
        wrong = tmp_path / "wrong.token"
        wrong.write_text("nope")
        call = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "notify", "arguments": {}},
        }
        listing = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        # Wrong secret: the call is an isError tool result, the listing a protocol error.
        frames = _by_id(_serve(BrokerClient(fake_broker.url, str(wrong), 5), call, listing))
        assert frames[1]["result"]["isError"] is True
        assert "401" in frames[1]["result"]["content"][0]["text"]
        assert frames[2]["error"]["code"] == INTERNAL_ERROR
        # Missing token file and an unreachable broker behave the same way.
        missing = BrokerClient(fake_broker.url, str(tmp_path / "absent"), 5)
        assert "token file" in _serve(missing, call)[0]["result"]["content"][0]["text"]
        down = BrokerClient("http://127.0.0.1:9", str(wrong), 5)
        assert "unreachable" in _serve(down, call)[0]["result"]["content"][0]["text"]

    def test_tool_calls_do_not_block_ping(self, fake_broker, token_file) -> None:
        fake_broker.delay_s = 0.3
        client = BrokerClient(fake_broker.url, str(token_file), timeout_s=5)
        frames = _serve(
            client,
            {
                "jsonrpc": "2.0",
                "id": "slow",
                "method": "tools/call",
                "params": {"name": "notify", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": "fast", "method": "ping"},
        )
        assert [frame["id"] for frame in frames] == ["fast", "slow"]

    @pytest.mark.parametrize(
        ("requested", "expected"),
        [
            ("2024-11-05", "2024-11-05"),
            ("2025-11-25", "2025-11-25"),
            ("1999-01-01", LATEST_PROTOCOL_VERSION),
            (None, LATEST_PROTOCOL_VERSION),
        ],
    )
    def test_version_negotiation(self, requested, expected) -> None:
        assert negotiate_protocol_version(requested) == expected

    def test_main_reads_stdio(self, fake_broker, token_file, monkeypatch) -> None:
        stdin = io.TextIOWrapper(io.BytesIO(json.dumps(INIT).encode() + b"\n"))
        stdout = io.TextIOWrapper(io.BytesIO())
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", stdout)
        argv = [
            "--broker-url",
            fake_broker.url,
            "--token-file",
            str(token_file),
            "--timeout",
            "5",
            "--max-concurrency",
            "1",
        ]
        assert main(argv) == 0
        stdout.flush()
        [frame] = [json.loads(line) for line in stdout.buffer.getvalue().splitlines()]
        assert frame["result"]["serverInfo"]["name"] == "forge"


def _spawn(fake_broker: FakeBroker, token_file: Path) -> subprocess.Popen[bytes]:
    from skuld.forge_mcp.integration import forge_mcp_command

    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    src = Path(__file__).resolve().parents[2] / "src"
    env["PYTHONPATH"] = str(src)  # the worktree under test, never an ambient release
    return subprocess.Popen(
        [
            *forge_mcp_command(),
            "--broker-url",
            fake_broker.url,
            "--token-file",
            str(token_file),
            "--timeout",
            "5",
            "--max-concurrency",
            "2",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=token_file.parent,
    )


def test_module_entry_point(fake_broker, token_file, monkeypatch) -> None:
    import runpy

    stdin = io.TextIOWrapper(io.BytesIO(b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'))
    stdout = io.TextIOWrapper(io.BytesIO())
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "skuld.forge_mcp",
            "--broker-url",
            fake_broker.url,
            "--token-file",
            str(token_file),
            "--timeout",
            "5",
            "--max-concurrency",
            "2",
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("skuld.forge_mcp", run_name="__main__")
    assert excinfo.value.code == 0
    stdout.flush()
    assert json.loads(stdout.buffer.getvalue()) == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_timeout_and_concurrency_have_no_hidden_defaults(token_file, capsys) -> None:
    """The broker always passes its configured values; there is no second default."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--broker-url", "http://127.0.0.1:1", "--token-file", str(token_file)])
    assert excinfo.value.code == 2
    assert "--timeout" in capsys.readouterr().err


class TestSubprocess:
    """The real module, spawned the way an agent CLI spawns it."""

    def test_json_rpc_round_trip(self, fake_broker, token_file) -> None:
        process = _spawn(fake_broker, token_file)
        requests = [
            INIT,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "notify", "arguments": {"kind": "milestone", "title": "t"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "bogus", "arguments": {}},
            },
            {"jsonrpc": "2.0", "id": 5, "method": "ping"},
        ]
        payload = b"".join(json.dumps(r).encode() + b"\n" for r in requests)
        started = time.monotonic()
        stdout, stderr = process.communicate(payload, timeout=30)
        assert process.returncode == 0, stderr.decode()
        assert time.monotonic() - started < 10
        frames = [json.loads(line) for line in stdout.splitlines()]  # stdout: frames only
        responses = _by_id(frames)
        assert set(responses) == {1, 2, 3, 4, 5}
        assert responses[1]["result"]["protocolVersion"] == "2025-06-18"
        assert responses[2]["result"]["tools"] == TOOLS
        assert responses[3]["result"]["structuredContent"]["state"] == "committed"
        assert responses[4]["error"]["code"] == INVALID_PARAMS
        assert fake_broker.calls[0][2] == f"Bearer {SECRET}"
        assert SECRET.encode() not in stdout + stderr

    def test_secret_never_reaches_argv(self, fake_broker, token_file) -> None:
        process = _spawn(fake_broker, token_file)
        cmdline = b""
        try:
            deadline = time.monotonic() + 10
            while not cmdline and time.monotonic() < deadline:  # populated just after exec
                cmdline = Path(f"/proc/{process.pid}/cmdline").read_bytes()
                time.sleep(0.01)
        finally:
            process.communicate(b"", timeout=30)
        assert SECRET.encode() not in cmdline
        assert str(token_file).encode() in cmdline
