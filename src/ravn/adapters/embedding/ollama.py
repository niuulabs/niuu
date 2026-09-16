"""Ollama local embedding adapter.

Calls Ollama's ``/api/embed`` endpoint using ``httpx``.  Requires a running
Ollama instance (``ollama serve``) with the embedding model pulled.

A single persistent ``httpx.AsyncClient`` is reused across calls to avoid
the overhead of creating a new TCP connection pool for each request.
Call ``await adapter.close()`` when done to release the connection pool.

Example::

    adapter = OllamaEmbeddingAdapter(model="nomic-embed-text")
    vector = await adapter.embed("some text")
    await adapter.close()
"""

from __future__ import annotations

import logging

import httpx

from ravn.ports.embedding import EmbeddingPort

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "nomic-embed-text"
_DEFAULT_BASE_URL = "http://localhost:11434"
# nomic-embed-text default dimension
_DEFAULT_DIMENSION = 768

#: Characters per token, used to turn a token budget into a character budget without pulling in a
#: tokenizer for the model. English prose runs ~4; 3.0 is deliberately conservative, because the
#: cost of guessing high is a hard 400 and the cost of guessing low is a slightly shorter input.
_CHARS_PER_TOKEN = 3.0


class OllamaEmbeddingAdapter(EmbeddingPort):
    """Embedding adapter using a locally-running Ollama instance.

    Args:
        model: Ollama model name (must support embeddings).
        base_url: URL of the Ollama API server.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 30.0,
        max_input_chars: int | None = None,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._dimension: int | None = None
        self._client: httpx.AsyncClient | None = None
        #: Explicit override; when None the budget is discovered from the model and cached.
        self._max_input_chars = max_input_chars
        self._context_tokens: int | None = None

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

    async def _context_length(self) -> int:
        """The model's context length in tokens, asked once and cached.

        Configure max_input_chars explicitly for servers without model metadata.
        Discovery failures propagate so a broken configured service remains visible.
        """
        if self._context_tokens is not None:
            return self._context_tokens
        response = await self._get_client().post(
            f"{self._base_url}/api/show", json={"model": self._model}
        )
        response.raise_for_status()
        info = response.json().get("model_info") or {}
        for key, value in info.items():
            if key.endswith(".context_length") and type(value) is int and value > 0:
                self._context_tokens = value
                return value
        raise ValueError(
            f"Ollama model {self._model!r} has no context length; configure max_input_chars"
        )

    async def _budget_chars(self) -> int:
        if self._max_input_chars is not None:
            return self._max_input_chars
        return int(await self._context_length() * _CHARS_PER_TOKEN)

    async def _post_embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Send all *texts* in a single /api/embed request (batch input).

        Inputs are bounded to the model's context first. Ollama rejects an over-long input with a
        hard 400 (`the input length exceeds the context length`), and because embedding happens
        inside memory prefetch, that 400 used to propagate out and kill the whole conversation
        turn — one long earlier message was enough to stop an agent answering at all.

        Truncation is the honest trade here: an embedding of the first N characters still retrieves
        usefully, whereas no embedding means no turn.
        """
        budget = await self._budget_chars()
        bounded: list[str] = []
        for text in texts:
            if len(text) > budget:
                logger.warning(
                    "embed: input of %d chars exceeds the %s budget of %d — truncating",
                    len(text),
                    self._model,
                    budget,
                )
                bounded.append(text[:budget])
            else:
                bounded.append(text)

        response = await self._get_client().post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": bounded},
        )
        response.raise_for_status()
        data = response.json()
        embeddings: list[list[float]] = data["embeddings"]
        if self._dimension is None and embeddings:
            self._dimension = len(embeddings[0])
        return embeddings

    # ------------------------------------------------------------------
    # EmbeddingPort
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        results = await self._post_embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._post_embed_batch(texts)

    @property
    def dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        return _DEFAULT_DIMENSION
