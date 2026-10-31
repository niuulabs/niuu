"""Per-broker-start runtime directory and loopback secret.

The broker's loopback routes that act on behalf of the agent (``/api/present-file``,
``/api/message``, ``/api/claude/hooks`` and ``/api/forge-mcp/*``) require
``Authorization: Bearer <secret>``. The secret is random, regenerated every time
the broker starts, and written 0600 to ``<runtime dir>/forge-mcp.token``. Helpers
that run inside the session (the ``present-file`` shim, the ``skuld.forge_mcp``
stdio server) receive the *path* of that file — never the value in argv or env —
and read it per request.

Threat model: in local mode every session runs as the same Unix user, so this
cannot stop a deliberately malicious local process that reads the file. It does
stop other hosts on the network (the broker may bind 0.0.0.0), accidental
cross-session calls and a model spoofing broker endpoints from a tool.

The runtime directory also holds the engine configs Skuld generates for the
session (Claude MCP config, hook settings, the forge-notify skill plugin). It is
broker-owned and lives outside the workspace so nothing secret lands in a repo.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path

logger = logging.getLogger("skuld.broker")

TOKEN_FILE_NAME = "forge-mcp.token"
_BEARER_PREFIX = "Bearer "
_SECRET_BYTES = 32
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")


class RuntimeDirError(RuntimeError):
    """The runtime directory is not a private directory owned by this user."""


def _ensure_private_dir(path: Path) -> None:
    """Create ``path`` (one level) as 0700 and prove it is ours and not a symlink."""
    try:
        path.mkdir(mode=_PRIVATE_DIR_MODE)
    except FileExistsError:
        pass
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise RuntimeDirError(f"{path} must be a real directory, not a link or file")
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise RuntimeDirError(f"{path} is owned by uid {info.st_uid}, not this broker's user")
    if stat.S_IMODE(info.st_mode) != _PRIVATE_DIR_MODE:
        path.chmod(_PRIVATE_DIR_MODE)


def default_runtime_dir(session_id: str) -> Path:
    """``<tempdir>/skuld-<uid>/<session id>``: private, per session, outside the workspace."""
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    safe_session = _UNSAFE_NAME_CHARS.sub("_", session_id) or "session"
    return Path(tempfile.gettempdir()) / f"skuld-{uid}" / safe_session


def prepare_runtime_dir(configured: str, session_id: str) -> Path:
    """Create the runtime dir. A configured dir must already have a usable parent."""
    if configured:
        root = Path(configured).expanduser()
        _ensure_private_dir(root)
        return root
    root = default_runtime_dir(session_id)
    _ensure_private_dir(root.parent)
    _ensure_private_dir(root)
    return root


def write_private_file(path: Path, content: str) -> None:
    """Atomically write ``content`` to ``path`` with mode 0600."""
    temp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


class SessionRuntime:
    """The runtime directory plus the loopback secret for one broker start."""

    def __init__(self, root: Path, secret: str) -> None:
        self.root = root
        self._secret = secret

    @classmethod
    def create(cls, configured_dir: str, session_id: str) -> SessionRuntime:
        root = prepare_runtime_dir(configured_dir, session_id)
        secret = secrets.token_urlsafe(_SECRET_BYTES)
        write_private_file(root / TOKEN_FILE_NAME, secret)
        logger.info("Broker loopback secret written to %s", root / TOKEN_FILE_NAME)
        return cls(root, secret)

    @property
    def token_file(self) -> Path:
        return self.root / TOKEN_FILE_NAME

    @property
    def authorization(self) -> str:
        """The ``Authorization`` header value helpers must send."""
        return f"{_BEARER_PREFIX}{self._secret}"

    def accepts(self, authorization: str | None) -> bool:
        """Constant-time check of an ``Authorization`` header."""
        if not authorization or not authorization.startswith(_BEARER_PREFIX):
            return False
        presented = authorization[len(_BEARER_PREFIX) :].strip()
        return hmac.compare_digest(presented.encode(), self._secret.encode())

    def path(self, name: str) -> Path:
        """A file or directory name inside the runtime dir."""
        if "/" in name or name in {"", ".", ".."}:
            raise ValueError(f"invalid runtime file name {name!r}")
        return self.root / name

    def revoke(self) -> None:
        """Delete the token file at shutdown — only if it is still this start's secret.

        A replacement broker for the same session may already have written its own
        secret; that one must survive this broker's teardown.
        """
        try:
            current = self.token_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        if hmac.compare_digest(current.encode(), self._secret.encode()):
            self.token_file.unlink(missing_ok=True)
