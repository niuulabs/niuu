"""Hook ports — interfaces for pre/post tool execution hooks."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.domain.models import ToolResult


class PreToolHookPort(ABC):
    """Abstract interface for a pre-execution tool hook.

    Called before a tool executes. May return modified args or raise
    ``PermissionDeniedError`` to block execution entirely.
    """

    @abstractmethod
    async def pre_execute(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        """Called before tool execution.

        Args:
            tool_name: Name of the tool being invoked.
            args: Input arguments to be passed to the tool.
            agent_state: Ambient state (e.g. required_permission, session_id).

        Returns:
            Possibly modified args dict passed to the next hook or the tool.

        Raises:
            PermissionDeniedError: If the tool should be blocked.
        """
        raise NotImplementedError


class PostToolHookPort(ABC):
    """Abstract interface for a post-execution tool hook.

    Called after a tool executes. May return a modified result.
    """

    @abstractmethod
    async def post_execute(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
        agent_state: dict,
    ) -> ToolResult:
        """Called after tool execution.

        Args:
            tool_name: Name of the tool that was invoked.
            args: Input arguments that were passed to the tool.
            result: The tool's execution result.
            agent_state: Ambient state at the time of the call.

        Returns:
            Possibly modified ToolResult.
        """
        raise NotImplementedError


class HookPipelinePort(ABC):
    """Abstract interface for the hook pipeline that wraps tool dispatch.

    The registry depends on this port — concrete implementations live in
    ``ravn.adapters.tools.hooks``.
    """

    @abstractmethod
    async def run_pre(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        """Run all pre-hooks in order and return the (possibly modified) args.

        Raises:
            PermissionDeniedError: If any pre-hook blocks execution.
        """
        raise NotImplementedError

    @abstractmethod
    async def run_post(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
        agent_state: dict,
    ) -> ToolResult:
        """Run all post-hooks in order and return the (possibly modified) result."""
        raise NotImplementedError
