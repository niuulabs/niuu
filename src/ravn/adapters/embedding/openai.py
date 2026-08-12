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
# tokenizer for any of them. This bound is therefore optimistic, not safe —
# measured against Qwen3-Embedding-0.6B (8192-token window), the chars that
# actually fit vary by an order of magnitude with content:
#
#     english prose   47948 chars   5.85 chars/token
#     python code     37722 chars   4.60
#     json payload    15177 chars   1.85
#     base64          10993 chars   1.34
#     japanese        10087 chars   1.23
#     hex/uuid soup    8204 chars   1.00
#
# 24k assumes ~2.9 and so holds for prose and code but not for the dense
# machine-generated text a resident actually gets fed: Ivaldi's laevateinn
# fleet-status JSON truncated to exactly 24000 chars tokenizes to 8193 — one
# token over — and lost 26 autonomous turns in five hours to that single token.
#
# Lowering this to the safe ratio is not the answer either: 1.0 chars/token
# would clip prose at 8k where 48k fits, throwing away most of the recall
# query on every ordinary turn to protect against the rare dense one. So this
# stays optimistic and `_clip_to_context_window` is the safety net — see there.
_DEFAULT_MAX_INPUT_CHARS = 24_000

# A 400 that means "too long" rather than "malformed". vLLM says "the model's
# context length is only 8192"; OpenAI sets code `context_length_exceeded`.
_CONTEXT_OVERFLOW_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum input length",
)


def _is_context_overflow(response: httpx.Response) -> bool:
    """Is this 400 the model refusing over-long input, or a real bad request?

    Only the former is worth retrying shorter; retrying a malformed request
    would just hide it behind a second identical failure.
    """
    if response.status_code != httpx.codes.BAD_REQUEST:
        return False
    try:
        error = response.json().get("error", "")
    except ValueError:
        return "context length" in response.text.lower()
    if isinstance(error, dict):
        error = f"{error.get('code', '')} {error.get('message', '')}"
    return any(marker in str(error).lower() for marker in _CONTEXT_OVERFLOW_MARKERS)


class OpenAIEmbeddingAdapter(EmbeddingPort):
    """Embedding adapter using OpenAI's embeddings API.

    Args:
        api_key: OpenAI API key.  When empty the ``OPENAI_API_KEY`` env var is used.
        model: Embedding model name.
        base_url: Base URL for the OpenAI (or compatible) API.
        timeout: HTTP request timeout in seconds.
        max_input_chars: Longest input sent per text on the first attempt.
            Longer text is truncated rather than rejected — see
            ``_DEFAULT_MAX_INPUT_CHARS``. 0 sends every text whole. Either way
            input that still overflows the window is re-sent at the safe
            bound rather than failing the caller.
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
        self._context_tokens: int | None = None
        self._context_tokens_probed = False
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

    async def _discover_context_tokens(self) -> int | None:
        """Read the model's real context window from ``/v1/models``.

        vLLM reports ``max_model_len`` there. Asking the server beats any
        constant in this file, and mirrors how ``dimension`` is learned from
        the embeddings response rather than assumed.

        The 400 itself cannot be used for this: the server reports the count
        it clipped to, not the count it received — 45k, 100k and 200k chars of
        the same text all come back as "you passed 8193 input tokens" — so the
        error says the window was exceeded but never by how much.
        """
        if self._context_tokens_probed:
            return self._context_tokens
        response = await self._get_client().get(f"{self._base_url}/models", headers=self._headers())
        response.raise_for_status()
        for entry in response.json().get("data", []):
            if entry.get("id") == self._model:
                window = entry.get("max_model_len")
                self._context_tokens = int(window) if window else None
                break
        self._context_tokens_probed = True
        return self._context_tokens

    async def _clip_to_context_window(
        self, texts: list[str], refusal: httpx.HTTPStatusError
    ) -> list[str]:
        """Re-clip *texts* to a length that cannot overflow the window.

        No chars-per-token ratio is involved, because every ratio is wrong for
        some content. A token is built from at least one character, so N
        characters can never tokenize to more than N tokens: clipping to
        ``max_model_len`` *characters* is arithmetic, not an estimate, and
        holds for prose, base64, Japanese and UUID soup alike.

        It is also lossy — it is the floor, roughly a sixth of what prose
        could have used — which is why it applies only after the optimistic
        bound has already been refused, and never to input that fit.
        """
        try:
            window = await self._discover_context_tokens()
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"{self._model} refused oversized embedding input, and its context window "
                f"could not be read from {self._base_url}/models ({exc}), so no safe input "
                f"length can be derived. Set max_input_chars on the embedding adapter to a "
                f"bound this model accepts."
            ) from refusal
        if window is None:
            raise RuntimeError(
                f"{self._model} refused oversized embedding input, and {self._base_url}/models "
                f"does not report max_model_len for it, so no safe input length can be derived. "
                f"Set max_input_chars on the embedding adapter to a bound this model accepts."
            ) from refusal
        if all(len(text) <= window for text in texts):
            raise RuntimeError(
                f"{self._model} refused embedding input that is already within its "
                f"{window}-token window per text, so the overflow is not one text being too "
                f"long — a server that counts a batch against one window would do this. "
                f"Reduce the embedding batch size."
            ) from refusal
        logger.info(
            "embedding input overflowed %s's %d-token window; re-sending clipped to %d chars",
            self._model,
            window,
            window,
        )
        return [text[:window] for text in texts]

    async def _send(self, texts: list[str]) -> list[list[float]]:
        response = await self._get_client().post(
            f"{self._base_url}/embeddings",
            headers=self._headers(),
            json={"input": texts, "model": self._model},
        )
        response.raise_for_status()
        data = response.json()

        sorted_items = sorted(data["data"], key=lambda x: x["index"])
        vectors = [item["embedding"] for item in sorted_items]
        if vectors and self._dimension is None:
            self._dimension = len(vectors[0])
        return vectors

    async def _post_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Embed *texts*, surviving input the optimistic bound misjudged.

        One retry, at a length that cannot overflow — so this cannot loop, and
        a turn is never lost to a tokenizer ratio being wrong for one episode.
        """
        clipped = [self._truncate(text) for text in texts]
        try:
            return await self._send(clipped)
        except httpx.HTTPStatusError as exc:
            if not _is_context_overflow(exc.response):
                raise
            return await self._send(await self._clip_to_context_window(clipped, exc))

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
