"""PostgreSQL-backed search adapter using tsvector FTS and pgvector.

Uses asyncpg for connection pooling, ``tsvector``/``tsquery`` for full-text
search, and the pgvector extension for embedding similarity search when the
extension is available.

When pgvector is present **and** an ``embed_fn`` is provided, the adapter uses
hybrid retrieval (tsvector + pgvector similarity merged via RRF).  Embeddings
are written to a native ``vector(dim)`` column (``embedding_vec``, created
lazily once the dimension is known) and the semantic arm runs as a single
``ORDER BY embedding_vec <=> $1`` KNN query over *all* embedded rows.  Without
pgvector, embeddings stay as JSON text and cosine similarity is computed in
Python over the most recent ``semantic_candidate_limit`` rows.  Without
``embed_fn`` the adapter falls back to tsvector-only search.

The adapter manages its own table (``niuu_search_index``) and uses
``CREATE TABLE IF NOT EXISTS`` so it co-exists safely with other tables.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import asyncpg

from niuu.adapters.search.rrf import cosine_similarity, reciprocal_rank_fusion
from niuu.ports.search import SearchPort, SearchResult

logger = logging.getLogger(__name__)

# Default RRF smoothing constant.
_DEFAULT_RRF_K = 60

# How many recent embedded documents to consider for semantic search on the
# JSON + Python-cosine fallback path (when pgvector is unavailable).  The
# native pgvector KNN path is NOT capped — it considers every embedded row.
_DEFAULT_SEMANTIC_CANDIDATE_LIMIT = 200

# Schema: the ``search_vector`` column is auto-updated by a trigger so that FTS
# is always current.  The ``embedding`` column stores JSON-encoded float arrays;
# a pgvector ``vector`` column is added conditionally after the extension check.
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS niuu_search_index (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    embedding   TEXT,
    search_vector TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);

CREATE INDEX IF NOT EXISTS niuu_search_index_fts
    ON niuu_search_index USING GIN(search_vector);
"""


class PostgresSearchAdapter(SearchPort):
    """Full-text and semantic search backed by PostgreSQL.

    Uses ``tsvector``/``websearch_to_tsquery`` for keyword search and pgvector
    (if the extension is available) for semantic similarity.

    Args:
        dsn: asyncpg connection DSN.
        embed_fn: Async callable that maps a text string to its embedding
            vector.  When provided and pgvector is available, hybrid retrieval
            is used.  Otherwise falls back to tsvector-only search.
        rrf_k: RRF smoothing constant (default 60).
        semantic_candidate_limit: Maximum number of candidate documents for
            semantic re-ranking.
        pool_min_size: Minimum asyncpg pool connections.
        pool_max_size: Maximum asyncpg pool connections.
    """

    def __init__(
        self,
        dsn: str,
        *,
        embed_fn: Callable[[str], Awaitable[list[float]]] | None = None,
        rrf_k: int = _DEFAULT_RRF_K,
        semantic_candidate_limit: int = _DEFAULT_SEMANTIC_CANDIDATE_LIMIT,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ) -> None:
        if not dsn:
            raise ValueError("PostgresSearchAdapter requires a non-empty DSN.")
        self._dsn = dsn
        self._embed_fn = embed_fn
        self._rrf_k = rrf_k
        self._semantic_candidate_limit = semantic_candidate_limit
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._owns_pool: bool = False
        self._pgvector_available: bool = False
        self._vector_column_dim: int | None = None

    def set_pool(self, pool: asyncpg.Pool) -> None:
        """Inject an externally-managed pool to avoid duplicate connections.

        When called before ``initialize()``, the adapter skips pool creation
        and uses *pool* instead.  The caller remains responsible for closing it.
        """
        self._pool = pool
        self._owns_pool = False

    @property
    def pgvector_available(self) -> bool:
        """True if the pgvector extension was detected at initialisation."""
        return self._pgvector_available

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the connection pool (if not already shared), ensure the table
        exists, and detect pgvector."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=self._pool_min_size,
                max_size=self._pool_max_size,
            )
            self._owns_pool = True
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)
        self._pgvector_available = await self._detect_pgvector()
        if self._pgvector_available:
            await self._sync_vector_column_state()
        if not self._pgvector_available and self._embed_fn is not None:
            logger.warning(
                "pgvector extension is not installed — semantic search falls "
                "back to JSON embeddings with Python cosine similarity capped "
                "at %d candidates; install pgvector for native KNN",
                self._semantic_candidate_limit,
            )

    async def close(self) -> None:
        """Close the connection pool gracefully (only if this adapter owns it)."""
        if self._owns_pool and self._pool is not None:
            await self._pool.close()
        self._pool = None
        self._owns_pool = False

    # ------------------------------------------------------------------
    # SearchPort implementation
    # ------------------------------------------------------------------

    async def index(
        self,
        id: str,
        content: str,
        metadata: dict[str, Any],
        *,
        embedding: list[float] | None = None,
    ) -> None:
        """Index a document, computing its embedding if ``embed_fn`` is set.

        If *embedding* is supplied it is stored directly, bypassing
        ``embed_fn``.  This allows callers with pre-computed embeddings to
        avoid redundant model inference.
        """  # noqa: D401
        resolved_embedding = embedding
        if resolved_embedding is None and self._embed_fn is not None:
            resolved_embedding = await self._embed_fn(content)

        if resolved_embedding is not None and self._pgvector_available:
            await self._ensure_vector_column(len(resolved_embedding))

        pool = self._require_pool()
        embedding_json = json.dumps(resolved_embedding) if resolved_embedding is not None else None

        if self._vector_column_dim is not None:
            vector_matches_column = (
                resolved_embedding is not None
                and len(resolved_embedding) == self._vector_column_dim
            )
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO niuu_search_index (id, content, metadata, embedding, embedding_vec)
                    VALUES ($1, $2, $3, $4, $5::vector)
                    ON CONFLICT (id) DO UPDATE SET
                        content       = EXCLUDED.content,
                        metadata      = EXCLUDED.metadata,
                        embedding     = EXCLUDED.embedding,
                        embedding_vec = EXCLUDED.embedding_vec
                    """,
                    id,
                    content,
                    json.dumps(metadata),
                    embedding_json,
                    embedding_json if vector_matches_column else None,
                )
            return

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO niuu_search_index (id, content, metadata, embedding)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (id) DO UPDATE SET
                    content   = EXCLUDED.content,
                    metadata  = EXCLUDED.metadata,
                    embedding = EXCLUDED.embedding
                """,
                id,
                content,
                json.dumps(metadata),
                embedding_json,
            )

    async def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        if not query.strip():
            return []

        use_hybrid = self._embed_fn is not None and self._pgvector_available
        if use_hybrid:
            return await self._search_hybrid(query, limit=limit)
        return await self._search_fts(query, limit=limit)

    async def remove(self, id: str) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM niuu_search_index WHERE id = $1", id)

    async def rebuild(self) -> None:
        """No-op for Postgres: tsvector is a generated column, always consistent."""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("PostgresSearchAdapter not initialized. Call initialize() first.")
        return self._pool

    async def _detect_pgvector(self) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        return row is not None

    @staticmethod
    async def _fetch_vector_column_dim(conn: asyncpg.Connection) -> int | None:
        """Return the declared dimension of ``embedding_vec``, or None if absent."""
        typmod = await conn.fetchval(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = 'niuu_search_index'::regclass
              AND attname = 'embedding_vec'
              AND NOT attisdropped
            """
        )
        if typmod is None or typmod <= 0:
            return None
        return int(typmod)

    async def _sync_vector_column_state(self) -> None:
        """Adopt an existing ``embedding_vec`` column, or create one from
        already-stored JSON embeddings (upgrade path)."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            dim = await self._fetch_vector_column_dim(conn)
            if dim is not None:
                self._vector_column_dim = dim
                return
            sample = await conn.fetchval(
                "SELECT embedding FROM niuu_search_index WHERE embedding IS NOT NULL LIMIT 1"
            )
        if sample is None:
            return
        await self._ensure_vector_column(len(json.loads(sample)))

    async def _ensure_vector_column(self, dim: int) -> None:
        """Create the ``vector(dim)`` column, rebuilding it on dimension change.

        The vector column is disposable derived data: on a dimension change
        (model swap) it is dropped and recreated, then backfilled from the
        JSON ``embedding`` column where dimensions allow.
        """
        if not self._pgvector_available:
            return
        if self._vector_column_dim == dim:
            return

        pool = self._require_pool()
        async with pool.acquire() as conn:
            existing = await self._fetch_vector_column_dim(conn)
            if existing == dim:
                self._vector_column_dim = dim
                return
            if existing is not None:
                logger.warning(
                    "embedding dimension changed (%d → %d) — rebuilding the "
                    "pgvector column from stored embeddings",
                    existing,
                    dim,
                )
                await conn.execute(
                    "ALTER TABLE niuu_search_index DROP COLUMN IF EXISTS embedding_vec"
                )
            await conn.execute(
                "ALTER TABLE niuu_search_index "
                f"ADD COLUMN IF NOT EXISTS embedding_vec vector({int(dim)})"
            )
            await self._backfill_vector_column(conn, dim)
            await self._create_vector_index(conn)
        self._vector_column_dim = dim

    @staticmethod
    async def _backfill_vector_column(conn: asyncpg.Connection, dim: int) -> None:
        """Backfill ``embedding_vec`` from JSON embeddings of matching dimension."""
        try:
            await conn.execute(
                """
                UPDATE niuu_search_index
                SET embedding_vec = embedding::vector
                WHERE embedding_vec IS NULL
                  AND embedding IS NOT NULL
                  AND json_array_length(embedding::json) = $1
                """,
                dim,
            )
        except asyncpg.PostgresError as exc:
            logger.warning(
                "pgvector backfill skipped (%s) — rows re-embed on the next reindex pass",
                exc,
            )

    @staticmethod
    async def _create_vector_index(conn: asyncpg.Connection) -> None:
        try:
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS niuu_search_index_vec
                    ON niuu_search_index USING hnsw (embedding_vec vector_cosine_ops)
                """
            )
        except asyncpg.PostgresError as exc:
            logger.warning(
                "could not create HNSW index on embedding_vec (%s) — KNN "
                "queries fall back to sequential scan",
                exc,
            )

    async def _search_fts(self, query: str, *, limit: int) -> list[SearchResult]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', $1) AS tsq)
                SELECT
                    id, content, metadata,
                    ts_rank(search_vector, q.tsq) AS rank_score
                FROM niuu_search_index, q
                WHERE search_vector @@ q.tsq
                ORDER BY rank_score DESC
                LIMIT $2
                """,
                query,
                limit,
            )

        if not rows:
            return []

        rank_scores = [float(r["rank_score"]) for r in rows]
        max_rank = max(rank_scores) if rank_scores else 1.0

        results: list[SearchResult] = []
        for row, raw_rank in zip(rows, rank_scores):
            normalised = raw_rank / max_rank if max_rank > 0 else 0.0
            meta: dict[str, Any] = json.loads(row["metadata"])
            results.append(
                SearchResult(
                    id=row["id"],
                    content=row["content"],
                    score=normalised,
                    metadata=meta,
                )
            )
        return results

    def _use_vector_column_for_query(self, query_vec: list[float]) -> bool:
        """True when the native pgvector KNN arm can serve this query vector."""
        if not self._pgvector_available or self._vector_column_dim is None:
            return False
        if len(query_vec) == self._vector_column_dim:
            return True
        logger.warning(
            "query embedding dimension (%d) does not match the pgvector "
            "column dimension (%d) — falling back to Python cosine similarity",
            len(query_vec),
            self._vector_column_dim,
        )
        return False

    async def _search_hybrid(self, query: str, *, limit: int) -> list[SearchResult]:
        """Hybrid retrieval: tsvector + semantic similarity merged via RRF.

        When pgvector is available the semantic leg is a single native KNN
        query (``ORDER BY embedding_vec <=> $1``) over all embedded rows.
        Without pgvector, embeddings are loaded as JSON text and cosine
        similarity is computed in Python over the most recent
        ``semantic_candidate_limit`` rows.
        """
        assert self._embed_fn is not None

        pool = self._require_pool()

        # FTS candidates.
        async with pool.acquire() as conn:
            fts_rows = await conn.fetch(
                """
                WITH q AS (SELECT websearch_to_tsquery('english', $1) AS tsq)
                SELECT id, content, metadata,
                       ts_rank(search_vector, q.tsq) AS rank_score
                FROM niuu_search_index, q
                WHERE search_vector @@ q.tsq
                ORDER BY rank_score DESC
                LIMIT $2
                """,
                query,
                limit * 3,
            )

        # Embed the query once.
        query_vec = await self._embed_fn(query)

        # Semantic candidates — native KNN on the vector column when present.
        sem_doc_map: dict[str, asyncpg.Record] = {}
        if self._use_vector_column_for_query(query_vec):
            # Format the query vector as a pgvector literal: [v1,v2,...].
            # json.dumps produces the same bracket-comma format pgvector accepts.
            query_vec_str = json.dumps(query_vec)
            async with pool.acquire() as conn:
                sem_rows = await conn.fetch(
                    """
                    SELECT id, content, metadata
                    FROM niuu_search_index
                    WHERE embedding_vec IS NOT NULL
                    ORDER BY embedding_vec <=> $1::vector
                    LIMIT $2
                    """,
                    query_vec_str,
                    limit * 3,
                )
            sem_ranking = [r["id"] for r in sem_rows]
            sem_doc_map = {r["id"]: r for r in sem_rows}
        else:
            # Fall back: load embeddings as JSON text and rank in Python.
            async with pool.acquire() as conn:
                emb_rows = await conn.fetch(
                    """
                    SELECT id, content, metadata, embedding
                    FROM niuu_search_index
                    WHERE embedding IS NOT NULL
                    ORDER BY id
                    LIMIT $1
                    """,
                    self._semantic_candidate_limit,
                )
            sem_scored: list[tuple[str, float]] = []
            for row in emb_rows:
                raw = row["embedding"]
                if raw is None:
                    continue
                emb: list[float] = json.loads(raw) if isinstance(raw, str) else list(raw)
                sim = cosine_similarity(query_vec, emb)
                sem_scored.append((row["id"], sim))
            sem_scored.sort(key=lambda x: x[1], reverse=True)
            sem_scored = sem_scored[: limit * 3]
            sem_ranking = [doc_id for doc_id, _ in sem_scored]
            sem_doc_map = {r["id"]: r for r in emb_rows}

        fts_ranking = [r["id"] for r in fts_rows]
        all_ids = list(dict.fromkeys(fts_ranking + sem_ranking))

        if not all_ids:
            return []

        rrf_scores = reciprocal_rank_fusion(
            [fts_ranking, sem_ranking] if sem_ranking else [fts_ranking],
            k=self._rrf_k,
        )
        max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0

        # Build lookup from fetched rows.
        doc_map: dict[str, asyncpg.Record] = {}
        for r in fts_rows:
            doc_map[r["id"]] = r
        for id_, row in sem_doc_map.items():
            doc_map.setdefault(id_, row)

        results: list[SearchResult] = []
        for doc_id, raw_score in rrf_scores.items():
            row = doc_map.get(doc_id)
            if row is None:
                continue
            meta: dict[str, Any] = json.loads(row["metadata"])
            results.append(
                SearchResult(
                    id=doc_id,
                    content=row["content"],
                    score=raw_score / max_rrf,
                    metadata=meta,
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]
