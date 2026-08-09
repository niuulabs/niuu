"""Backfilling embeddings onto a corpus built before they were enabled.

Turning embeddings on only affects new writes. noatun held 32,156 indexed
episodes with no vectors, so a conversational query returned nothing while
keyword queries worked — the corpus was there, only unreachable semantically.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.domain.models import Episode, Outcome


class FakeEmbedding:
    """Deterministic embedding port that records what it was asked to embed."""

    def __init__(self, dim: int = 8, fail_after: int | None = None) -> None:
        self._dim = dim
        self._fail_after = fail_after
        self.embedded: list[str] = []

    async def embed(self, text: str) -> list[float]:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if self._fail_after is not None and len(self.embedded) >= self._fail_after:
            raise RuntimeError("embedding endpoint refused")
        self.embedded.extend(texts)
        return [[float(len(t) % 7)] * self._dim for t in texts]

    @property
    def dimension(self) -> int:
        return self._dim


def _episode(n: int) -> Episode:
    return Episode(
        episode_id=f"ep-{n}",
        session_id="s",
        timestamp=datetime.now(UTC),
        summary=f"summary {n}",
        task_description=f"task {n} about unhealthy pods",
        tools_used=[],
        outcome=Outcome.SUCCESS,
        tags=["ops"],
    )


async def _corpus_without_vectors(tmp_path, count: int) -> str:
    """Build a store the way a resident does before embeddings are switched on."""
    path = str(tmp_path / "memory.db")
    adapter = SqliteMemoryAdapter(path=path)
    await adapter.initialize()
    for n in range(count):
        await adapter.record_episode(_episode(n))
    return path


class TestBackfill:
    async def test_backfills_a_corpus_indexed_without_vectors(self, tmp_path) -> None:
        path = await _corpus_without_vectors(tmp_path, 5)
        embedder = FakeEmbedding()
        adapter = SqliteMemoryAdapter(path=path, embedding_port=embedder)

        assert len(await adapter._search.unembedded(limit=100)) == 5

        embedded, remaining = await adapter.backfill_embeddings(batch_size=2)

        assert embedded == 5
        assert remaining == 0
        assert len(embedder.embedded) == 5

    async def test_is_idempotent(self, tmp_path) -> None:
        """A second run finds nothing left and embeds nothing."""
        path = await _corpus_without_vectors(tmp_path, 3)
        adapter = SqliteMemoryAdapter(path=path, embedding_port=FakeEmbedding())
        await adapter.backfill_embeddings(batch_size=10)

        embedder = FakeEmbedding()
        adapter2 = SqliteMemoryAdapter(path=path, embedding_port=embedder)
        embedded, remaining = await adapter2.backfill_embeddings(batch_size=10)

        assert (embedded, remaining) == (0, 0)
        assert embedder.embedded == []

    async def test_max_documents_bounds_the_run(self, tmp_path) -> None:
        path = await _corpus_without_vectors(tmp_path, 6)
        adapter = SqliteMemoryAdapter(path=path, embedding_port=FakeEmbedding())

        embedded, remaining = await adapter.backfill_embeddings(batch_size=2, max_documents=4)

        assert embedded == 4
        assert remaining == 1  # unembedded(limit=1) reports "some are left"

    async def test_a_refusing_endpoint_raises_and_keeps_finished_batches(self, tmp_path) -> None:
        """Partial progress must survive so the run is resumable.

        Swallowing this would leave a half-embedded corpus reporting success.
        """
        path = await _corpus_without_vectors(tmp_path, 6)
        embedder = FakeEmbedding(fail_after=2)
        adapter = SqliteMemoryAdapter(path=path, embedding_port=embedder)

        with pytest.raises(RuntimeError, match="refused"):
            await adapter.backfill_embeddings(batch_size=2)

        # The first batch committed; the rest are still waiting.
        assert len(await adapter._search.unembedded(limit=100)) == 4

    async def test_requires_an_embedding_port(self, tmp_path) -> None:
        path = await _corpus_without_vectors(tmp_path, 1)
        adapter = SqliteMemoryAdapter(path=path)

        with pytest.raises(RuntimeError, match="requires an embedding port"):
            await adapter.backfill_embeddings()

    async def test_misaligned_vector_count_is_refused(self, tmp_path) -> None:
        """A backend returning the wrong number of vectors must not be written."""

        class Misaligned(FakeEmbedding):
            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.0] * self._dim]

        path = await _corpus_without_vectors(tmp_path, 4)
        adapter = SqliteMemoryAdapter(path=path, embedding_port=Misaligned())

        with pytest.raises(RuntimeError, match="misaligned"):
            await adapter.backfill_embeddings(batch_size=4)

    async def test_backfilled_vectors_are_used_by_search(self, tmp_path) -> None:
        path = await _corpus_without_vectors(tmp_path, 3)
        adapter = SqliteMemoryAdapter(path=path, embedding_port=FakeEmbedding())
        await adapter.backfill_embeddings(batch_size=10)

        assert await adapter._search.unembedded(limit=10) == []
        assert await adapter.query_episodes("unhealthy pods", limit=3, min_relevance=0.0)
