"""ProviderPort — abstract interface for LLM provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from bifrost.translation.models import AnthropicRequest, AnthropicResponse


class ProviderError(Exception):
    """Raised when a provider call fails in a non-retryable way."""


class ProviderPort(ABC):
    """Abstract interface that every provider adapter must implement."""

    accepts_context_management: bool = False
    """Whether the adapter forwards Anthropic ``context_management`` and
    ``compaction`` blocks to a provider that honours them.

    Only the Anthropic adapter does: the canonical format *is* its wire format.
    A translating adapter has nowhere to put them, so the router refuses such a
    request for it instead of letting the compaction state silently vanish.
    """

    @abstractmethod
    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        """Perform a non-streaming completion.

        Args:
            request: The inbound Anthropic-format request.
            model: The resolved model name for this provider.

        Returns:
            An ``AnthropicResponse`` ready to return to the caller.

        Raises:
            ProviderError: On non-retryable provider errors.
            httpx.HTTPStatusError: On HTTP-level errors (callers handle retries).
        """

    @abstractmethod
    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        """Perform a streaming completion and yield Anthropic-format SSE lines.

        Args:
            request: The inbound Anthropic-format request.
            model: The resolved model name for this provider.

        Yields:
            Anthropic-format SSE event strings.

        Raises:
            ProviderError: On non-retryable provider errors.
        """

    async def close(self) -> None:
        """Release any held resources (e.g. HTTP client connections)."""
