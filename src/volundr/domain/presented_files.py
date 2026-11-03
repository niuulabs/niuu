"""Durable presented files (control-plane F2).

``present-file`` stages a copy of a file next to the session's broker. That copy is
served only while the broker runs, so a link to it dies with the session. Forge keeps
its own durable copy of every presented file, keyed by ``(session_id, file_id)``, and
serves it whether or not the session is still running.

Each copy has an absolute URL on the host's tailnet name (``Artifact.url``: http(s),
a ``*.ts.net`` MagicDNS name or a Tailscale address, never ``*.xteo.mesh`` or anything
public), so a phone on the tailnet opens it in Safari and nothing else can.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from uuid import UUID

from niuu.domain.presented_files import PRESENTED_FILE_ID_PATTERN

_PRESENTED_FILE_ID = re.compile(PRESENTED_FILE_ID_PATTERN)
#: Durable presented files are user content: their URLs may only name a Tailscale host,
#: so a link never resolves to anything public.
_TAILNET_NAME_SUFFIX = ".ts.net"
_TAILNET_NETWORKS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


class PresentedFileConflictError(ValueError):
    """A different file was already stored under this ``(session_id, file_id)``."""


class PresentedFileQuotaError(Exception):
    """Storing this copy would take the host past ``presented_files.max_total_bytes``."""

    def __init__(self, *, size: int, used: int, limit: int) -> None:
        self.size, self.used, self.limit = size, used, limit
        super().__init__(
            f"Forge refused to keep this file: {size} bytes would take presented files to "
            f"{used + size} of {limit} bytes (presented_files.max_total_bytes). Raise "
            "presented_files.max_total_bytes, or delete sessions with presented files."
        )


class PresentedFileQuota:
    """The host's total budget for durable copies (LEX-1514).

    ``admit`` and the store write it guards run under one lock, so two uploads cannot
    both fit into the last free bytes. A refusal raises and is counted; nothing is ever
    evicted to make room: copies go only with their session's delete.
    """

    def __init__(self, max_total_bytes: int) -> None:
        if max_total_bytes <= 0:
            raise ValueError("presented_files.max_total_bytes must be positive")
        self.max_total_bytes = max_total_bytes
        self.refusals = 0
        self.lock = asyncio.Lock()

    async def admit(
        self, store: PresentedFileStore, session_id: UUID, file_id: str, size: int
    ) -> None:
        """Raise :class:`PresentedFileQuotaError` when this new copy does not fit.

        Call with :attr:`lock` held, and keep it held through the write. Re-storing an
        id that is already stored adds nothing, so it is always admitted.
        """
        if await store.get(session_id, file_id) is not None:
            return
        used = await store.total_bytes()
        if used + size <= self.max_total_bytes:
            return
        self.refusals += 1
        raise PresentedFileQuotaError(size=size, used=used, limit=self.max_total_bytes)

    async def usage(self, store: PresentedFileStore) -> dict[str, int]:
        return {
            "used_bytes": await store.total_bytes(),
            "max_total_bytes": self.max_total_bytes,
            "quota_refusals": self.refusals,
        }


def is_presented_file_id(value: str) -> bool:
    return bool(_PRESENTED_FILE_ID.fullmatch(value or ""))


@dataclass(frozen=True)
class PresentedFile:
    """Metadata of one durable presented file."""

    session_id: UUID
    file_id: str
    name: str
    mime: str
    size: int
    sha256: str
    stored_at: datetime


class PresentedFileStore(ABC):
    """Durable storage for presented files."""

    @abstractmethod
    async def put(
        self, session_id: UUID, file_id: str, *, name: str, mime: str, data: bytes
    ) -> PresentedFile:
        """Store a file once. Re-storing identical bytes returns the stored record;
        different bytes under the same id raise :class:`PresentedFileConflictError`."""

    @abstractmethod
    async def get(self, session_id: UUID, file_id: str) -> tuple[PresentedFile, Path] | None:
        """The stored record and the path of its bytes, or ``None`` when absent."""

    @abstractmethod
    async def delete_session(self, session_id: UUID) -> int:
        """Remove every file stored for a session; return how many were removed."""

    @abstractmethod
    async def total_bytes(self) -> int:
        """Bytes of every copy currently stored (the quota's usage)."""


def is_tailnet_host(host: str) -> bool:
    """A ``*.ts.net`` MagicDNS name, or an address in Tailscale's ranges."""
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(_TAILNET_NAME_SUFFIX) and len(host) > len(_TAILNET_NAME_SUFFIX)
    return any(address in network for network in _TAILNET_NETWORKS)


def validate_public_base_url(value: str) -> str:
    """Normalize the configured base URL; empty means "no absolute URL"."""
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("presented_files.public_base_url must be an http(s) URL with a host")
    if not is_tailnet_host(parts.hostname):
        raise ValueError(
            "presented_files.public_base_url must name this host on the tailnet (a *.ts.net "
            "MagicDNS name or a 100.64.0.0/10 / fd7a:115c:a1e0::/48 address), never a public "
            f"or *.xteo.mesh name; got {parts.hostname!r}"
        )
    if parts.query or parts.fragment:
        raise ValueError("presented_files.public_base_url must not carry a query or fragment")
    return value


def presented_file_path(session_id: UUID, file_id: str, *, prefix: str = "/api/v1/forge") -> str:
    """The Forge route that serves a presented file (relative)."""
    return f"{prefix}/sessions/{session_id}/files/presented/{quote(file_id, safe='')}"


def durable_file_url(
    public_base_url: str, session_id: UUID, file_id: str, *, prefix: str = "/api/v1/forge"
) -> str | None:
    """The absolute, phone-reachable URL of a presented file, or ``None`` when no base
    URL is configured (callers then keep the relative route)."""
    base = validate_public_base_url(public_base_url)
    if not base:
        return None
    return base + presented_file_path(session_id, file_id, prefix=prefix)
