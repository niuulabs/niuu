"""Built-in hook implementations for the Ravn tool execution pipeline.

Configuration example (ravn.yaml):

    tools:
      hooks:
        pre:
          - adapter: ravn.adapters.tools.hooks.PermissionHook
          - adapter: ravn.adapters.tools.hooks.BudgetHook
            kwargs:
              max_calls: 50
        post:
          - adapter: ravn.adapters.tools.hooks.AuditHook
          - adapter: ravn.adapters.tools.hooks.SanitisationHook
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field

from ravn.domain.exceptions import PermissionDeniedError
from ravn.domain.models import ToolResult
from ravn.ports.hooks import HookPipelinePort, PostToolHookPort, PreToolHookPort
from ravn.ports.permission import Allow, NeedsApproval, PermissionEnforcerPort, PermissionPort

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Secret patterns used by SanitisationHook
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    # Generic API key / token assignments  (key = value)
    re.compile(
        r"(?i)(api[_-]?key|apikey|api[_-]?token|auth[_-]?token|access[_-]?token)\s*[=:]\s*\S+"
    ),
    # Bearer tokens in Authorization headers
    re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    # AWS access key IDs
    re.compile(r"AKIA[0-9A-Z]{16}"),
    # AWS secret / access key assignments
    re.compile(r"(?i)aws[_-]?(secret|access)[_-]?key\s*[=:]\s*\S+"),
    # PEM private key headers
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----"),
    # Password assignments
    re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"),
    # Generic secret / token assignments (≥8 chars value)
    re.compile(r"(?i)(secret|token)\s*[=:]\s*[A-Za-z0-9\-._~+/]{8,}"),
]

_REDACTED = "[REDACTED]"


# ---------------------------------------------------------------------------
# PermissionHook
# ---------------------------------------------------------------------------


class PermissionHook(PreToolHookPort):
    """Pre-hook that validates tool permission via a PermissionPort.

    Reads ``required_permission`` from ``agent_state`` and delegates to the
    configured ``PermissionPort``.  Raises ``PermissionDeniedError`` when the
    permission is not granted, which the registry converts to an error result.

    This replaces the inline permission check that would otherwise live
    directly inside the dispatch path.
    """

    def __init__(self, permission: PermissionPort) -> None:
        self._permission = permission

    async def pre_execute(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        required = agent_state.get("required_permission", "")
        granted = await self._permission.check(required)
        if not granted:
            raise PermissionDeniedError(tool_name, required)
        return args


# ---------------------------------------------------------------------------
# EnforcerHook
# ---------------------------------------------------------------------------


class EnforcerHook(PreToolHookPort):
    """Pre-hook that runs the full PermissionEnforcer evaluation pipeline.

    Unlike the simple ``PermissionHook`` (which checks a bare permission string),
    this hook passes the tool name *and* all input arguments to the enforcer so
    it can perform deep analysis: bash command parsing, file-path boundary checks,
    mode-aware policy, etc.

    Decisions
    ---------
    - Allow           → args passed through unmodified
    - NeedsApproval   → raises PermissionDeniedError (pending CLI prompt support)
    - Deny            → raises PermissionDeniedError
    """

    def __init__(self, enforcer: PermissionEnforcerPort) -> None:
        self._enforcer = enforcer

    async def pre_execute(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        decision = await self._enforcer.evaluate(tool_name, args)

        if isinstance(decision, Allow):
            return args

        if isinstance(decision, NeedsApproval):
            # Hooks cannot safely prompt without a channel-bound approval port.
            # Fail closed and let the caller retry through an approval-aware surface.
            raise PermissionDeniedError(tool_name, f"needs_approval:{decision.question}")

        # isinstance(decision, Deny)
        raise PermissionDeniedError(tool_name, decision.reason)


# ---------------------------------------------------------------------------
# BudgetHook
# ---------------------------------------------------------------------------


class BudgetHook(PreToolHookPort):
    """Pre-hook that enforces a maximum tool-call budget per hook instance.

    Each call to ``pre_execute`` increments an internal counter.  Once the
    counter exceeds ``max_calls``, further calls raise ``PermissionDeniedError``
    so the registry returns an error result rather than executing the tool.

    The counter is instance-scoped; create one ``BudgetHook`` per session to
    track per-session budgets, or share one across sessions for a global cap.
    """

    def __init__(self, max_calls: int) -> None:
        self._max_calls = max_calls
        self._call_count = 0

    @property
    def call_count(self) -> int:
        """Number of tool calls seen so far."""
        return self._call_count

    @property
    def max_calls(self) -> int:
        """Configured maximum calls."""
        return self._max_calls

    async def pre_execute(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        self._call_count += 1
        if self._call_count > self._max_calls:
            raise PermissionDeniedError(tool_name, f"budget:{self._max_calls}")
        return args


# ---------------------------------------------------------------------------
# AuditHook
# ---------------------------------------------------------------------------


@dataclass
class AuditEntry:
    """One structured audit record for a completed tool call."""

    tool_name: str
    args: dict
    result_content: str
    is_error: bool
    elapsed_ms: float
    timestamp: float = field(default_factory=time.time)


_DEFAULT_MAX_AUDIT_ENTRIES = 1000


class AuditHook(PostToolHookPort):
    """Post-hook that records a structured audit entry for every tool call.

    Entries are accumulated in ``self.entries`` (a bounded deque) and also
    emitted at DEBUG level so they appear in structured logs when the log
    level is set low enough.  Once ``max_entries`` is reached the oldest
    entry is evicted automatically, bounding memory use in long-running
    sessions.

    The hook reads ``_started_at`` from ``agent_state`` (a ``time.monotonic``
    stamp set by the registry before dispatch) to compute elapsed time.
    """

    def __init__(self, max_entries: int = _DEFAULT_MAX_AUDIT_ENTRIES) -> None:
        self.entries: deque[AuditEntry] = deque(maxlen=max_entries)

    async def post_execute(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
        agent_state: dict,
    ) -> ToolResult:
        started_at: float = agent_state.get("_started_at", time.monotonic())
        elapsed_ms = (time.monotonic() - started_at) * 1000.0

        entry = AuditEntry(
            tool_name=tool_name,
            args=args,
            result_content=result.content,
            is_error=result.is_error,
            elapsed_ms=elapsed_ms,
        )
        self.entries.append(entry)

        logger.debug(
            "audit tool=%r is_error=%s elapsed_ms=%.1f",
            tool_name,
            result.is_error,
            elapsed_ms,
        )
        return result


# ---------------------------------------------------------------------------
# SanitisationHook
# ---------------------------------------------------------------------------


class SanitisationHook(PostToolHookPort):
    """Post-hook that strips secrets and sensitive tokens from tool results.

    Scans the result content string against a configurable set of regex
    patterns and replaces any match with ``[REDACTED]``.  This prevents
    accidentally captured secrets (API keys, passwords, bearer tokens, etc.)
    from being injected into the LLM context.

    A fresh ``ToolResult`` is returned only when content actually changed,
    so the hook is a no-op for clean output.
    """

    def __init__(
        self,
        patterns: list[re.Pattern[str]] | None = None,
        redacted: str = _REDACTED,
    ) -> None:
        self._patterns = patterns if patterns is not None else _SECRET_PATTERNS
        self._redacted = redacted

    async def post_execute(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
        agent_state: dict,
    ) -> ToolResult:
        sanitised = result.content
        for pattern in self._patterns:
            sanitised = pattern.sub(self._redacted, sanitised)

        if sanitised == result.content:
            return result

        return ToolResult(
            tool_call_id=result.tool_call_id,
            content=sanitised,
            is_error=result.is_error,
        )


# ---------------------------------------------------------------------------
# HookPipeline
# ---------------------------------------------------------------------------


class HookPipeline(HookPipelinePort):
    """Orchestrates pre and post hooks around a tool dispatch.

    Pre-hooks run in registration order before the tool executes; any hook
    may raise ``PermissionDeniedError`` to abort the call.  Post-hooks run in
    registration order after execution and may transform the result.

    The pipeline is transparent to individual tool implementations — they
    receive (possibly modified) args and their results may be modified before
    being returned to the caller.
    """

    def __init__(
        self,
        pre_hooks: list[PreToolHookPort] | None = None,
        post_hooks: list[PostToolHookPort] | None = None,
    ) -> None:
        self._pre_hooks: list[PreToolHookPort] = pre_hooks or []
        self._post_hooks: list[PostToolHookPort] = post_hooks or []

    @property
    def pre_hooks(self) -> list[PreToolHookPort]:
        return list(self._pre_hooks)

    @property
    def post_hooks(self) -> list[PostToolHookPort]:
        return list(self._post_hooks)

    async def run_pre(
        self,
        tool_name: str,
        args: dict,
        agent_state: dict,
    ) -> dict:
        """Run all pre-hooks in order and return the (possibly modified) args."""
        for hook in self._pre_hooks:
            args = await hook.pre_execute(tool_name, args, agent_state)
        return args

    async def run_post(
        self,
        tool_name: str,
        args: dict,
        result: ToolResult,
        agent_state: dict,
    ) -> ToolResult:
        """Run all post-hooks in order and return the (possibly modified) result."""
        for hook in self._post_hooks:
            result = await hook.post_execute(tool_name, args, result, agent_state)
        return result
