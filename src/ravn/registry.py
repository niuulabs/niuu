"""Tool registry — register, validate, and dispatch tools."""

from __future__ import annotations

import asyncio
import logging
import time

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort
from ravn.domain.exceptions import PermissionDeniedError
from ravn.domain.models import ToolCall
from ravn.ports.hooks import HookPipelinePort

logger = logging.getLogger(__name__)

_VALID_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "integer", "number", "boolean", "null"}
)


class ToolRegistrationError(Exception):
    """Raised when a tool cannot be registered."""


class ToolRegistry:
    """Registry of agent tools.

    Handles registration (with collision detection and schema validation),
    dispatching (with exception capture), and listing.

    An optional ``HookPipeline`` can be supplied to run pre/post hooks around
    every dispatch call — transparent to individual tool implementations.
    """

    def __init__(self, hook_pipeline: HookPipelinePort | None = None) -> None:
        self._tools: dict[str, ToolPort] = {}
        self._pipeline = hook_pipeline

    def register(self, tool: ToolPort) -> None:
        """Register *tool*.

        Raises:
            ToolRegistrationError: on name collision or invalid JSON Schema.
        """
        name = tool.name
        if name in self._tools:
            raise ToolRegistrationError(f"Tool '{name}' is already registered")

        _validate_schema(name, tool.input_schema)
        self._tools[name] = tool

    async def dispatch(
        self,
        name: str,
        input: dict,
        call_id: str,
        agent_state: dict | None = None,
    ) -> ToolResult:
        """Execute tool *name* with *input*, returning a ToolResult.

        The hook pipeline (if configured) runs pre-hooks before the tool
        and post-hooks after.  Pre-hooks may modify args or raise
        ``PermissionDeniedError`` to block execution.  Post-hooks may modify
        the result before it is returned.

        Unknown tools and execution errors are returned as error results —
        exceptions are never propagated to the caller.

        Args:
            name: Registered tool name.
            input: Tool input arguments.
            call_id: Correlation ID for the ToolResult.
            agent_state: Optional ambient state forwarded to hooks.  The
                registry injects ``required_permission`` and ``_started_at``
                automatically when they are absent.
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool_call_id=call_id,
                content=f"Unknown tool: {name!r}",
                is_error=True,
            )

        state: dict = dict(agent_state) if agent_state else {}
        state.setdefault("required_permission", tool.required_permission)
        state.setdefault("_started_at", time.monotonic())

        try:
            args = input
            if self._pipeline is not None:
                args = await self._pipeline.run_pre(name, args, state)

            raw = await tool.execute(args)
            result = ToolResult(
                tool_call_id=call_id,
                content=raw.content,
                is_error=raw.is_error,
            )

            if self._pipeline is not None:
                result = await self._pipeline.run_post(name, args, result, state)

            return result

        except PermissionDeniedError as exc:
            return ToolResult(
                tool_call_id=call_id,
                content=str(exc),
                is_error=True,
            )
        except Exception as exc:
            logger.warning("Tool %r raised: %s", name, exc)
            return ToolResult(
                tool_call_id=call_id,
                content=f"Tool error: {exc}",
                is_error=True,
            )

    async def dispatch_batch(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Execute a batch of tool calls, returning results in the same order.

        When all tools in the batch declare ``parallelisable=True`` (the default),
        calls are executed concurrently via ``asyncio.gather``.  If any tool
        declares ``parallelisable=False`` the entire batch falls back to
        sequential execution so that ordering guarantees are preserved.

        Unknown tools and execution errors are captured as error results —
        exceptions are never propagated to the caller.
        """
        if not calls:
            return []

        all_parallelisable = all(
            (self._tools[c.name].parallelisable if c.name in self._tools else True) for c in calls
        )

        if all_parallelisable:
            return list(
                await asyncio.gather(*[self.dispatch(c.name, c.input, c.id) for c in calls])
            )

        results: list[ToolResult] = []
        for call in calls:
            result = await self.dispatch(call.name, call.input, call.id)
            results.append(result)
        return results

    def list(self) -> list[ToolPort]:
        """Return all registered tools in registration order."""
        return list(self._tools.values())

    def get(self, name: str) -> ToolPort | None:
        """Return a tool by name, or None if not registered."""
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)


def _validate_schema(name: str, schema: dict) -> None:
    """Validate that *schema* is a structurally plausible JSON Schema."""
    if not isinstance(schema, dict):
        raise ToolRegistrationError(
            f"Tool '{name}' input_schema must be a dict, got {type(schema).__name__}"
        )

    schema_type = schema.get("type")
    if schema_type is not None and schema_type not in _VALID_SCHEMA_TYPES:
        raise ToolRegistrationError(f"Tool '{name}' input_schema has invalid type: {schema_type!r}")

    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        raise ToolRegistrationError(f"Tool '{name}' input_schema 'properties' must be a dict")
