"""BifrostAnswerModel — answer calls and the model catalog over Bifröst's HTTP API.

- ``POST {bifrost}/v1/messages`` (Anthropic Messages shape) with the session's own
  ``forge_session`` credential as the bearer (contract C8): Bifröst confirms it with
  Forge and charges the call to the session's owner. No ``max_tokens`` is sent, so
  the provider's own limit bounds the answer, and a cut-off answer is reported by
  ``stop_reason``.
- ``GET {bifrost}/models/{id}`` for the model's ``context_window``, with the
  gateway's service credential (the catalog is not a model route, so a session
  credential is not accepted there).
"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx

from mimir.ports.answer_model import AnswerModel, AnswerModelError, ModelCatalogError, ModelReply
from niuu.ports.http_auth import HttpAuthPort

_WINDOW_KNOB = "the Bifröst model catalog's context_window"


class BifrostAnswerModel(AnswerModel):
    """Args:
    base_url: Bifröst's base URL, e.g. ``http://bifrost:8080``.
    timeout_seconds: per-request timeout (an answer call waits for the whole answer).
    service_auth: how the gateway authenticates as a service (the catalog read).
    """

    def __init__(
        self, base_url: str, *, timeout_seconds: float, service_auth: HttpAuthPort
    ) -> None:
        if not base_url.strip():
            raise ValueError(
                "memory.bifrost_url is empty: the gateway reads the model catalog and makes "
                "memory_ask's answer call through Bifröst; set it to Bifröst's base URL"
            )
        if timeout_seconds <= 0:
            raise ValueError("memory.bifrost_timeout_seconds must be positive")
        self._base = base_url.rstrip("/")
        self._timeout = float(timeout_seconds)
        self._auth = service_auth
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

    async def context_window(self, model: str) -> int:
        headers = await asyncio.to_thread(self._auth.headers)
        url = f"{self._base}/models/{quote(model, safe='')}"
        try:
            response = await self._http().get(
                url, headers={**headers, "Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise ModelCatalogError(
                f"the Bifröst catalog could not be read at {self._base}: {exc}"
            ) from exc
        if response.status_code == 404:
            raise ModelCatalogError(f"model {model!r} is not in the Bifröst catalog")
        if response.status_code in (401, 403):
            self._auth.invalidate()
        if response.status_code != 200:
            raise ModelCatalogError(
                f"the Bifröst catalog answered {response.status_code} for model {model!r}; "
                "check memory.service_auth"
            )
        try:
            entry = response.json()
        except ValueError as exc:
            raise ModelCatalogError("the Bifröst catalog's answer is not JSON") from exc
        window = entry.get("context_window") if isinstance(entry, dict) else None
        if window is None:
            raise ModelCatalogError(
                f"model {model!r} has no context_window in the Bifröst catalog, so the context "
                f"pack cannot be sized; set {_WINDOW_KNOB} for it (its documented input limit)"
            )
        if isinstance(window, bool) or not isinstance(window, int) or window <= 0:
            raise ModelCatalogError(
                f"model {model!r} has context_window={window!r} in the Bifröst catalog; it must "
                "be a positive token count"
            )
        return window


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
