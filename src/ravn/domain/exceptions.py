"""Domain exceptions for Ravn."""

from __future__ import annotations


class RavnError(Exception):
    """Base exception for all Ravn errors."""


class PermissionDeniedError(RavnError):
    """Raised when a tool call is denied by the permission enforcer."""

    def __init__(self, tool_name: str, permission: str) -> None:
        self.tool_name = tool_name
        self.permission = permission
        super().__init__(f"Permission '{permission}' denied for tool '{tool_name}'")


class ToolExecutionError(RavnError):
    """Raised when a tool fails to execute."""

    def __init__(self, tool_name: str, cause: Exception) -> None:
        self.tool_name = tool_name
        self.cause = cause
        super().__init__(f"Tool '{tool_name}' failed: {cause}")


class LLMError(RavnError):
    """Raised when the LLM call fails after all retries."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)


class MaxIterationsError(RavnError):
    """Raised when the agent loop exceeds the maximum allowed iterations."""

    def __init__(self, max_iterations: int) -> None:
        self.max_iterations = max_iterations
        super().__init__(f"Agent loop exceeded {max_iterations} iterations")


class ConfigurationError(RavnError):
    """Raised when Ravn is misconfigured."""


class PromptBudgetExceededError(RavnError):
    """Raised when a turn's estimated prompt exceeds the configured hard budget.

    Raised instead of sending a request that would overflow the model's
    context window — the message carries a per-section breakdown so the
    oversized section is identifiable from the failure alone.
    """

    def __init__(
        self,
        *,
        estimated_tokens: int,
        budget_tokens: int,
        sections: dict[str, int],
    ) -> None:
        self.estimated_tokens = estimated_tokens
        self.budget_tokens = budget_tokens
        self.sections = dict(sections)
        breakdown = ", ".join(f"{name}≈{count}" for name, count in sections.items())
        super().__init__(
            f"Prompt budget exceeded: estimated {estimated_tokens} tokens > "
            f"budget {budget_tokens} ({breakdown})"
        )


class AllProvidersExhaustedError(RavnError):
    """Raised when all LLM providers in the fallback chain have failed."""

    def __init__(self, provider_count: int, last_error: Exception | None = None) -> None:
        self.provider_count = provider_count
        self.last_error = last_error
        super().__init__(
            f"All {provider_count} LLM provider(s) failed; see provider logs for details."
        )
