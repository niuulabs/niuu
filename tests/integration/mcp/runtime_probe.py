"""Isolated CLI probe run inside the pinned runtime image, with networking disabled."""

import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from skuld.transports.mcp_config import build_claude_mcp_config, build_codex_mcp_overrides

seen = []


class MCP(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_error(405)

    def do_DELETE(self):
        self.send_response(200)
        self.end_headers()

    def do_POST(self):
        seen.append(self.headers.get("Authorization"))
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in data:
            self.send_response(202)
            self.end_headers()
            return
        method = data["method"]
        result = (
            {
                "protocolVersion": data.get("params", {}).get("protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "isolated-test", "version": "1"},
            }
            if method == "initialize"
            else {"tools": []}
            if method == "tools/list"
            else {}
        )
        body = json.dumps({"jsonrpc": "2.0", "id": data["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), MCP)
threading.Thread(target=server.serve_forever, daemon=True).start()
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    token = root / "token.json"
    token.write_text(
        json.dumps({"access_token": "test-initial", "expires_at": "2099-01-01T00:00:00Z"})
    )
    config = [
        {
            "name": "renewal-test",
            "type": "http",
            "url": f"http://127.0.0.1:{server.server_port}/mcp",
            "credential_file": str(token),
            "credential_format": "oauth",
        }
    ]
    child = """import json
import os
import sys

assert os.environ.get("MCP_PROBE_TOKEN") == "test-stdio"
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    result = {}
    if request["method"] == "initialize":
        result = {
            "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "stdio-env-ok", "version": "1"},
        }
    elif request["method"] == "tools/list":
        result = {"tools": []}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""
    config.append(
        {
            "name": "stdio-test",
            "command": sys.executable,
            "args": ["-u", "-c", child],
            "env_vars": ["MCP_PROBE_TOKEN"],
        }
    )
    env = {
        **os.environ,
        "CLAUDE_CONFIG_DIR": str(root / "claude"),
        "CODEX_HOME": str(root / "codex"),
        "MCP_PROBE_TOKEN": "test-stdio",
    }
    # These directories are isolated test configuration, never the user's home.
    payload = json.loads(build_claude_mcp_config(config))["mcpServers"]["renewal-test"]
    subprocess.run(
        ["claude", "mcp", "add-json", "--scope", "user", "renewal-test", json.dumps(payload)],
        env=env,
        check=True,
        capture_output=True,
    )
    for value in ("test-initial", "test-rotated"):
        token.write_text(json.dumps({"access_token": value, "expires_at": "2099-01-01T00:00:00Z"}))
        seen.clear()
        result = subprocess.run(
            ["claude", "mcp", "list"], env=env, capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0 and "Bearer " + value in seen, (
            result.stdout,
            result.stderr,
            len(seen),
        )
    print("Claude header helper: initial and rotated token accepted")
    (root / "codex").mkdir()
    cmd = ["codex", "app-server", "--strict-config"]
    for key, value in build_codex_mcp_overrides(config):
        cmd.extend(["-c", key + "=" + value])
    proc = subprocess.Popen(
        cmd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=open(root / "codex-errors", "w"),
        text=True,
        bufsize=1,
    )

    def request(i, method, params):
        proc.stdin.write(json.dumps({"id": i, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        until = time.monotonic() + 25
        while time.monotonic() < until:
            if not select.select([proc.stdout], [], [], 1)[0]:
                continue
            line = proc.stdout.readline()
            if not line:
                raise RuntimeError("Codex exited: " + (root / "codex-errors").read_text())
            data = json.loads(line)
            if data.get("id") == i:
                return data
        raise RuntimeError("Codex request timed out")

    try:
        assert "result" in request(
            1,
            "initialize",
            {
                "clientInfo": {"name": "renewal-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        proc.stdin.write('{"method":"initialized","params":{}}\n')
        proc.stdin.flush()
        seen.clear()
        token.write_text(
            json.dumps({"access_token": "codex-initial", "expires_at": "2099-01-01T00:00:00Z"})
        )
        status = request(2, "mcpServerStatus/list", {"limit": 100})
        stdio = next(item for item in status["result"]["data"] if item["name"] == "stdio-test")
        assert stdio["serverInfo"]["name"] == "stdio-env-ok", stdio
        assert "Bearer codex-initial" in seen, (
            "Codex did not invoke dynamic header helper",
            status,
        )
        seen.clear()
        token.write_text(
            json.dumps({"access_token": "codex-rotated", "expires_at": "2099-01-01T00:00:00Z"})
        )
        status = request(3, "mcpServerStatus/list", {"limit": 100})
        assert "Bearer codex-rotated" in seen, (
            "Codex did not reload dynamic header helper",
            status,
        )
        print("Codex: rotated HTTP token and explicit stdio credential inheritance accepted")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
server.shutdown()
