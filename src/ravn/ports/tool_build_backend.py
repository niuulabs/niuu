"""Port for commissioning a learned-tool build.

The investigating agent decides *what* tool it needs; a build backend decides
*how* the code gets produced — written inline by the agent, or commissioned
from a real dev build (a Volundr Forge session, or a Ting workflow that itself
spawns Forge sessions). Whatever the backend, the result is one
``ToolBuildResult`` that flows through the single review → canary → install →
register → flock path, so a Forge-built tool is gated exactly like an inline
one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ToolBuildError(RuntimeError):
    """A commissioned build failed, timed out, or returned an unusable result."""


@dataclass(frozen=True)
class ToolBuildRequest:
    """What a resident wants built, handed to a build backend."""

    name: str
    description: str
    #: Natural-language spec of the instrument to build (the agent's brief).
    build_request: str
    input_schema: dict[str, Any]
    required_permission: str
    declared_reach: list[dict[str, Any]] = field(default_factory=list)
    entry_point: str = "run"
    environment_id: str = ""
    valkyrie_id: str = ""
    domain: str = ""
    #: The signal/investigation context that motivated the tool.
    signal_context: str = ""


@dataclass(frozen=True)
class ToolBuildResult:
    """The produced artifact: a learned-tool manifest + its Python code."""

    manifest: dict[str, Any]
    tool_code: str
    provenance: dict[str, Any] = field(default_factory=dict)


class ToolBuildBackend(ABC):
    """Produce a learned tool's manifest + code from a build request."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable backend identifier recorded in provenance."""

    @abstractmethod
    async def build(self, request: ToolBuildRequest) -> ToolBuildResult:
        """Commission the build and return the produced artifact.

        Raises :class:`ToolBuildError` on failure — there is no silent
        fallback to a stub tool.
        """
