"""Shared scoring helpers for episodic memory adapters.

Both ``SqliteMemoryAdapter`` and ``PostgresMemoryAdapter`` use identical
scoring constants, recency/formatting helpers, and response-building logic.
This module is the single source of truth for those shared pieces.

Hybrid retrieval primitives (cosine similarity, RRF) live in
``niuu.adapters.search.rrf`` and are re-exported here for backwards
compatibility with any existing importers.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from niuu.adapters.search.rrf import cosine_similarity, reciprocal_rank_fusion
from niuu.ports.search import SearchResult
from ravn.domain.models import Episode, EpisodeMatch, Outcome, SessionSummary
from ravn.memory_telemetry import record_funnel

# Re-export so existing ``from ravn.adapters.memory.scoring import ...`` calls work.
__all__ = [
    "cosine_similarity",
    "reciprocal_rank_fusion",
    "build_prefetch_context",
    "build_session_summaries",
    "combined_score",
    "score_and_admit",
    "_CHARS_PER_TOKEN",
    "_AVG_EPISODE_CHARS",
    "_OUTCOME_WEIGHTS",
]

# Approximate chars per token for budget estimation.
_CHARS_PER_TOKEN = 4

# Estimated average character length of a single episode row, used to compute
# the FTS LIMIT when searching for sessions.
_AVG_EPISODE_CHARS = 200

# Outcome weights for combined scoring.
_OUTCOME_WEIGHTS: dict[str, float] = {
    Outcome.SUCCESS: 1.0,
    Outcome.PARTIAL: 0.7,
    Outcome.FAILURE: 0.3,
}


def _recency_score(
    timestamp: datetime,
    half_life_days: float,
    floor: float = 0.0,
) -> float:
    """Compute exponential decay recency in ``[floor, 1]`` given episode age.

    Without a floor this decays to zero, and because the caller *multiplies*
    it into the combined score, age stops being a ranking signal and becomes a
    filter: at a 14-day half-life nothing older than roughly 24 days can clear
    a ``min_relevance`` of 0.3 at any relevance. The floor keeps recency
    ordering results without annihilating them, matching the ``recency_floor``
    that Mímir's ranking layer already applies.
    """
    now = datetime.now(UTC)
    ts = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    age_days = (now - ts).total_seconds() / 86400.0
    return max(floor, math.exp(-age_days * math.log(2) / half_life_days))


def _age_days(timestamp: datetime) -> float:
    """Age of *timestamp* in days, naive timestamps assumed UTC."""
    ts = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - ts).total_seconds() / 86400.0)


def combined_score(
    relevance: float,
    timestamp: datetime,
    outcome: Outcome,
    half_life_days: float,
    recency_floor: float = 0.0,
) -> float:
    """Combine normalised relevance, recency decay, and outcome weight into [0, 1]."""
    recency = _recency_score(timestamp, half_life_days, recency_floor)
    weight = _OUTCOME_WEIGHTS.get(outcome, 0.5)
    return relevance * recency * weight


def score_and_admit(
    search_results: Sequence[SearchResult],
    episodes_by_id: dict[str, Episode],
    *,
    half_life_days: float,
    min_relevance: float,
    limit: int,
    backend: str,
    recency_floor: float = 0.0,
) -> list[EpisodeMatch]:
    """Score search-port candidates and keep those clearing *min_relevance*.

    Shared by the SQLite and Postgres adapters so the ranking rule — and the
    funnel telemetry that makes it diagnosable — exist in exactly one place.
    Every candidate's score is reported, admitted or not, so a corpus scoring
    just under the gate is visible rather than merely absent.
    """
    matches: list[EpisodeMatch] = []
    scores: list[float] = []
    best: tuple[float, Episode] | None = None

    for result in search_results:
        episode = episodes_by_id.get(result.id)
        if episode is None:
            continue
        score = combined_score(
            result.score,
            episode.timestamp,
            episode.outcome,
            half_life_days,
            recency_floor,
        )
        scores.append(score)
        if best is None or score > best[0]:
            best = (score, episode)
        if score >= min_relevance:
            matches.append(EpisodeMatch(episode=episode, relevance=score))

    record_funnel(
        backend=backend,
        candidates=len(scores),
        admitted=len(matches),
        scores=scores,
        top_candidate_age_days=_age_days(best[1].timestamp) if best else None,
    )

    matches.sort(key=lambda m: m.relevance, reverse=True)
    return matches[:limit]


def _format_episode_block(episode: Episode) -> str:
    """Format a single episode for injection into the system prompt."""
    ts = episode.timestamp.strftime("%Y-%m-%d")
    outcome = episode.outcome.upper()
    tags_str = ", ".join(episode.tags) if episode.tags else "general"
    tools_str = ", ".join(episode.tools_used) if episode.tools_used else "none"
    block = (
        f"[{ts}] [{outcome}] {episode.task_description}\n"
        f"Tags: {tags_str} | Tools: {tools_str}\n"
        f"{episode.summary}"
    )
    if episode.reflection:
        block += f"\nReflection: {episode.reflection}"
    return block


def build_prefetch_context(matches: list[EpisodeMatch], budget_chars: int) -> str:
    """Build the prefetch context string from a list of episode matches.

    Episodes are formatted and concatenated until the budget is exhausted.
    Returns an empty string if no episodes fit within the budget.
    """
    blocks: list[str] = []
    used = 0
    for match in matches:
        block = _format_episode_block(match.episode)
        if used + len(block) > budget_chars:
            break
        blocks.append(block)
        used += len(block) + 1  # +1 for separator

    if not blocks:
        return ""

    separator = "\n\n---\n\n"
    body = separator.join(blocks)
    return f"## Past Context\n\n{body}"


def build_session_summaries(
    episodes: list[Episode],
    limit: int,
    truncate_chars: int,
) -> list[SessionSummary]:
    """Group episodes by session and build SessionSummary objects.

    Args:
        episodes: Flat list of episodes from a search query.
        limit: Maximum number of session summaries to return.
        truncate_chars: Maximum total characters in per-session summary text.

    Returns:
        Session summaries sorted by last active timestamp, descending.
    """
    session_episodes: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes:
        session_episodes[ep.session_id].append(ep)

    summaries: list[SessionSummary] = []
    for session_id, eps in session_episodes.items():
        eps.sort(key=lambda e: e.timestamp)
        last_active = max(e.timestamp for e in eps)
        all_tags: list[str] = []
        for ep in eps:
            all_tags.extend(ep.tags)
        unique_tags = list(dict.fromkeys(all_tags))

        lines: list[str] = []
        total_chars = 0
        for ep in eps:
            line = f"- [{ep.outcome.upper()}] {ep.task_description}: {ep.summary}"
            if total_chars + len(line) > truncate_chars:
                break
            lines.append(line)
            total_chars += len(line)

        summary_text = "\n".join(lines)
        summaries.append(
            SessionSummary(
                session_id=session_id,
                summary=summary_text,
                episode_count=len(eps),
                last_active=last_active,
                tags=unique_tags[:10],
            )
        )

    summaries.sort(key=lambda s: s.last_active, reverse=True)
    return summaries[:limit]
