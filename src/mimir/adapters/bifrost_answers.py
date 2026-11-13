"""BifrostAnswerModel — answer calls over Bifröst's HTTP API.

- ``POST {bifrost}/v1/messages`` (Anthropic Messages shape) with the session's own
  ``forge_session`` credential as the bearer (contract C8): Bifröst confirms it with
  Forge and charges the call to the session's owner. No ``max_tokens`` is sent, so
  the provider's own limit bounds the answer, and a cut-off answer is reported by
  ``stop_reason``.
"""

from __future__ import annotations

from typing import Any

import httpx

from mimir.ports.answer_model import AnswerModel, AnswerModelError, ModelReply


class BifrostAnswerModel(AnswerModel):
    """Args:
    base_url: Bifröst's base URL, e.g. ``http://bifrost:8080``.
    timeout_seconds: per-request timeout (an answer call waits for the whole answer).
    """

    def __init__(self, base_url: str, *, timeout_seconds: float) -> None:
        if not base_url.strip():
            raise ValueError(
                "memory.bifrost_url is empty: the gateway makes memory_ask's answer call "
                "through Bifröst; set it to Bifröst's base URL"
            )
        if timeout_seconds <= 0:
            raise ValueError("memory.bifrost_timeout_seconds must be positive")
        self._base = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def answer(self, *, model: str, system: str, prompt: str, credential: str) -> ModelReply:
        body = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        try:
            response = await self._http().post(
                f"{self._base}/v1/messages",
                json=body,
                headers={"Authorization": f"Bearer {credential}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise AnswerModelError(f"Bifröst could not be reached at {self._base}: {exc}") from exc
        if response.status_code != 200:
            raise AnswerModelError(
                f"Bifröst answered {response.status_code} for model {model!r}: {response.text}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise AnswerModelError("Bifröst's answer is not JSON") from exc
        return _reply(payload)


def _reply(payload: Any) -> ModelReply:
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        raise AnswerModelError(f"Bifröst's answer has no content blocks: {payload!r}")
    text: list[str] = []
    reasoning: list[str] = []
    tools: list[Any] = []
    for block in payload["content"]:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text.append(str(block.get("text") or ""))
        elif kind == "thinking":
            reasoning.append(str(block.get("thinking") or ""))
        elif kind == "tool_use":
            tools.append(block)
    return ModelReply(
        content="".join(text),
        stop_reason=str(payload.get("stop_reason") or ""),
        reasoning="".join(reasoning),
        tool_calls=tuple(tools),
    )
