"""Sentence-transformers embedding adapter — local, Pi-friendly.

Uses ``all-MiniLM-L6-v2`` by default: 384-dimensional, ~80 MB, runs on CPU.
Requires the optional dependency: ``pip install sentence-transformers``.

The model is loaded lazily on first use and cached for the lifetime of the
adapter instance.
"""

from __future__ import annotations

import asyncio
from typing import Any

from niuu.ports.embedding import EmbeddingPort

_DEFAULT_MODEL = "all-MiniLM-L6-v2"


class SentenceTransformerEmbeddingAdapter(EmbeddingPort):
    """Local embedding adapter backed by sentence-transformers.

    Args:
        model_name: HuggingFace model name or local path.
        device: Torch device string (``"cpu"``, ``"cuda"``, etc.).
    """

    def __init__(
        self,
        model_name: str = _DEFAULT_MODEL,
        *,
        device: str = "cpu",
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._model: Any | None = None
        self._dim: int | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is required for SentenceTransformerEmbeddingAdapter. "
                "Install it with: pip install sentence-transformers"
            ) from exc
        self._model = SentenceTransformer(self._model_name, device=self._device)
        return self._model

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        vectors = model.encode(texts, convert_to_numpy=True)
        result = [v.tolist() for v in vectors]
        if self._dim is None and result:
            self._dim = len(result[0])
        return result

    # ------------------------------------------------------------------
    # EmbeddingPort
    # ------------------------------------------------------------------

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._encode_sync, texts)

    @property
    def dimension(self) -> int:
        if self._dim is not None:
            return self._dim
        model = self._load_model()
        dim = model.get_sentence_embedding_dimension()
        self._dim = dim
        return dim
