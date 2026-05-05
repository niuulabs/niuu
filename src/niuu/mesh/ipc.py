"""Helpers for local IPC-based mesh sockets."""

from __future__ import annotations

import hashlib
from pathlib import Path

_SOCKET_ROOT_NAME = "niuu-mesh"


def _socket_root_dir() -> Path:
    """Return the shared root for short local Unix socket paths."""
    short_tmp = Path("/tmp")
    if short_tmp.exists():
        return short_tmp / _SOCKET_ROOT_NAME
    return Path.cwd() / _SOCKET_ROOT_NAME


def _socket_namespace(flock_dir: Path) -> str:
    """Return a short deterministic namespace for *flock_dir*."""
    digest = hashlib.sha1(str(flock_dir.resolve()).encode("utf-8")).hexdigest()
    return digest[:12]


def ipc_address(path: Path) -> str:
    """Return an ``ipc://`` URI for *path*."""
    return f"ipc://{path}"


def ipc_path(address: str) -> Path | None:
    """Return the filesystem path for an ``ipc://`` URI."""
    if not address.startswith("ipc://"):
        return None
    return Path(address.removeprefix("ipc://")).expanduser()


def flock_socket_dir(flock_dir: Path) -> Path:
    """Return the per-flock directory used for Unix socket files."""
    return _socket_root_dir() / _socket_namespace(flock_dir)


def ravn_mesh_addresses(flock_dir: Path, persona: str) -> tuple[str, str]:
    """Return ``(pub_address, rep_address)`` IPC URIs for one Ravn persona."""
    sock_dir = flock_socket_dir(flock_dir)
    return (
        ipc_address(sock_dir / f"{persona}-pub.sock"),
        ipc_address(sock_dir / f"{persona}-rep.sock"),
    )


def skuld_mesh_addresses(flock_dir: Path) -> tuple[str, str]:
    """Return ``(pub_address, rep_address)`` IPC URIs for the local Skuld peer."""
    sock_dir = flock_socket_dir(flock_dir)
    return (
        ipc_address(sock_dir / "skuld-pub.sock"),
        ipc_address(sock_dir / "skuld-rep.sock"),
    )


def cleanup_ipc_socket_dir(flock_dir: Path) -> None:
    """Remove any stale ``.sock`` files from the flock socket directory."""
    sock_dir = flock_socket_dir(flock_dir)
    if not sock_dir.exists():
        return
    for sock in sock_dir.glob("*.sock"):
        sock.unlink(missing_ok=True)


def cleanup_ravn_mesh_sockets(flock_dir: Path, personas: list[str]) -> None:
    """Remove stale Ravn persona socket files for *personas*."""
    for persona in personas:
        pub_address, rep_address = ravn_mesh_addresses(flock_dir, persona)
        for address in (pub_address, rep_address):
            if path := ipc_path(address):
                path.unlink(missing_ok=True)


def cleanup_skuld_mesh_sockets(flock_dir: Path) -> None:
    """Remove stale Skuld mesh socket files."""
    for address in skuld_mesh_addresses(flock_dir):
        if path := ipc_path(address):
            path.unlink(missing_ok=True)
