"""Tests for niuu.adapters.search.sqlite — SqliteSearchAdapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import niuu.adapters.search.sqlite as sqlite_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ConstantEmbedFn:
    """Always returns the same vector regardless of input."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    async def __call__(self, text: str) -> list[float]:
        return list(self._vector)


class _DispatchEmbedFn:
    """Returns a specific vector per substring match, otherwise zeros."""

    def __init__(self, mapping: dict[str, list[float]], dim: int = 4) -> None:
        self._mapping = mapping
        self._dim = dim

    async def __call__(self, text: str) -> list[float]:
        for key, vec in self._mapping.items():
            if key in text:
                return list(vec)
        return [0.0] * self._dim


@pytest.fixture
def adapter(tmp_path: Path) -> sqlite_module.SqliteSearchAdapter:
    return sqlite_module.SqliteSearchAdapter(
        path=str(tmp_path / "search.db"),
        max_retries=3,
        min_jitter_ms=1.0,
        max_jitter_ms=5.0,
    )


@pytest.fixture
def hybrid_adapter(tmp_path: Path) -> sqlite_module.SqliteSearchAdapter:
    """Adapter with a constant embedding (same vector for all docs/queries)."""
    return sqlite_module.SqliteSearchAdapter(
        path=str(tmp_path / "search.db"),
        embed_fn=_ConstantEmbedFn([1.0, 0.0, 0.0, 0.0]),
        max_retries=3,
        min_jitter_ms=1.0,
        max_jitter_ms=5.0,
    )


# ---------------------------------------------------------------------------
# sqlite_module._sanitize_fts_query
# ---------------------------------------------------------------------------


class TestSanitizeFtsQuery:
    def test_basic_token(self) -> None:
        assert sqlite_module._sanitize_fts_query("python") == '"python"'

    def test_multiple_tokens(self) -> None:
        assert sqlite_module._sanitize_fts_query("run tests") == '"run" "tests"'

    def test_empty_query(self) -> None:
        assert sqlite_module._sanitize_fts_query("") == '""'

    def test_fts_operators_escaped(self) -> None:
        result = sqlite_module._sanitize_fts_query("NOT foo AND bar")
        # Each token wrapped in quotes → operators treated as literals
        assert '"NOT"' in result
        assert '"AND"' in result

    def test_double_quotes_escaped(self) -> None:
        result = sqlite_module._sanitize_fts_query('say "hello"')
        assert '""hello""' in result


# ---------------------------------------------------------------------------
# Index + FTS search (no embeddings)
# ---------------------------------------------------------------------------


class TestFtsSearch:
    @pytest.mark.asyncio
    async def test_has_documents_tracks_persisted_rows(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        assert await adapter.has_documents() is False
        await adapter.index("doc-1", "persisted content", {})
        assert await adapter.has_documents() is True

    @pytest.mark.asyncio
    async def test_index_and_find_by_keyword(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await adapter.index("doc-1", "python unit testing with pytest", {"source": "test"})
        results = await adapter.search("pytest")
        assert len(results) == 1
        assert results[0].id == "doc-1"

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        await adapter.index("doc-1", "python unit testing", {})
        results = await adapter.search("golang")
        assert results == []

    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await adapter.index("doc-1", "some content", {})
        results = await adapter.search("   ")
        assert results == []

    @pytest.mark.asyncio
    async def test_score_in_zero_one(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        await adapter.index("doc-1", "machine learning algorithms", {})
        await adapter.index("doc-2", "machine learning models for nlp", {})
        results = await adapter.search("machine learning")
        for r in results:
            assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_results_ordered_by_score_descending(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await adapter.index("doc-1", "python", {})
        await adapter.index("doc-2", "python python python", {})
        results = await adapter.search("python")
        assert len(results) == 2
        assert results[0].score >= results[1].score

    @pytest.mark.asyncio
    async def test_metadata_returned(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        meta: dict[str, Any] = {"author": "alice", "version": 2}
        await adapter.index("doc-1", "some searchable content", meta)
        results = await adapter.search("searchable")
        assert results[0].metadata == meta

    @pytest.mark.asyncio
    async def test_update_existing_document(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await adapter.index("doc-1", "old content", {"v": 1})
        await adapter.index("doc-1", "new updated content", {"v": 2})
        results = await adapter.search("updated")
        assert len(results) == 1
        assert results[0].metadata["v"] == 2
        # Old content should not be findable
        old_results = await adapter.search("old")
        assert old_results == []

    @pytest.mark.asyncio
    async def test_limit_respected(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        for i in range(5):
            await adapter.index(f"doc-{i}", f"python testing document {i}", {})
        results = await adapter.search("python", limit=2)
        assert len(results) <= 2

    @pytest.mark.asyncio
    async def test_remove_document(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        await adapter.index("doc-1", "removable document content", {})
        await adapter.remove("doc-1")
        results = await adapter.search("removable")
        assert results == []

    @pytest.mark.asyncio
    async def test_remove_nonexistent_is_noop(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await adapter.remove("no-such-doc")  # should not raise

    @pytest.mark.asyncio
    async def test_rebuild(self, adapter: sqlite_module.SqliteSearchAdapter) -> None:
        await adapter.index("doc-1", "rebuild test content", {})
        await adapter.rebuild()  # should not raise
        results = await adapter.search("rebuild")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_fts_only_mode_no_embed_fn(
        self, adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        """FTS-only mode returns functional results when no embed_fn is provided."""
        assert adapter._embed_fn is None
        await adapter.index("doc-1", "functional fts search without embeddings", {})
        results = await adapter.search("fts search")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Hybrid retrieval (FTS + semantic)
# ---------------------------------------------------------------------------


class TestHybridSearch:
    @pytest.mark.asyncio
    async def test_finds_by_keyword_with_embeddings(
        self, hybrid_adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        await hybrid_adapter.index("doc-1", "python pytest unit testing", {})
        results = await hybrid_adapter.search("pytest")
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_finds_by_semantics(self, tmp_path: Path) -> None:
        """Semantic search finds documents whose embedding matches the query."""
        # doc-1 has vector [1,0,0,0]; doc-2 has [0,1,0,0]
        # query for "alpha" → [1,0,0,0] → cosine sim with doc-1 = 1.0, doc-2 = 0.0
        mapping = {
            "alpha": [1.0, 0.0, 0.0, 0.0],
            "beta": [0.0, 1.0, 0.0, 0.0],
        }
        embed = _DispatchEmbedFn(mapping, dim=4)
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "sem.db"),
            embed_fn=embed,
        )
        await adapter.index("doc-alpha", "alpha related content", {})
        await adapter.index("doc-beta", "beta related content", {})
        results = await adapter.search("alpha query", limit=2)
        ids = [r.id for r in results]
        assert "doc-alpha" in ids

    @pytest.mark.asyncio
    async def test_precomputed_embedding_accepted(self, tmp_path: Path) -> None:
        """index() accepts a precomputed embedding and uses it for hybrid search."""
        embed_fn = _ConstantEmbedFn([0.0, 1.0, 0.0, 0.0])
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "pre.db"),
            embed_fn=embed_fn,
        )
        precomputed = [1.0, 0.0, 0.0, 0.0]
        await adapter.index("doc-1", "precomputed embedding test", {}, embedding=precomputed)
        # Search with embed_fn returning [1,0,0,0] → cosine sim = 1.0 with doc-1
        results = await adapter.search("precomputed")
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_hybrid_scores_in_zero_one(
        self, hybrid_adapter: sqlite_module.SqliteSearchAdapter
    ) -> None:
        for i in range(3):
            await hybrid_adapter.index(f"doc-{i}", f"content document number {i}", {})
        results = await hybrid_adapter.search("content document")
        for r in results:
            assert 0.0 <= r.score <= 1.0

    @pytest.mark.asyncio
    async def test_rrf_boosts_multi_ranked_doc(self, tmp_path: Path) -> None:
        """A doc ranked in both FTS and semantic wins over a doc ranked only in semantic."""
        # doc-a: matches keyword "shared" + semantic similar to query
        # doc-b: semantic only (no keyword match)
        mapping = {
            "shared": [1.0, 0.0, 0.0, 0.0],
            "semantic only": [1.0, 0.0, 0.0, 0.0],
            "test query": [1.0, 0.0, 0.0, 0.0],
        }
        embed = _DispatchEmbedFn(mapping, dim=4)
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "rrf.db"),
            embed_fn=embed,
            rrf_k=60,
        )
        # doc-a matches keyword AND gets semantic similarity
        await adapter.index("doc-a", "shared keyword content for testing", {})
        # doc-b does not match any keyword but has same embedding → semantic only
        await adapter.index("doc-b", "semantic only unrelated words", {})

        results = await adapter.search("shared test query", limit=5)
        ids_in_order = [r.id for r in results]
        # doc-a should appear (it has both FTS and semantic)
        assert "doc-a" in ids_in_order


# ---------------------------------------------------------------------------
# Native vector KNN (sqlite-vec)
# ---------------------------------------------------------------------------


def _hash_vector(text: str, dim: int = 8) -> list[float]:
    """Deterministic, normalised pseudo-embedding derived from a SHA-256 hash."""
    import hashlib
    import math

    digest = hashlib.sha256(text.encode("utf-8")).digest()
    raw = [b / 255.0 for b in digest[:dim]]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


class _HashEmbedFn:
    """Hash-based embeddings with an override vector for marker substrings."""

    def __init__(self, dim: int = 8, markers: dict[str, list[float]] | None = None) -> None:
        self._dim = dim
        self._markers = markers or {}

    async def __call__(self, text: str) -> list[float]:
        for marker, vector in self._markers.items():
            if marker in text:
                return list(vector)
        return _hash_vector(text, self._dim)


_TARGET_VEC = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


class TestVecKnnSearch:
    @pytest.mark.asyncio
    async def test_vec_path_enabled_with_package(self, tmp_path: Path) -> None:
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "vec.db"),
            embed_fn=_HashEmbedFn(),
        )
        assert adapter._vec_enabled is True
        await adapter.index("doc-1", "hello vector world", {})
        assert adapter._vec_dim == 8

    @pytest.mark.asyncio
    async def test_semantic_candidate_cap_removed(self, tmp_path: Path) -> None:
        """The oldest of 500+ embedded docs is found via native KNN.

        The legacy path only considered the 200 most recent rowids, so the
        first-indexed document was semantically invisible.  The fallback
        adapter over the same database proves the old behaviour (doc missing)
        while the sqlite-vec path finds it.
        """
        embed = _HashEmbedFn(markers={"zorgon": _TARGET_VEC, "needle": _TARGET_VEC})
        path = str(tmp_path / "cap.db")
        adapter = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed)

        # Oldest doc: semantically identical to the query, no keyword overlap.
        await adapter.index("doc-oldest", "zorgon prime artifact", {})
        for i in range(1, 501):
            await adapter.index(f"doc-{i}", f"filler document number {i} topic {i % 7}", {})

        results = await adapter.search("needle hunt", limit=5)
        assert results, "native KNN returned no results"
        assert results[0].id == "doc-oldest"

        # Same database, forced fallback path → capped at 200 recent docs.
        legacy = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed, use_sqlite_vec=False)
        legacy_results = await legacy.search("needle hunt", limit=5)
        assert all(r.id != "doc-oldest" for r in legacy_results)

    @pytest.mark.asyncio
    async def test_dim_change_rebuilds_vec_table(self, tmp_path: Path) -> None:
        """Swapping embedding models (dim change) rebuilds the disposable index."""
        path = str(tmp_path / "dim.db")
        adapter4 = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=_HashEmbedFn(dim=4))
        await adapter4.index("doc-a", "four dimensional alpha", {})
        await adapter4.index("doc-b", "four dimensional beta", {})
        assert adapter4._vec_dim == 4

        adapter16 = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=_HashEmbedFn(dim=16))
        await adapter16.index("doc-c", "sixteen dimensional gamma", {})
        assert adapter16._vec_dim == 16

        conn = adapter16._connect()
        try:
            meta = conn.execute(
                "SELECT value FROM search_index_vec_meta WHERE key = 'dim'"
            ).fetchone()
            count = conn.execute("SELECT count(*) FROM search_index_vec").fetchone()[0]
        finally:
            conn.close()
        assert meta["value"] == "16"
        # Only the 16-dim doc survives in the vec table; old-dim rows skipped.
        assert count == 1

        # Search keeps working after the swap (no corruption).
        results = await adapter16.search("sixteen dimensional gamma", limit=5)
        assert any(r.id == "doc-c" for r in results)
        # Old docs remain findable via the FTS arm.
        results = await adapter16.search("four dimensional alpha", limit=5)
        assert any(r.id == "doc-a" for r in results)

    @pytest.mark.asyncio
    async def test_reopen_restores_vec_state(self, tmp_path: Path) -> None:
        path = str(tmp_path / "reopen.db")
        embed = _HashEmbedFn(markers={"zorgon": _TARGET_VEC, "needle": _TARGET_VEC})
        first = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed)
        await first.index("doc-1", "zorgon prime artifact", {})

        reopened = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed)
        assert reopened._vec_dim == 8
        results = await reopened.search("needle hunt", limit=3)
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_stale_user_version_rebuilds_vec_index(self, tmp_path: Path) -> None:
        """A user_version bump drops and transparently rebuilds the vec index."""
        import sqlite3 as sqlite3_mod

        path = str(tmp_path / "stale.db")
        embed = _HashEmbedFn(markers={"zorgon": _TARGET_VEC, "needle": _TARGET_VEC})
        first = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed)
        await first.index("doc-1", "zorgon prime artifact", {})

        # Simulate a database written by an older adapter version.
        conn = sqlite3_mod.connect(path)
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        conn.close()

        reopened = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=embed)
        conn = reopened._connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        assert version != 0
        # The vec index was rebuilt from stored JSON embeddings.
        results = await reopened.search("needle hunt", limit=3)
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_remove_deletes_vec_row(self, tmp_path: Path) -> None:
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "rm.db"), embed_fn=_HashEmbedFn()
        )
        await adapter.index("doc-1", "ephemeral vector document", {})
        await adapter.remove("doc-1")

        conn = adapter._connect()
        try:
            count = conn.execute("SELECT count(*) FROM search_index_vec").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    @pytest.mark.asyncio
    async def test_reindex_without_embedding_clears_vec_row(self, tmp_path: Path) -> None:
        """Updating a doc to have no embedding removes its vec row."""
        adapter = sqlite_module.SqliteSearchAdapter(path=str(tmp_path / "clear.db"))  # no embed_fn
        await adapter.index("doc-1", "explicit embedding doc", {}, embedding=[1.0, 0.0, 0.0])
        await adapter.index("doc-1", "explicit embedding doc updated", {})

        conn = adapter._connect()
        try:
            count = conn.execute("SELECT count(*) FROM search_index_vec").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    @pytest.mark.asyncio
    async def test_rebuild_prunes_orphan_vec_rows(self, tmp_path: Path) -> None:
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "orphan.db"), embed_fn=_HashEmbedFn()
        )
        await adapter.index("doc-1", "orphan candidate document", {})

        # Bypass remove() to orphan the vec row.
        conn = adapter._connect()
        try:
            conn.execute("DELETE FROM search_index WHERE id = 'doc-1'")
            conn.commit()
        finally:
            conn.close()

        await adapter.rebuild()

        conn = adapter._connect()
        try:
            count = conn.execute("SELECT count(*) FROM search_index_vec").fetchone()[0]
        finally:
            conn.close()
        assert count == 0

    @pytest.mark.asyncio
    async def test_query_dim_mismatch_falls_back(self, tmp_path: Path) -> None:
        """A query vector with the wrong dimension uses the Python fallback."""
        path = str(tmp_path / "mismatch.db")
        adapter = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=_HashEmbedFn(dim=8))
        await adapter.index("doc-1", "dimension mismatch subject", {})

        # Same DB, but queries now embed at a different dimension.
        querier = sqlite_module.SqliteSearchAdapter(path=path, embed_fn=_HashEmbedFn(dim=4))
        results = await querier.search("dimension mismatch subject", limit=3)
        # FTS arm still finds the doc; no exception from the vec arm.
        assert any(r.id == "doc-1" for r in results)


# ---------------------------------------------------------------------------
# Degradation matrix
# ---------------------------------------------------------------------------


class TestDegradationMatrix:
    @pytest.mark.asyncio
    async def test_no_sqlite_vec_falls_back_and_warns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Without the sqlite-vec package: loud warning + JSON fallback works."""
        monkeypatch.setattr(sqlite_module, "sqlite_vec", None)

        embed = _HashEmbedFn(markers={"zorgon": _TARGET_VEC, "needle": _TARGET_VEC})
        with caplog.at_level("WARNING", logger="niuu.adapters.search.sqlite"):
            adapter = sqlite_module.SqliteSearchAdapter(
                path=str(tmp_path / "novec.db"), embed_fn=embed
            )

        assert adapter._vec_enabled is False
        assert any("sqlite-vec" in rec.message for rec in caplog.records)

        await adapter.index("doc-1", "zorgon prime artifact", {})
        results = await adapter.search("needle hunt", limit=3)
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_constructor_flag_disables_vec(self, tmp_path: Path) -> None:
        embed = _HashEmbedFn(markers={"zorgon": _TARGET_VEC, "needle": _TARGET_VEC})
        adapter = sqlite_module.SqliteSearchAdapter(
            path=str(tmp_path / "flag.db"), embed_fn=embed, use_sqlite_vec=False
        )
        assert adapter._vec_enabled is False

        await adapter.index("doc-1", "zorgon prime artifact", {})
        results = await adapter.search("needle hunt", limit=3)
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_extension_load_failure_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing extension load disables vec support with a loud warning."""
        import sqlite3

        class _BrokenVec:
            @staticmethod
            def load(conn: object) -> None:
                raise sqlite3.OperationalError("cannot load extension")

        monkeypatch.setattr(sqlite_module, "sqlite_vec", _BrokenVec)

        with caplog.at_level("WARNING", logger="niuu.adapters.search.sqlite"):
            adapter = sqlite_module.SqliteSearchAdapter(
                path=str(tmp_path / "broken.db"), embed_fn=_HashEmbedFn()
            )

        assert adapter._vec_enabled is False
        assert any("sqlite-vec" in rec.message for rec in caplog.records)

        await adapter.index("doc-1", "still searchable content", {})
        results = await adapter.search("searchable content", limit=3)
        assert any(r.id == "doc-1" for r in results)

    @pytest.mark.asyncio
    async def test_no_embed_fn_is_fts_only(self, tmp_path: Path) -> None:
        """Without embed_fn: FTS-only, no vec table is ever created."""
        adapter = sqlite_module.SqliteSearchAdapter(path=str(tmp_path / "fts.db"))
        await adapter.index("doc-1", "pure keyword document", {})
        results = await adapter.search("keyword document", limit=3)
        assert any(r.id == "doc-1" for r in results)
        assert adapter._vec_dim is None
