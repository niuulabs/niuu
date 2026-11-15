"""Service discovery for the nng Sleipnir transport.

Multiple ODIN processes on the same machine use a shared JSON registry file
to find each other's IPC socket addresses automatically.  No hardcoded paths,
no external service required — just a file protected by an advisory flock.

Registry file format::

    {
      "services": [
        {
          "id": "ravn:agent-abc",
          "pid": 12345,
          "socket": "ipc:///tmp/sleipnir-abc.sock",
          "started": "2026-04-05T12:00:00+00:00"
        }
      ],
      "primary_pub": "ipc:///tmp/sleipnir.sock"
    }

Locking
-------
All writes acquire an exclusive ``fcntl.LOCK_EX`` advisory lock on the
registry file.  Reads acquire a shared ``fcntl.LOCK_SH`` lock.  Locks are
released when the context exits.

Stale-entry cleanup
-------------------
On every access, PIDs that are no longer running are pruned.  A process is
considered dead if ``os.kill(pid, 0)`` raises ``ProcessLookupError``.
``PermissionError`` means the process exists but is owned by another user —
treated as alive.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_REGISTRY_PATH = Path.home() / ".odin" / "sleipnir.json"

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceEntry:
    """A single registered service instance."""

    id: str
    pid: int
    socket: str
    started: str  # ISO 8601 UTC
    #: What the service says about itself (a mesh peer's identity); empty for a plain socket.
    info: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_alive(pid: int) -> bool:
    """Return ``True`` if *pid* is a running process on this machine."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to a different user — still alive.
        return True


class _LockedFile:
    """Open a file and hold an advisory ``flock`` for the duration.

    - ``write=False`` → ``LOCK_SH`` (shared read lock), opened ``"rb"``.
    - ``write=True`` → ``LOCK_EX`` (exclusive write lock), created if absent
      via ``O_CREAT | O_RDWR`` so create-or-open is atomic.
    """

    def __init__(self, path: Path, *, write: bool) -> None:
        self._path = path
        self._write = write
        self._fh = None

    def __enter__(self):
        if self._write:
            fd = os.open(str(self._path), os.O_RDWR | os.O_CREAT, 0o600)
            self._fh = os.fdopen(fd, "r+b")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
        else:
            self._fh = open(self._path, "rb")
            fcntl.flock(self._fh, fcntl.LOCK_SH)
        return self._fh

    def __exit__(self, *_: object) -> None:
        if self._fh is not None:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


# ---------------------------------------------------------------------------
# ServiceRegistry
# ---------------------------------------------------------------------------


class ServiceRegistry:
    """JSON-backed service registry with file-level advisory locking.

    Each service that wants to be discoverable calls :meth:`register` on
    startup and :meth:`deregister` on clean shutdown.  Subscribers call
    :meth:`list_services` to obtain the current set of live publisher sockets.

    Usage::

        registry = ServiceRegistry(Path("/run/odin/sleipnir.json"))
        registry.register("ravn:agent-abc", "ipc:///tmp/sleipnir-abc.sock")
        entries = registry.list_services()
        registry.deregister("ravn:agent-abc")
    """

    def __init__(
        self,
        path: Path = DEFAULT_REGISTRY_PATH,
        primary_pub: str = "",
    ) -> None:
        self._path = path
        self._primary_pub = primary_pub

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, service_id: str, socket: str, info: dict[str, Any] | None = None) -> None:
        """Register *service_id* at *socket* in the registry, with what it says about itself.

        Stale entries (dead PIDs) are removed first.  If *service_id* is
        already present (restart scenario), the old entry is replaced.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _LockedFile(self._path, write=True) as f:
            data = self._read(f)
            live = [s for s in data["services"] if _is_alive(s["pid"])]
            live = [s for s in live if s["id"] != service_id]
            live.append(
                {
                    "id": service_id,
                    "pid": os.getpid(),
                    "socket": socket,
                    "started": datetime.now(UTC).isoformat(),
                    "info": dict(info or {}),
                }
            )
            data["services"] = live
            self._write(f, data)
        logger.debug("ServiceRegistry: registered %s at %s", service_id, socket)

    def deregister(self, service_id: str) -> None:
        """Remove *service_id* from the registry.

        No-op if the file does not exist or the entry is already absent.
        """
        if not self._path.exists():
            return
        with _LockedFile(self._path, write=True) as f:
            data = self._read(f)
            before = len(data["services"])
            data["services"] = [s for s in data["services"] if s["id"] != service_id]
            if len(data["services"]) < before:
                self._write(f, data)
        logger.debug("ServiceRegistry: deregistered %s", service_id)

    def list_services(self) -> list[ServiceEntry]:
        """Return all live (non-stale) service entries.

        Returns an empty list if the registry file does not exist.
        """
        if not self._path.exists():
            return []
        with _LockedFile(self._path, write=False) as f:
            data = self._read(f)
        return [
            ServiceEntry(
                id=s["id"],
                pid=s["pid"],
                socket=s["socket"],
                started=s["started"],
                info=dict(s.get("info") or {}),
            )
            for s in data["services"]
            if _is_alive(s["pid"])
        ]

    def cleanup_stale(self) -> int:
        """Remove entries whose PID is no longer running.

        Returns the number of entries removed.
        """
        if not self._path.exists():
            return 0
        with _LockedFile(self._path, write=True) as f:
            data = self._read(f)
            before = len(data["services"])
            data["services"] = [s for s in data["services"] if _is_alive(s["pid"])]
            removed = before - len(data["services"])
            if removed:
                self._write(f, data)
        if removed:
            logger.info("ServiceRegistry: removed %d stale entries", removed)
        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self, f) -> dict:
        """Parse JSON from an open, locked file handle (seeking to start)."""
        f.seek(0)
        content = f.read()
        if not content.strip():
            return {"services": [], "primary_pub": self._primary_pub}
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("ServiceRegistry: corrupt registry at %s; resetting", self._path)
            return {"services": [], "primary_pub": self._primary_pub}
        data.setdefault("services", [])
        data.setdefault("primary_pub", self._primary_pub)
        return data

    def _write(self, f, data: dict) -> None:
        """Serialise *data* to JSON and write to an open, locked file handle."""
        f.seek(0)
        f.truncate()
        f.write(json.dumps(data, indent=2).encode())
        f.flush()
