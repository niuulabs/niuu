"""BifrostAdapter — LLMPort implementation routing through Niuu's centralised LLM proxy.

Bifrost exposes an Anthropic Messages API-compatible endpoint, providing:
- Centralised key management (no api_key needed in the agent)
- Usage tracking per agent/session via identity headers
- Model routing and aliasing
- Centralised rate limiting (Bifrost handles upstream 429s)

Subclasses AnthropicAdapter — same wire protocol, same SSE parsing, same retry
logic.  The only differences are that api_key is omitted from headers and two
agent-identity headers are injected for usage attribution.
"""

from __future__ import annotations

from ravn.adapters.llm.anthropic import (
    ANTHROPIC_API_VERSION,  # noqa: F401 — re-exported for tests
    AnthropicAdapter,
)

__all__ = [
    "ANTHROPIC_API_VERSION",
    "BifrostAdapter",
    "HEADER_AGENT_ID",
    "HEADER_LEGACY_AGENT_ID",
    "HEADER_LEGACY_SESSION_ID",
    "HEADER_SESSION_ID",
]

_DEFAULT_BASE_URL = "http://bifrost:8080"

HEADER_AGENT_ID = "X-Agent-Id"
HEADER_SESSION_ID = "X-Session-Id"

#: What this adapter used to send, and what Bifröst never read.
#:
#: Bifröst's auth adapters read `x-agent-id`; this one sent `X-Ravn-Agent-Id`,
#: so every call in the estate was attributed to `anonymous` — a usage store
#: with a column for the caller that had never seen one. Bifröst now accepts
#: both, and these are still sent so a Bifröst on an older image keeps working.
HEADER_LEGACY_AGENT_ID = "X-Ravn-Agent-Id"
HEADER_LEGACY_SESSION_ID = "X-Ravn-Session-Id"


class BifrostAdapter(AnthropicAdapter):
    """Routes LLM calls through Niuu's Bifrost proxy.

    Subclasses AnthropicAdapter — same Anthropic Messages API protocol,
    but omits api_key and injects agent identity headers for usage attribution.

    Constructor kwargs are forwarded from config via the dynamic adapter pattern.
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        model: str = "claude-sonnet-4-20250514",
        max_tokens: int = 8192,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        timeout: float = 120.0,
        agent_id: str = "",
        session_id: str = "",
    ) -> None:
        super().__init__(
            api_key="",
            base_url=base_url,
            model=model,
            max_tokens=max_tokens,
            max_retries=max_retries,
            retry_base_delay=retry_base_delay,
            timeout=timeout,
        )
        self._agent_id = agent_id
        self._session_id = session_id

    def _headers(self, *, thinking_enabled: bool = False) -> dict[str, str]:
        headers = super()._headers(thinking_enabled=thinking_enabled)
        # Bifrost manages API keys — remove the empty x-api-key header
        headers.pop("x-api-key", None)
        # Inject agent identity for per-agent usage tracking and cost attribution
        if self._agent_id:
            headers[HEADER_AGENT_ID] = self._agent_id
            headers[HEADER_LEGACY_AGENT_ID] = self._agent_id
        if self._session_id:
            headers[HEADER_SESSION_ID] = self._session_id
            headers[HEADER_LEGACY_SESSION_ID] = self._session_id
        return headers
