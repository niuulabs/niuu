"""The built-in MCP servers over stdio, forwarding every tool to the session's broker.

An agent CLI (Claude Code, Codex, Grok) spawns this process for a built-in MCP server:
``forge`` (the default) or, with ``--server memory``, ``memory``. It speaks MCP
JSON-RPC 2.0 over newline-delimited stdio and forwards ``tools/list`` / ``tools/call``
to the broker's loopback routes (``/api/forge-mcp/tools`` or ``/api/memory-mcp/tools``),
authenticating with the broker's per-start secret read from ``--token-file``. It holds
no Forge or gateway credential of its own. A :class:`ServerProfile` carries what differs
between the servers (name, route, where the ``initialize`` instructions come from).

Why hand-rolled rather than the ``mcp`` SDK: this process starts once per agent
CLI (and per restart), so it must come up fast and must never write anything to
stdout except protocol frames. The protocol surface it needs — ``initialize``
with version negotiation, ``ping``, ``tools/list``, ``tools/call`` and a couple
of notifications — is small and stable, so a standard-library implementation
avoids importing anyio/pydantic/starlette on every spawn, adds no direct
dependency (``mcp`` is only transitive today) and behaves identically in the
frozen ``niuu`` binary. ``src/mimir/mcp.py`` is the repo precedent.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import IO, Any

from niuu.question_standard import QUESTION_STANDARD

logger = logging.getLogger("skuld.forge_mcp.stdio")

# Protocol constants (MCP revisions this server implements, JSON-RPC error codes).
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
JSONRPC_VERSION = "2.0"
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

TOOLS_PATH = "/api/forge-mcp/tools"
MEMORY_TOOLS_PATH = "/api/memory-mcp/tools"
SERVER_NAME = "forge"
SERVER_VERSION = "1.0.0"
SERVER_INSTRUCTIONS = (
    "Forge tools for this coding session: `notify` the user only when they would want "
    "to be interrupted (task done, you need them, or a failure stops the work; never for "
    "turn ends or progress), `environment` to learn where you run, and read tools to "
    "inspect other sessions. Tools that act on other sessions need an operator grant. "
    "Keep your session's `short_description` current with `describe_session`: set it "
    "when you start, whenever the phase changes, when blocked ('Blocked on …') and when "
    "done ('Done: …'); put the fuller status someone opening the session would want in "
    "`description`. Questions follow this standard: " + QUESTION_STANDARD
)
_ERROR_BODY_CHARS = 500


@dataclass(frozen=True)
class ServerProfile:
    """What differs between the built-in servers this process can be."""

    name: str
    title: str
    tools_path: str
    #: Fixed ``initialize`` instructions; ``None`` takes them from the broker's tool
    #: listing (the memory gateway owns its own), and a listing without them fails
    #: ``initialize`` rather than serving a server with no instructions.
    instructions: str | None
    #: What a failed broker call is called in the tool error the model reads.
    broker_label: str
    thread_prefix: str


FORGE_PROFILE = ServerProfile(
    name=SERVER_NAME,
    title="Forge",
    tools_path=TOOLS_PATH,
    instructions=SERVER_INSTRUCTIONS,
    broker_label="Forge broker",
    thread_prefix="forge-mcp",
)
MEMORY_PROFILE = ServerProfile(
    name="memory",
    title="Memory",
    tools_path=MEMORY_TOOLS_PATH,
    instructions=None,
    broker_label="memory broker",
    thread_prefix="memory-mcp",
)
PROFILES = {profile.name: profile for profile in (FORGE_PROFILE, MEMORY_PROFILE)}


class BrokerError(Exception):
    """The broker could not be reached or refused the call."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UnknownToolError(BrokerError):
    """The broker does not know the tool."""


class BrokerClient:
    """Calls the broker's loopback MCP routes for one built-in server."""

    def __init__(
        self,
        base_url: str,
        token_file: str,
        timeout_s: float,
        *,
        tools_path: str = TOOLS_PATH,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token_file = token_file
        self._timeout_s = timeout_s
        self._tools_path = tools_path

    def _authorization(self) -> str:
        # Re-read per call: the file is tiny and always holds the live broker's secret.
        try:
            with open(self._token_file, encoding="utf-8") as handle:
                return "Bearer " + handle.read().strip()
        except OSError as exc:
            raise BrokerError(f"cannot read the broker token file: {exc}") from exc

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self._base_url + path,
            data=data,
            method=method,
            headers={
                "authorization": self._authorization(),
                "content-type": "application/json",
                "accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:_ERROR_BODY_CHARS]
            if exc.code == 404 and method == "POST":
                raise UnknownToolError(detail, status=exc.code) from exc
            raise BrokerError(f"broker answered {exc.code}: {detail}", status=exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerError(f"broker unreachable: {exc}") from exc
        except ValueError as exc:
            raise BrokerError(f"broker sent invalid JSON: {exc}") from exc

    def tool_listing(self) -> dict[str, Any]:
        body = self._request("GET", self._tools_path)
        if not isinstance(body, dict) or not isinstance(body.get("tools"), list):
            raise BrokerError("broker tool list is malformed")
        return body

    def list_tools(self) -> list[dict[str, Any]]:
        return self.tool_listing()["tools"]

    def instructions(self) -> str:
        """The instructions the broker's listing carries (the memory gateway's)."""
        text = self.tool_listing().get("instructions")
        if not isinstance(text, str) or not text.strip():
            raise BrokerError("the broker's tool listing carries no instructions")
        return text

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        body = self._request(
            "POST", f"{self._tools_path}/{urllib.parse.quote(name, safe='')}", arguments
        )
        if not isinstance(body, dict):
            raise BrokerError("broker tool result is malformed")
        return body


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def negotiate_protocol_version(requested: object) -> str:
    """Echo a version we implement, otherwise offer our latest (the client decides)."""
    if isinstance(requested, str) and requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return LATEST_PROTOCOL_VERSION


class StdioServer:
    """Newline-delimited JSON-RPC over a pair of byte streams."""

    def __init__(
        self,
        client: BrokerClient,
        *,
        stdin: IO[bytes],
        stdout: IO[bytes],
        max_concurrency: int,
        profile: ServerProfile = FORGE_PROFILE,
    ) -> None:
        self._client = client
        self._profile = profile
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency, thread_name_prefix=profile.thread_prefix
        )

    # -- output

    def _write(self, message: dict[str, Any] | list[dict[str, Any]]) -> None:
        frame = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._write_lock:
            self._stdout.write(frame.encode("utf-8"))
            self._stdout.flush()

    @staticmethod
    def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "error": {"code": code, "message": message},
        }

    # -- dispatch

    def _handle(self, message: object) -> dict[str, Any] | None:
        """Handle one request or notification; ``None`` for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC_VERSION:
            return self._error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message and ("result" in message or "error" in message):
            return None  # a response; this server never sends requests
        method = message.get("method")
        is_request = "id" in message
        request_id = message.get("id")
        if not isinstance(method, str):
            return self._error(request_id, INVALID_REQUEST, "method must be a string")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            return self._error(request_id, INVALID_PARAMS, "params must be an object")
        if not is_request:
            return None  # notifications/initialized, notifications/cancelled, …
        try:
            return self._result(request_id, self._dispatch(method, params))
        except JsonRpcError as exc:
            return self._error(request_id, exc.code, exc.message)
        except BrokerError as exc:
            return self._error(request_id, INTERNAL_ERROR, str(exc))

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            profile = self._profile
            return {
                "protocolVersion": negotiate_protocol_version(params.get("protocolVersion")),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": profile.name,
                    "title": profile.title,
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    profile.instructions
                    if profile.instructions is not None
                    else self._client.instructions()
                ),
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self._client.list_tools()}
        if method == "tools/call":
            return self._call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"method not found: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            raise JsonRpcError(INVALID_PARAMS, "tools/call needs a tool name")
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "tool arguments must be an object")
        try:
            return self._client.call_tool(name, arguments)
        except UnknownToolError:
            raise JsonRpcError(INVALID_PARAMS, f"unknown tool: {name}") from None
        except BrokerError as exc:
            # A failed call is a tool result the model can read, not a protocol failure.
            text = f"The {self._profile.broker_label} call failed: {exc}"
            return {"content": [{"type": "text", "text": text}], "isError": True}

    def _respond(self, message: object) -> None:
        response = self._handle(message)
        if response is not None:
            self._write(response)

    def _process_line(self, line: bytes) -> None:
        text = line.decode("utf-8", errors="replace").strip()
        if not text:
            return
        try:
            message = json.loads(text)
        except ValueError:
            self._write(self._error(None, PARSE_ERROR, "invalid JSON"))
            return
        if isinstance(message, list):
            # JSON-RPC batch (MCP 2025-03-26): answer as one array, in order.
            responses = [response for item in message if (response := self._handle(item))]
            if responses:
                self._write(responses)
            return
        if isinstance(message, dict) and message.get("method") == "tools/call":
            # Tool calls may wait on Forge; keep ping and list responsive meanwhile.
            self._executor.submit(self._respond, message)
            return
        self._respond(message)

    def serve(self) -> None:
        try:
            for line in self._stdin:
                self._process_line(line)
        finally:
            self._executor.shutdown(wait=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skuld.forge_mcp", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--server",
        choices=sorted(PROFILES),
        default=FORGE_PROFILE.name,
        help="which built-in MCP server to be (the broker passes it for 'memory')",
    )
    parser.add_argument("--broker-url", required=True, help="http://127.0.0.1:<broker port>")
    parser.add_argument("--token-file", required=True, help="the broker's 0600 loopback token")
    # No defaults: the broker passes its configured values
    # (forge_mcp.* or memory.* broker_request_timeout_s / stdio_max_concurrency).
    parser.add_argument("--timeout", type=float, required=True, help="seconds per broker call")
    parser.add_argument(
        "--max-concurrency", type=int, required=True, help="tool calls served in parallel"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # stdout carries protocol frames only; diagnostics go to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    profile = PROFILES[args.server]
    server = StdioServer(
        BrokerClient(args.broker_url, args.token_file, args.timeout, tools_path=profile.tools_path),
        stdin=sys.stdin.buffer,
        stdout=sys.stdout.buffer,
        max_concurrency=args.max_concurrency,
        profile=profile,
    )
    server.serve()
    return 0
