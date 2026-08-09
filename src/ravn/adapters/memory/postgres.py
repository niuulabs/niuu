"""PostgreSQL episodic memory adapter.

Uses asyncpg for connection pooling, tsvector/tsquery for full-text search,
and pgvector for embedding similarity search when the extension is available.
Designed for infra-mode deployments (e.g. on-cluster Kubernetes).

Search is delegated to ``PostgresSearchAdapter`` from ``niuu.adapters.search``
which manages its own ``niuu_search_index`` table.  Episode-specific scoring
(recency decay × outcome weight) is applied on top of the raw search scores.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from time import monotonic
from typing import Any

import asyncpg

from niuu.adapters.search.postgres import PostgresSearchAdapter
from niuu.ports.search import SearchPort
from ravn.adapters.memory.scoring import (
    _AVG_EPISODE_CHARS,
    _CHARS_PER_TOKEN,
    build_prefetch_context,
    build_session_summaries,
    combined_score,
    score_and_admit,
)
from ravn.domain.models import Episode, EpisodeMatch, Outcome, SessionSummary, SharedContext
from ravn.memory_telemetry import (
    RESULT_EMPTY,
    RESULT_ERROR,
    RESULT_HIT,
    record_funnel,
    record_injected_chars,
    record_memory_operation,
    result_for,
)
from ravn.ports.memory import MemoryPort

# Identifies this backend on every metric it emits.
_BACKEND = "postgres"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The scoring rule now lives in ``scoring`` alongside the admission loop that
# applies it, so both adapters share one definition. Re-exported under the
# historical name for existing importers.
_combined_score = combined_score


def _row_to_episode(row: asyncpg.Record | dict[str, Any]) -> Episode:
    """Convert an asyncpg Record (or compatible dict) to an Episode dataclass."""
    ts = row["timestamp"]
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    tools_used = row["tools_used"]
    if isinstance(tools_used, str):
        tools_used = json.loads(tools_used)

    tags = row["tags"]
    if isinstance(tags, str):
        tags = json.loads(tags)

    embedding_raw = row["embedding"]
    embedding: list[float] | None = None
    if embedding_raw is not None:
        if isinstance(embedding_raw, str):
            embedding = json.loads(embedding_raw)
        elif isinstance(embedding_raw, list):
            embedding = embedding_raw

    errors_raw = row.get("errors") if hasattr(row, "get") else None
    try:
        errors: list[str] = json.loads(errors_raw) if errors_raw else []
    except (json.JSONDecodeError, TypeError):
        errors = []

    return Episode(
        episode_id=row["episode_id"],
        session_id=row["session_id"],
        timestamp=ts,
        summary=row["summary"],
        task_description=row["task_description"],
        tools_used=list(tools_used),
        outcome=Outcome(row["outcome"]),
        tags=list(tags),
        embedding=embedding,
        reflection=row.get("reflection") if hasattr(row, "get") else None,
        errors=errors,
        cost_usd=row.get("cost_usd") if hasattr(row, "get") else None,
        duration_seconds=row.get("duration_seconds") if hasattr(row, "get") else None,
    )


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class PostgresMemoryAdapter(MemoryPort):
    """Episodic memory backed by PostgreSQL.

    Search is delegated to ``PostgresSearchAdapter`` which provides tsvector
    FTS-only or hybrid (tsvector + pgvector) retrieval via the shared ``niuu``
    search port.  Episode-specific scoring (recency decay × outcome weight) is
    applied on top of the raw search scores.

    Requires the schema created by migration 000025_ravn_episodes.up.sql.
    pgvector is detected at initialisation via the search adapter; if present,
    the extension is used for hybrid embedding-similarity queries.

    Example configuration (ravn.yaml)::

        memory:
          backend: postgres
          dsn: "postgresql://user:pass@localhost:5432/ravn"
          # or point to an env var:
          dsn_env: "RAVN_POSTGRES_DSN"
    """

    def __init__(
        self,
        dsn: str = "",
        *,
        dsn_env: str = "",
        pool_min_size: int = 1,
        pool_max_size: int = 5,
        prefetch_budget: int = 2000,
        prefetch_limit: int = 5,
        prefetch_min_relevance: float = 0.3,
        recency_half_life_days: float = 14.0,
        recency_floor: float = 0.5,
        session_search_truncate_chars: int = 100_000,
        rrf_k: int = 60,
        semantic_candidate_limit: int = 200,
        search_port: SearchPort | None = None,
        environment_id: str = "",
    ) -> None:
        resolved_dsn = os.environ.get(dsn_env, dsn) if dsn_env else dsn
        if not resolved_dsn:
            raise ValueError(
                "PostgreSQL DSN is required. Provide dsn= or set dsn_env= to an env var name."
            )

        self._dsn = resolved_dsn
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._prefetch_budget = prefetch_budget
        self._prefetch_limit = prefetch_limit
        self._prefetch_min_relevance = prefetch_min_relevance
        self._recency_half_life_days = recency_half_life_days
        self._recency_floor = recency_floor
        self._environment_id = environment_id
        self._session_search_truncate_chars = session_search_truncate_chars
        self._pool: asyncpg.Pool | None = None
        self._shared_context: SharedContext | None = None

        self._search: SearchPort = search_port or PostgresSearchAdapter(
            dsn=resolved_dsn,
            rrf_k=rrf_k,
            semantic_candidate_limit=semantic_candidate_limit,
            pool_min_size=pool_min_size,
            pool_max_size=pool_max_size,
        )

    @property
    def pgvector_available(self) -> bool:
        """True if the pgvector extension was detected at initialisation."""
        return getattr(self._search, "pgvector_available", False)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the connection pool, ensure tables exist, and detect pgvector.

        When the search adapter is a ``PostgresSearchAdapter``, the pool is
        shared via ``set_pool`` to avoid opening a second set of connections to
        the same database.
        """
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
        )
        if isinstance(self._search, PostgresSearchAdapter):
            self._search.set_pool(self._pool)
        await self._search.initialize()

    async def close(self) -> None:
        """Close the connection pool gracefully. Safe to call more than once."""
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.close()
        await self._search.close()

    # ------------------------------------------------------------------
    # MemoryPort implementation
    # ------------------------------------------------------------------

    async def record_episode(self, episode: Episode) -> None:
        started = monotonic()
        try:
            await self._record_episode(episode)
        except Exception:
            record_memory_operation(
                operation="record",
                backend=_BACKEND,
                result=RESULT_ERROR,
                seconds=monotonic() - started,
                environment_id=self._environment_id,
            )
            raise
        record_memory_operation(
            operation="record",
            backend=_BACKEND,
            result=RESULT_HIT,
            seconds=monotonic() - started,
            environment_id=self._environment_id,
        )

    async def _record_episode(self, episode: Episode) -> None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ravn_episodes
                    (episode_id, session_id, timestamp, summary,
                     task_description, tools_used, outcome, tags, embedding,
                     reflection, errors, cost_usd, duration_seconds)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (episode_id) DO UPDATE SET
                    session_id       = EXCLUDED.session_id,
                    timestamp        = EXCLUDED.timestamp,
                    summary          = EXCLUDED.summary,
                    task_description = EXCLUDED.task_description,
                    tools_used       = EXCLUDED.tools_used,
                    outcome          = EXCLUDED.outcome,
                    tags             = EXCLUDED.tags,
                    embedding        = EXCLUDED.embedding,
                    reflection       = EXCLUDED.reflection,
                    errors           = EXCLUDED.errors,
                    cost_usd         = EXCLUDED.cost_usd,
                    duration_seconds = EXCLUDED.duration_seconds
                """,
                episode.episode_id,
                episode.session_id,
                episode.timestamp,
                episode.summary,
                episode.task_description,
                episode.tools_used,
                episode.outcome.value,
                episode.tags,
                json.dumps(episode.embedding) if episode.embedding is not None else None,
                episode.reflection,
                json.dumps(episode.errors) if episode.errors else None,
                episode.cost_usd,
                episode.duration_seconds,
            )

        # Index the episode in the search adapter for future retrieval.
        content = f"{episode.task_description} {episode.summary} {' '.join(episode.tags)}"
        metadata = {
            "session_id": episode.session_id,
            "timestamp": episode.timestamp.isoformat(),
            "outcome": episode.outcome.value,
        }
        await self._search.index(
            episode.episode_id,
            content,
            metadata,
            embedding=episode.embedding,
        )

    async def query_episodes(
        self,
        query: str,
        *,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[EpisodeMatch]:
        if not query.strip():
            return []

        started = monotonic()
        # Get raw search results from the shared search adapter.
        search_results = await self._search.search(query, limit=limit * 3)

        if not search_results:
            record_funnel(
                backend=_BACKEND,
                candidates=0,
                admitted=0,
                scores=[],
                top_candidate_age_days=None,
                environment_id=self._environment_id,
            )
            record_memory_operation(
                operation="query",
                backend=_BACKEND,
                result=RESULT_EMPTY,
                seconds=monotonic() - started,
                environment_id=self._environment_id,
            )
            return []

        # Load full episode objects from ravn_episodes by ID.
        episode_ids = [r.id for r in search_results]
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT episode_id, session_id, timestamp, summary,
                       task_description, tools_used, outcome, tags, embedding,
                       reflection, errors, cost_usd, duration_seconds
                FROM ravn_episodes
                WHERE episode_id = ANY($1::text[])
                """,
                episode_ids,
            )

        episodes_by_id = {row["episode_id"]: _row_to_episode(row) for row in rows}

        # Apply episode-specific scoring: recency decay × outcome weight.
        matches = score_and_admit(
            search_results,
            episodes_by_id,
            half_life_days=self._recency_half_life_days,
            min_relevance=min_relevance,
            limit=limit,
            backend=_BACKEND,
            recency_floor=self._recency_floor,
            environment_id=self._environment_id,
        )
        record_memory_operation(
            operation="query",
            backend=_BACKEND,
            result=result_for(len(matches)),
            seconds=monotonic() - started,
            environment_id=self._environment_id,
        )
        return matches

    async def prefetch(self, context: str) -> str:
        if self._prefetch_limit == 0:
            return ""
        started = monotonic()
        matches = await self.query_episodes(
            context,
            limit=self._prefetch_limit,
            min_relevance=self._prefetch_min_relevance,
        )
        block = ""
        if matches:
            budget_chars = self._prefetch_budget * _CHARS_PER_TOKEN
            block = build_prefetch_context(matches, budget_chars)
        record_injected_chars(
            backend=_BACKEND, chars=len(block), environment_id=self._environment_id
        )
        record_memory_operation(
            operation="prefetch",
            backend=_BACKEND,
            result=result_for(len(block)),
            seconds=monotonic() - started,
            environment_id=self._environment_id,
        )
        return block

    async def count_episodes(self) -> int:
        """Return the total number of stored episodes."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS cnt FROM ravn_episodes")
        return int(row["cnt"]) if row else 0

    async def search_sessions(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[SessionSummary]:
        if not query.strip():
            return []

        # Use the search adapter for FTS and load full episodes for grouping.
        search_results = await self._search.search(
            query,
            limit=self._session_search_truncate_chars // _AVG_EPISODE_CHARS,
        )

        if not search_results:
            return []

        episode_ids = [r.id for r in search_results]
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT episode_id, session_id, timestamp, summary,
                       task_description, tools_used, outcome, tags, embedding,
                       reflection, errors, cost_usd, duration_seconds
                FROM ravn_episodes
                WHERE episode_id = ANY($1::text[])
                """,
                episode_ids,
            )

        if not rows:
            return []

        episodes = [_row_to_episode(row) for row in rows]
        return build_session_summaries(episodes, limit, self._session_search_truncate_chars)

    def inject_shared_context(self, context: SharedContext) -> None:
        self._shared_context = context

    def get_shared_context(self) -> SharedContext | None:
        return self._shared_context

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_pool(self) -> asyncpg.Pool:
        """Return the pool, raising RuntimeError if not initialized."""
        if self._pool is None:
            raise RuntimeError("PostgresMemoryAdapter not initialized. Call initialize() first.")
        return self._pool
