"""Permission port — interface for tool execution authorization."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class PermissionMode(StrEnum):
    """Built-in permission modes."""

    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    FULL_ACCESS = "full_access"
    PROMPT = "prompt"
    # Legacy aliases
    ALLOW_ALL = "allow_all"
    DENY_ALL = "deny_all"


class CommandIntent(StrEnum):
    """Classification of a shell command's intent."""

    READ_ONLY = "read_only"
    WRITE = "write"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    PROCESS_MANAGEMENT = "process_management"
    PACKAGE_MANAGEMENT = "package_management"
    SYSTEM_ADMIN = "system_admin"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Permission decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Allow:
    """Permission decision: the action is allowed."""


@dataclass(frozen=True)
class Deny:
    """Permission decision: the action is denied."""

    reason: str


@dataclass(frozen=True)
class NeedsApproval:
    """Permission decision: interactive approval required before proceeding."""

    question: str


PermissionDecision = Allow | Deny | NeedsApproval


# ---------------------------------------------------------------------------
# Simple port (backward-compatible)
# ---------------------------------------------------------------------------


class PermissionPort(ABC):
    """Abstract interface for checking tool execution permissions."""

    @abstractmethod
    async def check(self, permission: str) -> bool:
        """Return True if the given permission is granted, False to deny."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Rich enforcer port
# ---------------------------------------------------------------------------


class PermissionEnforcerPort(ABC):
    """Rich permission enforcer — evaluates full tool context for decisions.

    Implementations receive the tool name *and* its arguments, enabling
    deep inspection (e.g., bash command analysis, file-path boundary checks)
    that is impossible from a bare permission string.
    """

    @abstractmethod
    async def evaluate(self, tool_name: str, args: dict) -> PermissionDecision:
        """Evaluate whether a tool call should be allowed.

        Args:
            tool_name: Name of the tool being invoked.
            args: Input arguments supplied to the tool.

        Returns:
            Allow, Deny, or NeedsApproval decision.
        """
        raise NotImplementedError

    @abstractmethod
    def check_file_write(self, path: str | Path, workspace_root: Path) -> PermissionDecision:
        """Validate a file write target against workspace boundaries.

        Args:
            path: Target path for the write operation.
            workspace_root: Allowed root directory.

        Returns:
            Allow if the path is safe, Deny otherwise.
        """
        raise NotImplementedError

    @abstractmethod
    def check_bash(self, command: str) -> PermissionDecision:
        """Validate a bash command through the multi-stage pipeline.

        Args:
            command: Shell command string to validate.

        Returns:
            Allow, Deny, or NeedsApproval depending on command analysis.
        """
        raise NotImplementedError

    @abstractmethod
    def record_approval(self, tool_name: str, args: dict) -> None:
        """Persist an explicit user approval so the same call is auto-approved later.

        Called by the agent loop (or hook pipeline) after the user confirms a
        NeedsApproval prompt.  Implementations that do not support approval
        memory should provide a no-op.

        Args:
            tool_name: Name of the tool the user approved.
            args: Arguments that were supplied to the tool.
        """
        raise NotImplementedError
