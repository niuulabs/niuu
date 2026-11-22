"""A program a session's tool server runs, pinned by digest and fetched when it starts.

An MCP server is often a program no session image carries. The operator's catalogue pins it
once, per platform, and every session that asks for the server gets exactly that build,
wherever it runs: in a pod, on a VM, on a registered host or on a laptop.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Where a session keeps the programs it installed, in its home. It is a cache: every entry
#: is fetched again by digest, so session archives leave it out.
PROGRAM_CACHE = ".cache/niuu/programs"

PLATFORM_PATTERN = re.compile(r"(linux|darwin)-(x86_64|arm64)")

#: What the operating system calls an architecture, and what a platform key calls it.
_ARCHITECTURES = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}


class ProgramError(RuntimeError):
    """A session's program cannot be provided; the message says why and what to change."""


def platform_key(system: str, machine: str) -> str:
    """The platform key of a host, for example ``linux-x86_64``."""
    return f"{system.lower()}-{_ARCHITECTURES.get(machine.lower(), machine.lower())}"


class ProgramArchive(BaseModel):
    """One platform's build: a tar or zip archive, its digest and the executable in it."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        pattern=r"^https://", description="Where the archive (tar, tar.gz/xz/bz2 or zip) is."
    )
    sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$", description="SHA-256 of the archive; any other is refused."
    )
    command: str = Field(min_length=1, description="Path of the executable inside the archive.")

    @field_validator("command")
    @classmethod
    def _inside_the_archive(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"command {value!r} must be a path inside the archive")
        return value


class SessionProgram(BaseModel):
    """A pinned program: its version and one archive per platform it runs on."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, description="Version, as people refer to it.")
    platforms: dict[str, ProgramArchive] = Field(
        min_length=1,
        description="Archive per platform: linux-x86_64, linux-arm64, darwin-x86_64, darwin-arm64.",
    )

    @field_validator("platforms")
    @classmethod
    def _known_platforms(cls, value: dict[str, ProgramArchive]) -> dict[str, ProgramArchive]:
        unknown = sorted(key for key in value if not PLATFORM_PATTERN.fullmatch(key))
        if unknown:
            raise ValueError(
                f"unknown platforms {', '.join(unknown)}; use (linux|darwin)-(x86_64|arm64)"
            )
        return value
