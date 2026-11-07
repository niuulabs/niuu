"""Tests for hybrid retrieval: cosine_similarity, reciprocal_rank_fusion,
and SqliteMemoryAdapter with an EmbeddingPort.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from niuu.ports.embedding import EmbeddingPort
from ravn.adapters.memory.scoring import cosine_similarity, reciprocal_rank_fusion
from ravn.adapters.memory.sqlite import SqliteMemoryAdapter
from ravn.domain.models import Episode, Outcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConstantEmbeddingAdapter(EmbeddingPort):
    """Always returns the same vector regardless of input text."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    async def embed(self, text: str) -> list[float]:
        return self._vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector for _ in texts]

    @property
    def dimension(self) -> int:
        return len(self._vector)


class _DispatchEmbeddingAdapter(EmbeddingPort):
    """Returns a specific vector per query substring match, otherwise zeros."""

    def __init__(self, mapping: dict[str, list[float]], default_dim: int = 4) -> None:
        self._mapping = mapping
        self._default_dim = default_dim

    async def embed(self, text: str) -> list[float]:
        for key, vec in self._mapping.items():
            if key in text:
                return vec
        return [0.0] * self._default_dim

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        return self._default_dim


def _ep(
    episode_id: str = "ep-1",
    session_id: str = "sess-1",
    summary: str = "ran the test suite",
    task_description: str = "run tests",
    tools_used: list[str] | None = None,
    outcome: Outcome = Outcome.SUCCESS,
    tags: list[str] | None = None,
    timestamp: datetime | None = None,
    embedding: list[float] | None = None,
) -> Episode:
    ep = Episode(
        episode_id=episode_id,
        session_id=session_id,
        timestamp=timestamp or datetime.now(UTC),
        summary=summary,
        task_description=task_description,
        tools_used=tools_used or ["bash"],
        outcome=outcome,
        tags=tags or ["shell"],
    )
    ep.embedding = embedding
    return ep


@pytest.fixture
async def hybrid_mem(tmp_path: Path) -> SqliteMemoryAdapter:
    """Adapter with a constant embedding port (same vector for everything)."""
    emb = _ConstantEmbeddingAdapter([1.0, 0.0, 0.0, 0.0])
    adapter = SqliteMemoryAdapter(
        path=str(tmp_path / "memory.db"),
        prefetch_min_relevance=0.0,
        embedding_port=emb,
        rrf_k=60,
        semantic_candidate_limit=20,
    )
    await adapter.initialize()
    return adapter


# ---------------------------------------------------------------------------
# cosine_similarity
# ---------------------------------------------------------------------------


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        v = [1.0, 0.0, 0.0]
        assert cosine_similarity(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert cosine_similarity(a, b) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self) -> None:
        a = [0.0, 0.0, 0.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_mismatched_length_returns_zero(self) -> None:
        a = [1.0, 2.0]
        b = [1.0, 2.0, 3.0]
        assert cosine_similarity(a, b) == pytest.approx(0.0)

    def test_empty_vectors_returns_zero(self) -> None:
        assert cosine_similarity([], []) == pytest.approx(0.0)

    def test_unit_vector_similarity(self) -> None:
        a = [1.0, 1.0]
        b = [1.0, 1.0]
        assert cosine_similarity(a, b) == pytest.approx(1.0)

    def test_partial_overlap(self) -> None:
        a = [1.0, 0.0]
        b = [1.0, 1.0]
        # cos(45°) = 1/sqrt(2)
        expected = 1.0 / math.sqrt(2)
        assert cosine_similarity(a, b) == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# reciprocal_rank_fusion
# ---------------------------------------------------------------------------


class TestReciprocalRankFusion:
    def test_single_ranking(self) -> None:
        scores = reciprocal_rank_fusion([["a", "b", "c"]], k=60)
        assert scores["a"] > scores["b"] > scores["c"]

    def test_two_rankings_boosts_shared(self) -> None:
        r1 = ["a", "b"]
        r2 = ["a", "c"]
        scores = reciprocal_rank_fusion([r1, r2], k=60)
        # "a" appears in both lists at rank 0 → highest score
        assert scores["a"] > scores["b"]
        assert scores["a"] > scores["c"]

    def test_empty_rankings(self) -> None:
        scores = reciprocal_rank_fusion([], k=60)
        assert scores == {}

    def test_single_empty_list(self) -> None:
        scores = reciprocal_rank_fusion([[]], k=60)
        assert scores == {}

    def test_rrf_formula(self) -> None:
        # Single item at rank 0 with k=60: score = 1/(60+0+1) = 1/61
        scores = reciprocal_rank_fusion([["x"]], k=60)
        assert scores["x"] == pytest.approx(1.0 / 61)

    def test_k_affects_scores(self) -> None:
        low_k = reciprocal_rank_fusion([["a", "b"]], k=1)
        high_k = reciprocal_rank_fusion([["a", "b"]], k=100)
        # With low k, top rank is more advantaged.
        low_gap = low_k["a"] - low_k["b"]
        high_gap = high_k["a"] - high_k["b"]
        assert low_gap > high_gap

    def test_disjoint_rankings(self) -> None:
        r1 = ["a", "b"]
        r2 = ["c", "d"]
        scores = reciprocal_rank_fusion([r1, r2], k=60)
        assert set(scores.keys()) == {"a", "b", "c", "d"}


# ---------------------------------------------------------------------------
# SqliteMemoryAdapter with hybrid retrieval
# ---------------------------------------------------------------------------


class TestHybridRetrieval:
    async def test_hybrid_returns_matches(self, hybrid_mem: SqliteMemoryAdapter) -> None:
        await hybrid_mem.record_episode(_ep(summary="ran unit tests"))
        matches = await hybrid_mem.query_episodes("unit tests", min_relevance=0.0)
        assert len(matches) >= 1

    async def test_hybrid_no_embedding_stored(self, tmp_path: Path) -> None:
        """Hybrid query works even if stored episodes have no embedding."""
        emb = _ConstantEmbeddingAdapter([1.0, 0.0])
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=emb,
        )
        await adapter.initialize()
        ep = _ep(summary="configure nginx", embedding=None)
        await adapter.record_episode(ep)
        matches = await adapter.query_episodes("nginx", min_relevance=0.0)
        assert isinstance(matches, list)

    async def test_hybrid_with_stored_embeddings(self, tmp_path: Path) -> None:
        """Episode with stored embedding + matching query embedding gets boosted."""
        target_vec = [1.0, 0.0, 0.0, 0.0]
        other_vec = [0.0, 1.0, 0.0, 0.0]

        emb = _DispatchEmbeddingAdapter({"semantic": target_vec, "noise": other_vec}, default_dim=4)
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=emb,
            rrf_k=60,
            semantic_candidate_limit=20,
        )
        await adapter.initialize()

        target_ep = _ep(
            episode_id="target",
            summary="semantic search implementation",
            embedding=target_vec,
        )
        noise_ep = _ep(
            episode_id="noise",
            summary="noise test noise episode",
            embedding=other_vec,
        )
        await adapter.record_episode(target_ep)
        await adapter.record_episode(noise_ep)

        matches = await adapter.query_episodes("semantic", min_relevance=0.0)
        assert any(m.episode.episode_id == "target" for m in matches)

    async def test_hybrid_respects_limit(self, tmp_path: Path) -> None:
        emb = _ConstantEmbeddingAdapter([1.0, 0.0])
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=emb,
        )
        await adapter.initialize()
        for i in range(8):
            await adapter.record_episode(
                _ep(episode_id=f"e{i}", summary="python debugging session")
            )
        matches = await adapter.query_episodes("python", limit=3, min_relevance=0.0)
        assert len(matches) <= 3

    async def test_hybrid_empty_db_returns_empty(self, hybrid_mem: SqliteMemoryAdapter) -> None:
        matches = await hybrid_mem.query_episodes("anything", min_relevance=0.0)
        assert matches == []

    async def test_fts_fallback_without_embedding_port(self, tmp_path: Path) -> None:
        """Without embedding_port, adapter uses FTS5-only search."""
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=None,
        )
        await adapter.initialize()
        await adapter.record_episode(_ep(summary="deployed to staging"))
        matches = await adapter.query_episodes("deployed", min_relevance=0.0)
        assert len(matches) >= 1

    async def test_outcome_weighting_in_hybrid(self, tmp_path: Path) -> None:
        vec = [1.0, 0.0]
        emb = _ConstantEmbeddingAdapter(vec)
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=emb,
        )
        await adapter.initialize()

        success_ep = _ep(
            episode_id="success",
            summary="deployed successfully",
            outcome=Outcome.SUCCESS,
            embedding=vec,
        )
        failure_ep = _ep(
            episode_id="failure",
            summary="deployed with errors",
            outcome=Outcome.FAILURE,
            embedding=vec,
        )
        await adapter.record_episode(success_ep)
        await adapter.record_episode(failure_ep)

        matches = await adapter.query_episodes("deployed", min_relevance=0.0)
        success_match = next((m for m in matches if m.episode.episode_id == "success"), None)
        failure_match = next((m for m in matches if m.episode.episode_id == "failure"), None)

        if success_match and failure_match:
            assert success_match.relevance > failure_match.relevance

    async def test_recency_weighting_in_hybrid(self, tmp_path: Path) -> None:
        vec = [1.0, 0.0]
        emb = _ConstantEmbeddingAdapter(vec)
        adapter = SqliteMemoryAdapter(
            path=str(tmp_path / "memory.db"),
            prefetch_min_relevance=0.0,
            embedding_port=emb,
            recency_half_life_days=14.0,
        )
        await adapter.initialize()

        recent_ep = _ep(
            episode_id="recent",
            summary="fixed the auth bug",
            timestamp=datetime.now(UTC),
            embedding=vec,
        )
        old_ep = _ep(
            episode_id="old",
            summary="fixed the auth bug",
            timestamp=datetime.now(UTC) - timedelta(days=90),
            embedding=vec,
        )
        await adapter.record_episode(recent_ep)
        await adapter.record_episode(old_ep)

        matches = await adapter.query_episodes("auth bug", min_relevance=0.0)
        recent_match = next((m for m in matches if m.episode.episode_id == "recent"), None)
        old_match = next((m for m in matches if m.episode.episode_id == "old"), None)

        if recent_match and old_match:
            assert recent_match.relevance > old_match.relevance
