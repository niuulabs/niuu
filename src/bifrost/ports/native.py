"""NativePassthroughPort — serve a request in the provider's own protocol.

The canonical path translates every request into Bifröst's Anthropic Messages
format and back out again. A provider whose adapter implements this port, and
whose configuration sets ``passthrough: true``, is instead handed the caller's
request body verbatim when the caller speaks the provider's native protocol:
Bifröst rewrites only the model name and keeps everything it must do itself
(auth, routing, quotas, usage and cost, audit, events) around the call.

What the payload carries that the canonical format cannot — reasoning items,
compaction items and ``context_management``, hosted tools — therefore reaches
the provider and comes back unchanged.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Any


class NativeProtocol(StrEnum):
    """Wire protocols a provider can be served in without translation."""

    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"


class NativePassthroughPort(ABC):
    """A provider adapter that can serve requests in its native protocol."""

    native_protocol: NativeProtocol
    """The protocol :meth:`native_stream` and :meth:`native_complete` speak."""

    supports_native_compaction: bool = False
    """Whether the provider compacts context itself when the payload asks it to.

    A passthrough request that asks for provider compaction (``context_management``
    on the Responses API) is refused with a 400 when this is ``False``, rather
    than forwarded to a provider that would ignore or reject it.
    """

    @abstractmethod
    def native_stream(self, payload: dict[str, Any], model: str) -> AsyncIterator[str]:
        """Stream *payload* to the provider and yield its SSE output line by line.

        Every yielded string is one SSE line terminated by ``\\n`` (an empty
        line, ``"\\n"``, ends an event), so the inbound layer can forward the
        stream to the caller as is.

        Raises:
            httpx.HTTPStatusError: The provider refused the request.
            ProviderError: The provider failed in a non-retryable way.
        """

    @abstractmethod
    async def native_complete(self, payload: dict[str, Any], model: str) -> dict[str, Any]:
        """Send *payload* and return the provider's complete response body.

        Raises:
            httpx.HTTPStatusError: The provider refused the request.
            ProviderError: The provider failed in a non-retryable way.
        """
