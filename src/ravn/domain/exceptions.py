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


class OutputTruncatedError(LLMError):
    """Raised when a model answer stopped because it ran out of output room.

    A truncated answer is not an answer: prose cut mid-sentence reads as a
    conclusion, and a tool call cut mid-argument parses to an empty input.
    ``knob`` is the setting that bounded the call, so the failure names its own
    remedy.
    """

    def __init__(self, *, knob: str, message: str) -> None:
        self.knob = knob
        super().__init__(message)


class ContextCompressionError(RavnError):
    """Raised when client-side compaction cannot produce its state document.

    The compactor replaces history with that document, so compacting without
    one would throw the conversation away while the turn carried on as if it
    remembered it.
    """


class ConfigurationError(RavnError):
    """Raised when Ravn is misconfigured."""


class MimirUnavailableError(RavnError):
    """Raised when Mímir could not answer, so absence cannot be concluded.

    "Not found" and "could not ask" are different answers. Collapsing them
    reported a transient 503 as "research page references missing source_ids",
    which reads as a permanent provenance defect and prescribes a remedy —
    ingest the sources — that cannot fix an outage.
    """


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


class UnboundOperatorReplyError(RavnError):
    """Raised when a human reply names a resident case that holds no open question.

    The reply answers a question the resident no longer has open: it was
    answered, withdrawn or superseded, or never persisted at all. Applying it to
    some other case, or quietly treating it as fresh chat, would hide that the
    question and its answer have come apart.
    """

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        super().__init__(
            f"operator reply names resident case {case_id}, which has no pending "
            "question: it was answered, withdrawn or superseded, or never persisted"
        )
