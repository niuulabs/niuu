"""Programs this session's MCP servers run, installed before the engine starts.

A server may name a ``program`` (``niuu.domain.session_program``) instead of a ``command``.
Skuld fetches this platform's archive, checks its digest, unpacks it once and puts the
executable's path in the server's ``command``. It is the same on every placement, because
Skuld is what every placement runs. Anything that fails stops the session from starting.
"""

from __future__ import annotations

import hashlib
import logging
import os
import platform
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from niuu.domain.session_program import (
    ProgramArchive,
    ProgramError,
    SessionProgram,
    platform_key,
)

logger = logging.getLogger("skuld.broker")

PROGRAM_KEY = "program"
_READ_BYTES = 1024 * 1024
_EXECUTE_BITS = 0o755


class ProgramInstaller:
    """Installs pinned programs below *cache_dir*, one directory per archive digest."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        connect_timeout_s: float,
        read_timeout_s: float,
        host_platform: str = "",
    ) -> None:
        self._cache_dir = cache_dir
        self._timeout = httpx.Timeout(read_timeout_s, connect=connect_timeout_s)
        self._platform = host_platform or platform_key(platform.system(), platform.machine())

    def resolve(self, servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """*servers* with every ``program`` installed and replaced by its ``command``."""
        return [self._resolve(server) if PROGRAM_KEY in server else server for server in servers]

    def _resolve(self, server: dict[str, Any]) -> dict[str, Any]:
        name = str(server.get("name") or "").strip()
        if server.get("command") or server.get("url"):
            raise ProgramError(
                f"MCP server {name!r} names both a program and a command or url; keep one"
            )
        try:
            program = SessionProgram.model_validate(server[PROGRAM_KEY])
        except ValidationError as exc:
            raise ProgramError(f"MCP server {name!r} has an invalid program: {exc}") from exc
        resolved = {key: value for key, value in server.items() if key != PROGRAM_KEY}
        return {**resolved, "type": "stdio", "command": str(self.install(name, program))}

    def install(self, name: str, program: SessionProgram) -> Path:
        """The executable of *program* on this platform, installed if it is not yet."""
        archive = program.platforms.get(self._platform)
        if archive is None:
            raise ProgramError(
                f"{name} {program.version} has no build for {self._platform}, where this session "
                f"runs (it has {', '.join(sorted(program.platforms))}). Add that platform to its "
                "mcp_servers entry, or place the session on a host that has one"
            )
        home = self._cache_dir / archive.sha256
        command = home / archive.command
        if command.is_file():
            return command
        try:
            self._fetch(name, program, archive, home)
        except (OSError, httpx.HTTPError, tarfile.TarError, zipfile.BadZipFile) as exc:
            raise ProgramError(
                f"{name} {program.version} could not be installed from {archive.url} into "
                f"{self._cache_dir}: {type(exc).__name__}: {exc}"
            ) from exc
        return command

    def _fetch(
        self, name: str, program: SessionProgram, archive: ProgramArchive, home: Path
    ) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        # Beside its final place, so that the rename below is atomic: sessions that share a
        # home may install the same program at once, and none may see half of it.
        staging = Path(tempfile.mkdtemp(dir=self._cache_dir, prefix=".install-"))
        try:
            download = staging / "archive"
            self._download(name, program, archive, download)
            content = staging / "content"
            _unpack(download, content)
            executable = content / archive.command
            if not executable.is_file() or not executable.resolve().is_relative_to(
                content.resolve()
            ):
                raise ProgramError(
                    f"{name} {program.version} has no file {archive.command!r} in its archive; "
                    "correct `command` in its mcp_servers entry"
                )
            executable.chmod(_EXECUTE_BITS)
            try:
                os.rename(content, home)
            except OSError:
                if not (home / archive.command).is_file():
                    raise
            logger.info("Installed %s %s for %s", name, program.version, self._platform)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def _download(
        self, name: str, program: SessionProgram, archive: ProgramArchive, target: Path
    ) -> None:
        digest = hashlib.sha256()
        with (
            httpx.Client(timeout=self._timeout, follow_redirects=True) as client,
            client.stream("GET", archive.url) as response,
            target.open("wb") as out,
        ):
            response.raise_for_status()
            for chunk in response.iter_bytes(_READ_BYTES):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != archive.sha256:
            raise ProgramError(
                f"{name} {program.version} from {archive.url} has SHA-256 {digest.hexdigest()}, "
                f"not the pinned {archive.sha256}; it was not installed"
            )


def _unpack(download: Path, content: Path) -> None:
    if tarfile.is_tarfile(download):
        with tarfile.open(download) as tar:
            tar.extractall(content, filter="data")
        return
    if zipfile.is_zipfile(download):
        with zipfile.ZipFile(download) as bundle:
            bundle.extractall(content)
        return
    raise tarfile.ReadError("not a tar or zip archive")
