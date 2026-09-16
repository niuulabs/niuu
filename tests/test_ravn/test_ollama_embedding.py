"""Tests for OllamaEmbeddingAdapter — all HTTP mocked with respx."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from ravn.adapters.embedding.ollama import (
    _DEFAULT_BASE_URL,
    _DEFAULT_DIMENSION,
    _DEFAULT_MODEL,
    OllamaEmbeddingAdapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embed_response(embeddings: list[list[float]]) -> dict:
    return {"model": _DEFAULT_MODEL, "embeddings": embeddings}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOllamaEmbeddingAdapter:
    def test_defaults(self) -> None:
        adapter = OllamaEmbeddingAdapter()
        assert adapter._model == _DEFAULT_MODEL
        assert adapter._base_url == _DEFAULT_BASE_URL
        assert adapter.dimension == _DEFAULT_DIMENSION

    def test_custom_model_and_url(self) -> None:
        adapter = OllamaEmbeddingAdapter(
            model="mxbai-embed-large",
            base_url="http://my-ollama:11434",
        )
        assert adapter._model == "mxbai-embed-large"
        assert "my-ollama" in adapter._base_url

    @respx.mock
    async def test_embed_returns_vector(self) -> None:
        respx.post("http://localhost:11434/api/show").mock(
            return_value=Response(200, json={"model_info": {"nomic.context_length": 2048}})
        )
        vec = [0.1, 0.2, 0.3, 0.4]
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=Response(200, json=_make_embed_response([vec]))
        )
        adapter = OllamaEmbeddingAdapter()
        result = await adapter.embed("hello world")
        assert result == pytest.approx(vec)

    @respx.mock
    async def test_embed_updates_dimension(self) -> None:
        respx.post("http://localhost:11434/api/show").mock(
            return_value=Response(200, json={"model_info": {"nomic.context_length": 2048}})
        )
        vec = [0.0] * 512
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=Response(200, json=_make_embed_response([vec]))
        )
        adapter = OllamaEmbeddingAdapter()
        await adapter.embed("text")
        assert adapter.dimension == 512

    @respx.mock
    async def test_embed_batch_calls_embed_per_text(self) -> None:
        respx.post("http://localhost:11434/api/show").mock(
            return_value=Response(200, json={"model_info": {"nomic.context_length": 2048}})
        )
        vec = [0.5, 0.5]
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=Response(200, json=_make_embed_response([vec, vec, vec]))
        )
        adapter = OllamaEmbeddingAdapter()
        result = await adapter.embed_batch(["a", "b", "c"])
        assert len(result) == 3
        assert result[0] == pytest.approx(vec)

    @respx.mock
    async def test_embed_batch_empty(self) -> None:
        adapter = OllamaEmbeddingAdapter()
        result = await adapter.embed_batch([])
        assert result == []

    @respx.mock
    async def test_http_error_propagates(self) -> None:
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=Response(500, json={"error": "Internal Server Error"})
        )
        adapter = OllamaEmbeddingAdapter()
        with pytest.raises(Exception):
            await adapter.embed("text")

    @respx.mock
    async def test_base_url_trailing_slash_stripped(self) -> None:
        respx.post("http://localhost:11434/api/show").mock(
            return_value=Response(200, json={"model_info": {"nomic.context_length": 2048}})
        )
        vec = [0.1, 0.2]
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=Response(200, json=_make_embed_response([vec]))
        )
        adapter = OllamaEmbeddingAdapter(base_url="http://localhost:11434/")
        result = await adapter.embed("test")
        assert result == pytest.approx(vec)
