"""Tool port — interface for agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.models import ToolResult


class ToolPort(ABC):
    """Abstract interface for a tool the agent can call."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name used by the LLM."""
        raise NotImplementedError

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what the tool does."""
        raise NotImplementedError

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """JSON Schema for the tool's input parameters."""
        raise NotImplementedError

    @property
    @abstractmethod
    def required_permission(self) -> str:
        """Permission string that must be granted before this tool executes."""
        raise NotImplementedError

    @property
    def parallelisable(self) -> bool:
        """Whether this tool can run concurrently with other tools in the same batch.

        Defaults to True. Override to False for stateful tools where ordering
        matters (e.g. git_commit, git_checkout, todo_write).
        """
        return True

    @abstractmethod
    async def execute(self, input: dict) -> ToolResult:
        """Execute the tool with the given input and return a result."""
        raise NotImplementedError

    def diff_preview(self, input: dict) -> str | None:
        """Return a unified diff preview of the change, or None if not applicable.

        Tools that modify files should override this to return a diff of the
        proposed change before it is written to disk.  The diff is included in
        the TOOL_START event so all channels can render it.
        """
        return None

    def to_api_dict(self) -> dict:
        """Serialize to the Anthropic tool definition format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
