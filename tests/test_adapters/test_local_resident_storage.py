"""Host storage layout and log helpers shared by the local resident runtimes."""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from volundr.adapters.outbound.local_resident_storage import (
    host_runtime_path,
    materialize_agent_home,
    parse_resident_logs,
    resident_log_level,
    service_ready,
    write_resident_files,
)


def test_sandbox_paths_map_onto_the_durable_resident_layout(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"

    assert host_runtime_path(root, "/sandbox/workspace/project/file.txt") == (
        root / "workspace" / "project" / "file.txt"
    )
    assert host_runtime_path(root, "/sandbox/workspace") == root / "workspace"
    assert host_runtime_path(root, "/sandbox/.volundr/skuld.yaml") == root / "config" / "skuld.yaml"
    assert host_runtime_path(root, "/sandbox/.codex") == root / "home" / ".codex"
    with pytest.raises(RuntimeError, match="not backed"):
        host_runtime_path(root, "/tmp/file")
    with pytest.raises(RuntimeError, match="not backed"):
        host_runtime_path(root, "/sandbox/workspaces/file")


def test_resident_files_are_written_privately(tmp_path: Path) -> None:
    root = tmp_path / "sandbox"

    write_resident_files(root, {"/sandbox/.volundr/ravn.yaml": b"persona: steward\n"})

    written = root / "config" / "ravn.yaml"
    assert written.read_bytes() == b"persona: steward\n"
    assert written.stat().st_mode & 0o777 == 0o600
    assert (root / "workspace").is_dir()
    assert (root / "home" / ".claude").is_dir()


def test_agent_credentials_are_copied_once(tmp_path: Path) -> None:
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text("operator", encoding="utf-8")
    root = tmp_path / "sandbox"
    write_resident_files(root, {})

    materialize_agent_home(root, home)
    copied = root / "home" / ".codex" / "auth.json"
    assert copied.read_text() == "operator"
    assert copied.stat().st_mode & 0o777 == 0o600

    copied.write_text("refreshed by the resident", encoding="utf-8")
    materialize_agent_home(root, home)
    assert copied.read_text() == "refreshed by the resident"
    assert not (root / "home" / ".claude" / ".credentials.json").exists()


def test_service_ready_requires_an_http_answer() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert service_ready(server.server_address[1]) is True
    finally:
        server.shutdown()
        server.server_close()

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    assert service_ready(closed_port) is False


def test_logs_are_parsed_filtered_and_levelled() -> None:
    entries = parse_resident_logs(
        "\n".join(
            [
                "",
                "invalid-time [api] error details",
                "2026-07-12T12:00:00Z [api] WARNING retrying",
                "2026-07-12T12:00:00Z [api] ready",
                "2026-07-12T12:00:01Z [api] fatal failure",
                "2026-07-12T12:00:02Z [other] error ignored",
            ]
        ),
        ("api",),
        "warning",
    )

    assert [(entry.level, entry.message) for entry in entries] == [
        ("error", "error details"),
        ("warning", "WARNING retrying"),
        ("critical", "fatal failure"),
    ]
    assert parse_resident_logs("2026-07-12T12:00:00Z plain", (), "")[0].source == "container"
    assert resident_log_level("exception raised") == "error"
    assert resident_log_level("warn soon") == "warning"
    assert resident_log_level("debug details") == "debug"
    assert resident_log_level("ready") == "info"
