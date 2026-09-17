"""Qwen3-Reranker over a vLLM server.

**Why this uses ``/v1/score`` and not ``/v1/rerank``.** Qwen3-Reranker is instruction-tuned: it
expects the query and document wrapped in its own template, and judges relevance as a
yes/no-style decision. The vLLM server exposes ``/v1/rerank``, but does not apply that template,
and the scores that come back are not relevance at all. Measured against the live server on
2026-08-15, with three documents and three queries:

    query "what port is the broker on"     -> "a recipe for chocolate cake"  0.820  (first)
                                              "the broker listens on 7503"   0.324  (last)
    query "superconducting qubit physics"  -> "a recipe for chocolate cake"  0.653  (first)

The cake won every query, including one about qubits. Wiring that into recall would not merely
fail to help — it would actively demote the correct answer. With the template applied by hand and
scored through ``/v1/score``, the same model orders correctly:

    query "what port is the broker on"     -> broker 0.482, qubits 0.233, cake 0.204
    query "superconducting qubit physics"  -> qubits 0.667, broker 0.250, cake 0.180

So the model is right and the endpoint is misconfigured. This adapter applies the template itself,
which works against the server as it stands today. If ``/v1/rerank`` is later configured with the
template, it becomes the simpler path and this can be swapped for it.
"""

from __future__ import annotations

import math

import httpx

from ravn.ports.reranker import RerankerPort

_DEFAULT_MODEL = "qwen3-reranker"
_DEFAULT_BASE_URL = "http://127.0.0.1:8078/v1"
_TIMEOUT_S = 15.0

#: The task description Qwen3-Reranker was trained against. It is part of the prompt, not a
#: comment: the model conditions on it, and changing it changes the scores.
_INSTRUCTION = "Given a search query, retrieve relevant passages that answer the query"


class QwenRerankerAdapter(RerankerPort):
    """Rerank with Qwen3-Reranker; configured failures are explicit."""

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = "",
        timeout: float = _TIMEOUT_S,
        instruction: str = _INSTRUCTION,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._instruction = instruction
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _prompt(self, query: str, document: str) -> str:
        return f"<Instruct>: {self._instruction}\n<Query>: {query}\n<Document>: {document}"

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """Order documents by relevance; reject failed or incomplete scoring."""
        if not query.strip() or not documents:
            return []
        response = await self._get_client().post(
            f"{self._base_url}/score",
            headers=self._headers(),
            json={
                "model": self._model,
                "text_1": self._prompt(query, ""),
                "text_2": [self._prompt(query, d) for d in documents],
            },
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError("Configured reranker returned no scoring data")
        scored: dict[int, float] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Configured reranker returned an invalid score row")
            index, score = row.get("index"), row.get("score")
            if (
                type(index) is not int
                or not isinstance(score, int | float)
                or isinstance(score, bool)
                or not math.isfinite(score)
                or not 0 <= index < len(documents)
                or index in scored
            ):
                raise ValueError("Configured reranker returned an invalid or duplicate score")
            scored[index] = float(score)
        if len(scored) != len(documents):
            raise ValueError("Configured reranker returned incomplete scoring data")
        ranked = sorted(scored.items(), key=lambda pair: pair[1], reverse=True)
        return ranked[:top_n] if top_n is not None else ranked
