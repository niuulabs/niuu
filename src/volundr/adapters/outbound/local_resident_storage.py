"""Host storage layout and log helpers shared by the local resident runtimes.

Both local resident adapters — Docker containers and host processes — keep a
resident's durable state under ``<residents_dir>/<resident-id>/sandbox`` with
the same layout, so switching the local runtime keeps the resident's workspace,
Ravn state and agent credentials.
"""

from __future__ import annotations

import re
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

from volundr.domain.models import ResidentLogEntry

# Mini mode authorizes every caller; residents still need a non-empty bearer
# value, because an empty ``Bearer `` header is rejected by HTTP clients.
LOCAL_PLATFORM_ACCESS_TOKEN = "local-mini"

# Container sandbox directories and their durable host subdirectories.
SANDBOX_DIRECTORIES: dict[str, tuple[str, ...]] = {
    "/sandbox/workspace": ("workspace",),
    "/sandbox/.volundr": ("config",),
    "/sandbox/.codex": ("home", ".codex"),
    "/sandbox/.claude": ("home", ".claude"),
}

LOG_LINE = re.compile(r"^(?P<timestamp>\S+)\s+(?:\[(?P<source>[^]]+)\]\s+)?(?P<message>.*)$")
LOG_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40, "critical": 50}


def host_runtime_path(root: Path, destination: str) -> Path:
    """Map a durable ``/sandbox`` path to its location under a resident root."""
    for prefix, parts in SANDBOX_DIRECTORIES.items():
        if destination == prefix or destination.startswith(f"{prefix}/"):
            return root.joinpath(*parts, destination.removeprefix(prefix).lstrip("/"))
    raise RuntimeError(f"Resident file path is not backed by durable local storage: {destination}")


def write_resident_files(root: Path, files: dict[str, bytes]) -> None:
    """Create the durable resident layout and write the materialized files."""
    for parts in SANDBOX_DIRECTORIES.values():
        root.joinpath(*parts).mkdir(parents=True, exist_ok=True)
    for destination, content in files.items():
        path = host_runtime_path(root, destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)


def materialize_agent_home(root: Path, host_home_dir: Path) -> None:
    """Copy the operator's Codex and Claude credentials into a resident home once."""
    candidates = {
        host_home_dir / ".codex" / "auth.json": root / "home" / ".codex" / "auth.json",
        host_home_dir / ".codex" / "config.toml": root / "home" / ".codex" / "config.toml",
        host_home_dir / ".claude" / ".credentials.json": root
        / "home"
        / ".claude"
        / ".credentials.json",
    }
    for source, destination in candidates.items():
        if not source.is_file() or destination.exists():
            continue
        shutil.copy2(source, destination)
        destination.chmod(0o600)


def service_ready(port: int) -> bool:
    """Return whether a loopback HTTP service answers on ``port``."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5) as connection:
            connection.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            return connection.recv(16).startswith(b"HTTP/")
    except OSError:
        return False


def parse_resident_logs(
    payload: str,
    sources: tuple[str, ...],
    min_level: str,
) -> list[ResidentLogEntry]:
    """Parse ``<timestamp> [<source>] <message>`` lines into normalized entries."""
    minimum = LOG_LEVELS.get(min_level.lower(), 0)
    entries: list[ResidentLogEntry] = []
    for line in payload.splitlines():
        match = LOG_LINE.match(line)
        if match is None:
            continue
        source = match.group("source") or "container"
        if sources and source not in sources:
            continue
        message = match.group("message")
        level = resident_log_level(message)
        if LOG_LEVELS.get(level, 20) < minimum:
            continue
        try:
            timestamp = datetime.fromisoformat(match.group("timestamp").replace("Z", "+00:00"))
        except ValueError:
            timestamp = datetime.now(UTC)
        entries.append(
            ResidentLogEntry(
                timestamp_ms=int(timestamp.timestamp() * 1000),
                level=level,
                source=source,
                target=source,
                message=message,
            )
        )
    return entries


def resident_log_level(message: str) -> str:
    """Infer a log level from free-form process output."""
    lowered = message.lower()
    if "critical" in lowered or "fatal" in lowered:
        return "critical"
    if "error" in lowered or "exception" in lowered:
        return "error"
    if "warning" in lowered or "warn" in lowered:
        return "warning"
    if "debug" in lowered:
        return "debug"
    return "info"
