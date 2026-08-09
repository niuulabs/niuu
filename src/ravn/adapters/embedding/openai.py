"""OpenAI embeddings adapter.

Calls the OpenAI ``/v1/embeddings`` endpoint using ``httpx``.  No OpenAI SDK
dependency required — only ``httpx``, which is already a project dependency.

A single persistent ``httpx.AsyncClient`` is reused across calls to avoid
the overhead of creating a new TCP connection pool for each request.
Call ``await adapter.close()`` when done to release the connection pool.
"""

from __future__ import annotations

import logging
import os

import httpx

from ravn.ports.embedding import EmbeddingPort

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "text-embedding-3-small"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
# text-embedding-3-small default dimension
_DEFAULT_DIMENSION = 1536

# Embedding models reject input past their context window rather than
# truncating it. Qwen3-Embedding-0.6B on our vLLM answers 400 with
# "You passed 8193 input tokens ... context length is only 8192", and because
# memory prefetch treats an embedding failure as fatal, one long episode kills
# the whole turn — Runa lost ten turns that way in six hours.
#
# Chars rather than tokens: this adapter serves several models and has no
# tokenizer for any of them. Four chars per token is the usual conservative
# ratio for English prose, so the default leaves room under an 8k window.
# Raise it for a model with a larger context.
_DEFAULT_MAX_INPUT_CHARS = 24_000


class OpenAIEmbeddingAdapter(EmbeddingPort):
    """Embedding adapter using OpenAI's embeddings API.

    Args:
        api_key: OpenAI API key.  When empty the ``OPENAI_API_KEY`` env var is used.
        model: Embedding model name.
        base_url: Base URL for the OpenAI (or compatible) API.
        timeout: HTTP request timeout in seconds.
        max_input_chars: Longest input sent per text. Longer text is
            truncated rather than rejected — see ``_DEFAULT_MAX_INPUT_CHARS``.
            0 disables truncation, which restores the old behaviour of letting
            the model refuse oversized input.
    """

    def __init__(
        self,
        api_key: str = "",
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_input_chars: int = _DEFAULT_MAX_INPUT_CHARS,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_input_chars = max_input_chars
        self._dimension: int | None = None
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        """Close the persistent HTTP client and release connection pool."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        """Build request headers, omitting auth entirely when no key is set.

        A self-hosted OpenAI-compatible server (vLLM, TGI, Ollama) needs no
        key. Sending ``Authorization: Bearer`` with an empty value is not
        merely ignored — httpx rejects the bare ``"Bearer "`` outright with
        ``LocalProtocolError``, so every embed call fails against exactly the
        deployments most likely to be used without credentials.
        """
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _truncate(self, text: str) -> str:
        """Clip *text* to the model's input window.

        Truncating loses the tail of a long episode; refusing loses the whole
        turn. The first is a worse embedding, the second is an outage.
        """
        if self._max_input_chars <= 0 or len(text) <= self._max_input_chars:
            return text
        logger.debug(
            "embedding input truncated from %d to %d chars for model %s",
            len(text),
            self._max_input_chars,
            self._model,
        )
        return text[: self._max_input_chars]

    async def _post_embeddings(self, texts: list[str]) -> list[list[float]]:
        response = await self._get_client().post(
            f"{self._base_url}/embeddings",
            headers=self._headers(),
            json={"input": [self._truncate(t) for t in texts], "model": self._model},
        )
        response.raise_for_status()
        data = response.json()

        sorted_items = sorted(data["data"], key=lambda x: x["index"])
        vectors = [item["embedding"] for item in sorted_items]
        if vectors and self._dimension is None:
            self._dimension = len(vectors[0])
        return vectors

    # ------------------------------------------------------------------
    # EmbeddingPort
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        vectors = await self.embed_batch([text])
        return vectors[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await self._post_embeddings(texts)

    @property
    def dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        return _DEFAULT_DIMENSION
