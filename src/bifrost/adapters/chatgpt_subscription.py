"""ChatGPT-subscription provider adapter (OpenAI models over a Codex subscription).

Serves OpenAI GPT models such as ``gpt-6-sol`` through the operator's
ChatGPT/Codex *subscription* instead of an API key. It speaks the Responses
API that the Codex CLI uses against ``chatgpt.com/backend-api/codex``, so the
requests are metered against the subscription.

Credential flow: the subscription belongs to a Völundr user. This adapter asks
Völundr's Codex token broker for a short-lived access token (see
:mod:`bifrost.adapters.codex_token_broker`), authenticating as Bifröst's own
workload identity, caches it until shortly before expiry, and sends it with the
account id the broker returns. When the backend answers 401 the token is
refreshed once through the broker; a second 401 is an error, never a retry loop.

Wire format (verified against the live backend):

- the backend only streams, so :meth:`complete` consumes the stream;
- ``temperature`` / ``top_p`` / ``top_k`` are not forwarded (refused upstream);
  ``stop_sequences`` are refused with an error rather than dropped;
- every reasoning item comes back as a ``thinking`` block whose ``signature`` is
  the item's ``encrypted_content``; the summary text is filled in when the caller
  enabled thinking. A caller that sends the block back unchanged lets the next
  step continue that reasoning (the request carries ``store: false``, so this is
  the only way to carry it).

Native passthrough: with ``passthrough: true`` on the provider, a caller that
speaks the Responses API (``POST /v1/responses``) is served without the
Anthropic round trip — its body goes to the backend verbatim apart from the
model name and ``stream: true`` (the backend only streams). That is the path
that carries ``context_management`` compaction, which this backend accepts
(verified live: ``compaction`` output items with ``encrypted_content``);
``POST /responses/compact`` answers 404 here, so standalone compaction is not
offered.

Configure it on a provider (all keys under ``kwargs``)::

    providers:
      chatgpt:
        adapter: bifrost.adapters.chatgpt_subscription.ChatGPTSubscriptionAdapter
        models: [gpt-6-sol]
        kwargs:
          broker_base_url: http://niuu-volundr.volundr.svc
          auth_adapter: niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter
          auth_kwargs:
            exchange_url: https://yggdrasil.niuu.world/api/v1/tokens/workload/exchange
            token_file: /var/run/secrets/niuu/workload/token
            audiences: [volundr-api]
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from bifrost.adapters.codex_token_broker import (
    CodexCredentials,
    CodexSubscriptionError,
    CodexTokenBroker,
)
from bifrost.ports.native import NativePassthroughPort, NativeProtocol
from bifrost.ports.provider import ProviderError, ProviderPort
from bifrost.translation.models import AnthropicRequest, AnthropicResponse
from bifrost.translation.responses_stream import (
    new_message_id,
    responses_stream_to_anthropic,
    responses_stream_to_message,
)
from bifrost.translation.to_responses import anthropic_to_responses, thinking_requested
from niuu.utils import import_class

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_BROKER_TOKEN_PATH = "/api/v1/internal/credentials/codex/tokens"

_ERROR_BODY_LIMIT = 500


# Responses stream events that end the response, and the one that fails it.
_TERMINAL_EVENTS = frozenset({"response.completed", "response.incomplete", "response.failed"})
_FAILED_EVENT = "response.failed"


class ChatGPTSubscriptionAdapter(ProviderPort, NativePassthroughPort):
    """Provider adapter for OpenAI models served over a ChatGPT/Codex subscription."""

    native_protocol = NativeProtocol.OPENAI_RESPONSES
    supports_native_compaction = True

    def __init__(
        self,
        *,
        broker_base_url: str,
        auth_adapter: str,
        auth_kwargs: dict[str, Any] | None = None,
        credential_name: str = "codex-credentials",
        credential_field: str = "auth.json",
        broker_token_path: str = DEFAULT_BROKER_TOKEN_PATH,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
        broker_timeout: float = 15.0,
        refresh_skew_seconds: float = 60.0,
        reasoning_effort: str = "",
        reasoning_summary: str = "auto",
        parallel_tool_calls: bool = True,
        originator: str = "codex_cli_rs",
        openai_beta: str = "responses=experimental",
        session_id: str = "",
    ) -> None:
        if not broker_base_url:
            raise ValueError(
                "ChatGPTSubscriptionAdapter needs kwargs.broker_base_url: the Völundr "
                "base URL that serves /api/v1/internal/credentials/codex/tokens"
            )
        if not auth_adapter:
            raise ValueError(
                "ChatGPTSubscriptionAdapter needs kwargs.auth_adapter: the class path of "
                "the HttpAuthPort that authenticates to the token broker, e.g. "
                "niuu.adapters.outbound.http_auth.WorkloadIdentityBearerTokenAuthAdapter"
            )
        self._responses_url = f"{base_url.rstrip('/')}/responses"
        self._reasoning_effort = reasoning_effort
        self._reasoning_summary = reasoning_summary
        self._parallel_tool_calls = parallel_tool_calls
        self._originator = originator
        self._openai_beta = openai_beta
        self._session_id = session_id or str(uuid.uuid4())
        self._client = httpx.AsyncClient(timeout=timeout)
        self._broker = CodexTokenBroker(
            base_url=broker_base_url,
            auth=import_class(auth_adapter)(**(auth_kwargs or {})),
            credential_name=credential_name,
            credential_field=credential_field,
            token_path=broker_token_path,
            timeout=broker_timeout,
            refresh_skew_seconds=refresh_skew_seconds,
        )

    def _headers(self, credentials: CodexCredentials) -> dict[str, str]:
        return {
            "authorization": f"Bearer {credentials.access_token}",
            "chatgpt-account-id": credentials.account_id,
            "openai-beta": self._openai_beta,
            "originator": self._originator,
            "session_id": self._session_id,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

    def _payload(self, request: AnthropicRequest, model: str) -> dict[str, Any]:
        return anthropic_to_responses(
            request,
            model,
            default_reasoning_effort=self._reasoning_effort,
            reasoning_summary=self._reasoning_summary,
            parallel_tool_calls=self._parallel_tool_calls,
        )

    async def _response_lines(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        """Open the Responses stream and yield its lines.

        A 401 is answered by one token refresh through the broker and one
        retry; any other failure, or a second 401, is raised.
        """
        credentials = await self._broker.credentials()
        refreshed = False
        while True:
            async with self._client.stream(
                "POST", self._responses_url, headers=self._headers(credentials), json=payload
            ) as response:
                if response.status_code == httpx.codes.UNAUTHORIZED and not refreshed:
                    await response.aread()
                    refreshed = True
                    logger.info("ChatGPT backend rejected the cached token; refreshing once")
                    credentials = await self._broker.credentials(rejected=credentials)
                    continue
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    await response.aread()
                    raise self._status_error(response)
                async for line in response.aiter_lines():
                    yield line
                return

    @staticmethod
    def _status_error(response: httpx.Response) -> Exception:
        detail = _error_detail(response)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            return CodexSubscriptionError(
                "the ChatGPT backend rejected a freshly refreshed Codex token "
                f"(401: {detail}); the subscription may need reconnecting in Völundr"
            )
        return httpx.HTTPStatusError(
            f"ChatGPT backend answered {response.status_code}: {detail}",
            request=response.request,
            response=response,
        )

    async def complete(self, request: AnthropicRequest, model: str) -> AnthropicResponse:
        payload = self._payload(request, model)
        async with contextlib.aclosing(self._response_lines(payload)) as lines:
            return await responses_stream_to_message(
                lines,
                message_id=new_message_id(),
                model=model,
                include_thinking=thinking_requested(request),
            )

    async def stream(self, request: AnthropicRequest, model: str) -> AsyncIterator[str]:
        payload = self._payload(request, model)
        async with contextlib.aclosing(self._response_lines(payload)) as lines:
            async for event in responses_stream_to_anthropic(
                lines,
                message_id=new_message_id(),
                model=model,
                include_thinking=thinking_requested(request),
            ):
                yield event

    async def native_stream(self, payload: dict[str, Any], model: str) -> AsyncIterator[str]:
        body = {**payload, "model": model, "stream": True}
        async with contextlib.aclosing(self._response_lines(body)) as lines:
            async for line in lines:
                yield f"{line}\n"

    async def native_complete(self, payload: dict[str, Any], model: str) -> dict[str, Any]:
        """Consume the stream and return the terminal event's ``response``.

        The backend only streams. Its ``response.completed`` object does not
        always list the output, so the items announced by
        ``response.output_item.done`` are put in when it arrives empty.
        """
        items: list[dict[str, Any]] = []
        body = {**payload, "model": model, "stream": True}
        async with contextlib.aclosing(self._response_lines(body)) as lines:
            async for line in lines:
                event = _sse_event(line)
                if event is None:
                    continue
                kind = event.get("type")
                if kind == "response.output_item.done" and isinstance(event.get("item"), dict):
                    items.append(event["item"])
                    continue
                if kind not in _TERMINAL_EVENTS:
                    continue
                response = dict(event.get("response") or {})
                if kind == _FAILED_EVENT:
                    error = response.get("error") or {}
                    raise ProviderError(
                        f"ChatGPT backend failed the response: {error.get('message') or error}"
                    )
                if not response.get("output"):
                    response["output"] = items
                return response
        raise ProviderError("ChatGPT backend ended the stream without a terminal response event")

    async def close(self) -> None:
        await self._client.aclose()
        await self._broker.close()


def _sse_event(line: str) -> dict[str, Any] | None:
    """The JSON object on an SSE ``data:`` line, or ``None`` for any other line."""
    if not line.startswith("data: "):
        return None
    try:
        event = json.loads(line[len("data: ") :])
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _error_detail(response: httpx.Response) -> str:
    """The backend's error text: ``detail`` or ``error.message``, else the raw body."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:_ERROR_BODY_LIMIT] or "no detail"
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
        if body.get("detail"):
            return str(body["detail"])
    return response.text[:_ERROR_BODY_LIMIT] or "no detail"
