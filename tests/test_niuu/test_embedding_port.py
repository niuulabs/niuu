"""Tests for the EmbeddingPort interface and concrete adapters.

All network and model calls are mocked — no real models or HTTP required.
"""

from __future__ import annotations

from niuu.ports.embedding import EmbeddingPort

# ---------------------------------------------------------------------------
# Test double
# ---------------------------------------------------------------------------


class FakeEmbeddingAdapter(EmbeddingPort):
    """Minimal in-memory embedding adapter for testing."""

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self._calls: list[str] = []

    async def embed(self, text: str) -> list[float]:
        self._calls.append(text)
        return [float(i) / self._dim for i in range(self._dim)]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


# ---------------------------------------------------------------------------
# Interface contract tests
# ---------------------------------------------------------------------------


class TestEmbeddingPortInterface:
    async def test_embed_returns_list_of_floats(self) -> None:
        adapter = FakeEmbeddingAdapter(dim=4)
        vec = await adapter.embed("hello world")
        assert isinstance(vec, list)
        assert all(isinstance(x, float) for x in vec)
        assert len(vec) == 4

    async def test_embed_batch_returns_list_per_text(self) -> None:
        adapter = FakeEmbeddingAdapter(dim=8)
        vecs = await adapter.embed_batch(["foo", "bar", "baz"])
        assert len(vecs) == 3
        for v in vecs:
            assert len(v) == 8

    async def test_dimension_property(self) -> None:
        adapter = FakeEmbeddingAdapter(dim=384)
        assert adapter.dimension == 384

    async def test_embed_single_vs_batch_consistent(self) -> None:
        adapter = FakeEmbeddingAdapter(dim=4)
        single = await adapter.embed("test text")
        batch = await adapter.embed_batch(["test text"])
        assert single == batch[0]

    async def test_embed_batch_empty_list(self) -> None:
        adapter = FakeEmbeddingAdapter(dim=4)
        result = await adapter.embed_batch([])
        assert result == []

    async def test_fake_adapter_tracks_calls(self) -> None:
        adapter = FakeEmbeddingAdapter()
        await adapter.embed("first")
        await adapter.embed("second")
        assert "first" in adapter._calls
        assert "second" in adapter._calls
