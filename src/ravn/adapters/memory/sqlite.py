"""SQLite episodic memory adapter.

Uses WAL mode for concurrent access, FTS5 for full-text search, and BM25
ranking for relevance.  Designed for low-resource environments (e.g. Pi).

Search is delegated to ``SqliteSearchAdapter`` from ``niuu.adapters.search``
which manages its own ``search_index`` / ``search_index_fts`` tables inside
the same database file.  When an ``EmbeddingPort`` is provided, hybrid
retrieval (FTS5 + cosine similarity merged via RRF) is used automatically.

Retry strategy: up to ``max_retries`` attempts on SQLite "database is locked"
errors with random jitter in [min_jitter_ms, max_jitter_ms] between attempts.
WAL passive checkpoint fires every ``checkpoint_interval`` writes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic

from niuu.adapters.search.sqlite import SqliteSearchAdapter
from ravn.adapters.memory.scoring import (
    _AVG_EPISODE_CHARS,
    _CHARS_PER_TOKEN,
    build_prefetch_context,
    build_session_summaries,
    combined_score,
    score_and_admit,
)
from ravn.domain.models import (
    Episode,
    EpisodeMatch,
    Outcome,
    SessionSummary,
    SharedContext,
)
from ravn.memory_telemetry import (
    RESULT_EMPTY,
    RESULT_ERROR,
    RESULT_HIT,
    record_corpus,
    record_funnel,
    record_injected_chars,
    record_memory_operation,
    result_for,
)
from ravn.ports.embedding import EmbeddingPort
from ravn.ports.memory import MemoryPort

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)

# Identifies this backend on every metric it emits.
_BACKEND = "sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    episode_id      TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    task_description TEXT NOT NULL,
    tools_used      TEXT NOT NULL,
    outcome         TEXT NOT NULL,
    tags            TEXT NOT NULL,
    embedding       TEXT,
    reflection      TEXT,
    errors          TEXT,
    cost_usd        REAL,
    duration_seconds REAL
);
"""

# ALTER TABLE statements applied once to existing databases.
_MIGRATIONS = [
    "ALTER TABLE episodes ADD COLUMN reflection TEXT",
    "ALTER TABLE episodes ADD COLUMN errors TEXT",
    "ALTER TABLE episodes ADD COLUMN cost_usd REAL",
    "ALTER TABLE episodes ADD COLUMN duration_seconds REAL",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_episode(row: sqlite3.Row) -> Episode:
    """Convert a database row to an Episode dataclass."""
    ts_str: str = row["timestamp"]
    try:
        ts = datetime.fromisoformat(ts_str)
    except ValueError:
        ts = datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    tools: list[str] = json.loads(row["tools_used"])
    tags: list[str] = json.loads(row["tags"])
    emb_raw = row["embedding"]
    embedding: list[float] | None = json.loads(emb_raw) if emb_raw else None

    errors_raw = row["errors"] if "errors" in row.keys() else None
    errors: list[str] = json.loads(errors_raw) if errors_raw else []

    return Episode(
        episode_id=row["episode_id"],
        session_id=row["session_id"],
        timestamp=ts,
        summary=row["summary"],
        task_description=row["task_description"],
        tools_used=tools,
        outcome=Outcome(row["outcome"]),
        tags=tags,
        embedding=embedding,
        reflection=row["reflection"] if "reflection" in row.keys() else None,
        errors=errors,
        cost_usd=row["cost_usd"] if "cost_usd" in row.keys() else None,
        duration_seconds=row["duration_seconds"] if "duration_seconds" in row.keys() else None,
    )


# The scoring rule now lives in ``scoring`` alongside the admission loop that
# applies it, so both adapters share one definition. Re-exported under the
# historical name for existing importers.
_combined_score = combined_score


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class SqliteMemoryAdapter(MemoryPort):
    """Episodic memory backed by a local SQLite database.

    Search is delegated to ``SqliteSearchAdapter`` which provides FTS5-only or
    hybrid (FTS5 + semantic) retrieval via the shared ``niuu`` search port.
    Episode-specific scoring (recency decay × outcome weight) is applied on
    top of the raw search scores returned by the search adapter.

    When *embedding_port* is supplied, ``query_episodes`` uses hybrid
    retrieval (FTS5 + cosine similarity merged via RRF).  Without an
    embedding port the adapter falls back to FTS5-only search.
    """

    def __init__(
        self,
        path: str = "~/.ravn/memory.db",
        *,
        max_retries: int = 15,
        min_jitter_ms: float = 20.0,
        max_jitter_ms: float = 150.0,
        checkpoint_interval: int = 50,
        prefetch_budget: int = 2000,
        prefetch_limit: int = 5,
        prefetch_min_relevance: float = 0.3,
        recency_half_life_days: float = 14.0,
        recency_floor: float = 0.5,
        session_search_truncate_chars: int = 100_000,
        embedding_port: EmbeddingPort | None = None,
        rrf_k: int = 60,
        semantic_candidate_limit: int = 50,
        corpus_stats_interval_seconds: float = 300.0,
        environment_id: str = "",
    ) -> None:
        self._path = Path(path).expanduser()
        self._max_retries = max_retries
        self._min_jitter_ms = min_jitter_ms
        self._max_jitter_ms = max_jitter_ms
        self._checkpoint_interval = checkpoint_interval
        self._prefetch_budget = prefetch_budget
        self._prefetch_limit = prefetch_limit
        self._prefetch_min_relevance = prefetch_min_relevance
        self._recency_half_life_days = recency_half_life_days
        self._recency_floor = recency_floor
        self._environment_id = environment_id
        self._session_search_truncate_chars = session_search_truncate_chars
        self._embedding_port = embedding_port
        self._corpus_stats_interval_seconds = corpus_stats_interval_seconds
        self._last_corpus_sample_at: float | None = None
        self._write_count = 0
        self._shared_context: SharedContext | None = None

        embed_fn = None
        if embedding_port is not None:
            embed_fn = embedding_port.embed

        self._search = SqliteSearchAdapter(
            path=str(self._path),
            embed_fn=embed_fn,
            rrf_k=rrf_k,
            semantic_candidate_limit=semantic_candidate_limit,
            max_retries=max_retries,
            min_jitter_ms=min_jitter_ms,
            max_jitter_ms=max_jitter_ms,
            checkpoint_interval=checkpoint_interval,
        )
        self._init_db()

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the database directory and tables if they do not exist."""
        await asyncio.to_thread(self._init_db)

    async def record_episode(self, episode: Episode) -> None:
        started = monotonic()
        try:
            await asyncio.to_thread(self._record_episode_sync, episode)
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
        await self._maybe_emit_corpus_gauges()

    async def query_episodes(
        self,
        query: str,
        *,
        limit: int = 5,
        min_relevance: float = 0.3,
    ) -> list[EpisodeMatch]:
        started = monotonic()
        try:
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
                )
                return []

            # Load full episode objects from the episodes table by ID.
            episode_ids = [r.id for r in search_results]
            episodes_by_id = await asyncio.to_thread(self._load_episodes_by_ids_sync, episode_ids)

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
        except Exception:
            record_memory_operation(
                operation="query",
                backend=_BACKEND,
                result=RESULT_ERROR,
                seconds=monotonic() - started,
                environment_id=self._environment_id,
            )
            raise
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

    async def _maybe_emit_corpus_gauges(self) -> None:
        """Sample corpus-health gauges at most once per configured interval.

        Coverage ratios only change slowly, so sampling on write keeps them
        current without a dedicated scheduler or a per-call table scan.
        """
        if self._corpus_stats_interval_seconds <= 0:
            return
        now = monotonic()
        if (
            self._last_corpus_sample_at is not None
            and now - self._last_corpus_sample_at < self._corpus_stats_interval_seconds
        ):
            return
        self._last_corpus_sample_at = now
        try:
            episodes, embedded, indexed = await asyncio.to_thread(self._corpus_stats_sync)
        except sqlite3.Error:
            logger.warning("Corpus gauge sampling failed.", exc_info=True)
            return
        if episodes == 0:
            record_corpus(backend=_BACKEND, episodes=0, embedding_coverage=0.0, index_coverage=0.0)
            return
        record_corpus(
            backend=_BACKEND,
            episodes=episodes,
            embedding_coverage=embedded / episodes,
            index_coverage=min(1.0, indexed / episodes),
            environment_id=self._environment_id,
        )

    async def count_episodes(self) -> int:
        """Return the total number of stored episodes."""
        return await asyncio.to_thread(self._count_episodes_sync)

    async def search_sessions(
        self,
        query: str,
        *,
        limit: int = 3,
    ) -> list[SessionSummary]:
        if not query.strip():
            return []

        search_results = await self._search.search(
            query,
            limit=self._session_search_truncate_chars // _AVG_EPISODE_CHARS,
        )

        if not search_results:
            return []

        episode_ids = [r.id for r in search_results]
        episodes_by_id = await asyncio.to_thread(self._load_episodes_by_ids_sync, episode_ids)
        episodes = [episodes_by_id[eid] for eid in episode_ids if eid in episodes_by_id]
        return build_session_summaries(episodes, limit, self._session_search_truncate_chars)

    def inject_shared_context(self, context: SharedContext) -> None:
        self._shared_context = context

    def get_shared_context(self) -> SharedContext | None:
        return self._shared_context

    # ------------------------------------------------------------------
    # Synchronous internals (run via to_thread)
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            # Apply migrations for existing databases (idempotent — ignore if column exists).
            for stmt in _MIGRATIONS:
                try:
                    conn.execute(stmt)
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # Column already exists
        finally:
            conn.close()

    def _with_retry(self, fn, *args):
        """Execute *fn* with retry on SQLite locked errors."""
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(self._max_retries):
            try:
                return fn(*args)
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower():
                    raise
                last_exc = exc
                if attempt < self._max_retries - 1:
                    jitter = random.uniform(self._min_jitter_ms, self._max_jitter_ms) / 1000.0
                    time.sleep(jitter)
        raise last_exc

    def _record_episode_sync(self, episode: Episode) -> None:
        def _do_insert() -> None:
            conn = self._connect()
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO episodes
                        (episode_id, session_id, timestamp, summary,
                         task_description, tools_used, outcome, tags, embedding,
                         reflection, errors, cost_usd, duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode.episode_id,
                        episode.session_id,
                        episode.timestamp.isoformat(),
                        episode.summary,
                        episode.task_description,
                        json.dumps(episode.tools_used),
                        episode.outcome.value,
                        json.dumps(episode.tags),
                        json.dumps(episode.embedding) if episode.embedding else None,
                        episode.reflection,
                        json.dumps(episode.errors) if episode.errors else None,
                        episode.cost_usd,
                        episode.duration_seconds,
                    ),
                )
                conn.commit()
                self._write_count += 1
                if self._write_count % self._checkpoint_interval == 0:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                conn.close()

        self._with_retry(_do_insert)

    def _load_episodes_by_ids_sync(self, ids: list[str]) -> dict[str, Episode]:
        """Load full episode rows for a list of IDs."""
        if not ids:
            return {}

        placeholders = ",".join("?" for _ in ids)

        def _do() -> dict[str, Episode]:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT * FROM episodes WHERE episode_id IN ({placeholders})",
                    ids,
                ).fetchall()
            finally:
                conn.close()
            return {row["episode_id"]: _row_to_episode(row) for row in rows}

        return self._with_retry(_do)

    def _corpus_stats_sync(self) -> tuple[int, int, int]:
        """Return ``(episodes, episodes_with_embedding, indexed_documents)``.

        ``indexed_documents`` counts rows the search adapter owns; a shortfall
        against ``episodes`` means part of the corpus is unreachable by any
        query regardless of how it is scored.
        """

        def _do_stats() -> tuple[int, int, int]:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*), COUNT(embedding) FROM episodes").fetchone()
                episodes = int(row[0]) if row else 0
                embedded = int(row[1]) if row else 0
                try:
                    index_row = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()
                    indexed = int(index_row[0]) if index_row else 0
                except sqlite3.OperationalError:
                    indexed = 0
                return episodes, embedded, indexed
            except sqlite3.OperationalError:
                return 0, 0, 0
            finally:
                conn.close()

        return self._with_retry(_do_stats)

    def _count_episodes_sync(self) -> int:
        def _do_count() -> int:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()
                return int(row[0]) if row else 0
            except sqlite3.OperationalError:
                return 0
            finally:
                conn.close()

        return self._with_retry(_do_count)
