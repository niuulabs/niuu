"""Tests for SentenceTransformerEmbeddingAdapter.

No real model is loaded — sentence_transformers is mocked at import level.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from niuu.adapters.embedding.sentence_transformer import (
    _DEFAULT_MODEL,
    SentenceTransformerEmbeddingAdapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeNpArray:
    """Minimal numpy-array-like object with a tolist() method."""

    def __init__(self, data: list[float]) -> None:
        self._data = data

    def tolist(self) -> list[float]:
        return list(self._data)


def _make_mock_model(dim: int = 384) -> MagicMock:
    """Build a mock SentenceTransformer model (no numpy required)."""
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = dim
    model.encode.side_effect = lambda texts, convert_to_numpy=True: [
        _FakeNpArray([0.0] * dim) for _ in texts
    ]
    return model


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSentenceTransformerEmbeddingAdapter:
    def test_default_model_name(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        assert adapter._model_name == _DEFAULT_MODEL

    def test_custom_model_name(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter("custom/model")
        assert adapter._model_name == "custom/model"

    def test_device_default_cpu(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        assert adapter._device == "cpu"

    def test_import_error_raised_if_not_installed(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        # Temporarily hide sentence_transformers
        original = sys.modules.get("sentence_transformers")
        sys.modules["sentence_transformers"] = None  # type: ignore[assignment]
        try:
            with pytest.raises(ImportError, match="sentence-transformers"):
                adapter._load_model()
        finally:
            if original is None:
                sys.modules.pop("sentence_transformers", None)
            else:
                sys.modules["sentence_transformers"] = original

    async def test_embed_returns_vector(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=4)
        adapter._model = mock_model

        vec = await adapter.embed("hello world")
        assert isinstance(vec, list)
        assert len(vec) == 4
        assert all(isinstance(x, float) for x in vec)

    async def test_embed_batch_calls_encode_once(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=4)
        adapter._model = mock_model

        vecs = await adapter.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        mock_model.encode.assert_called_once()

    async def test_embed_batch_empty(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=4)
        adapter._model = mock_model

        vecs = await adapter.embed_batch([])
        assert vecs == []

    async def test_dimension_from_loaded_model(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=384)
        adapter._model = mock_model

        assert adapter.dimension == 384

    async def test_dimension_cached_after_encode(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=8)
        adapter._model = mock_model

        await adapter.embed("test")
        assert adapter._dim == 8
        assert adapter.dimension == 8
        # Second call should not hit the model again for dimension.
        mock_model.get_sentence_embedding_dimension.assert_not_called()

    async def test_model_cached_after_first_load(self) -> None:
        adapter = SentenceTransformerEmbeddingAdapter()
        mock_model = _make_mock_model(dim=4)
        adapter._model = mock_model

        await adapter.embed("a")
        await adapter.embed("b")
        # Model is cached — encode should be called once per call, not re-loaded.
        assert mock_model.encode.call_count == 2
