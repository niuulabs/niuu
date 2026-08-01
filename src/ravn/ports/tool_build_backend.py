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


class ToolBuildInputRequiredError(ToolBuildError):
    """A commissioned build paused for a durable remote input.

    ``continuation`` is an opaque backend-owned envelope.  The build tool
    persists it and returns the peer's question or gate to the resident.  A
    later resident turn supplies the operator/model answer with the same
    envelope so the backend can resume the original remote task.
    """

    def __init__(
        self,
        *,
        task_id: str,
        input_kind: str,
        prompt: str,
        continuation: dict[str, Any],
    ) -> None:
        self.task_id = task_id
        self.input_kind = input_kind
        self.prompt = prompt
        self.continuation = continuation
        super().__init__(f"A2A task {task_id} requires {input_kind} input: {prompt}")


class ToolBuildPendingError(ToolBuildError):
    """A commissioned build is running and will resume after an A2A callback."""

    def __init__(
        self,
        *,
        task_id: str,
        continuation: dict[str, Any],
        push_registered: bool,
    ) -> None:
        self.task_id = task_id
        self.continuation = continuation
        self.push_registered = push_registered
        super().__init__(f"A2A task {task_id} is still running")


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
    #: Stable local operation identifier used to recover a lost remote response.
    operation_id: str = ""
    #: Opaque backend-owned state used to resume a durable remote build.
    continuation: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolBuildResult:
    """The produced artifact: a learned-tool manifest + its Python code.

    ``test_code`` and ``requirements`` capture the tests and dependencies the
    builder produced (empty when it produced none). ``build_evidence`` records
    what verification the builder reported and how the artifact was retrieved
    (e.g. ``{"retrieval": "canonical_file"}`` vs ``"chronicle_scrape"``).
    """

    manifest: dict[str, Any]
    tool_code: str
    provenance: dict[str, Any] = field(default_factory=dict)
    test_code: str = ""
    requirements: list[str] = field(default_factory=list)
    build_evidence: dict[str, Any] = field(default_factory=dict)


class ToolBuildBackend(ABC):
    """Produce a learned tool's manifest + code from a build request."""

    @property
    def supports_restart_recovery(self) -> bool:
        """Whether repeated builds with the same operation_id resume one task."""
        return False

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
