"""Post-retrieval ranking layer for Mímir search (NIU-1057, NIU-1058, NIU-1062).

The search port returns base relevance scores (RRF over FTS/semantic). This
module applies domain signals on top — recency, title match, confidence,
page type, backlink centrality, and zone weighting — as multiplicative,
individually-attributed boosts. The ``SearchPort`` contract explicitly leaves
domain scoring to the caller; this is that caller.

Every weight comes from :class:`mimir.config.RankingConfig`. Each boost is a
pure function so it can be tested and eval'd in isolation, and every applied
factor is recorded in a per-result breakdown for the ``?debug=true`` search
surface.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

from mimir.config import RankingConfig
from niuu.domain.mimir import MimirPageMeta

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {"the", "a", "an", "is", "in", "of", "for", "to", "and", "or", "on", "at", "what", "who"}
)


def tokenize(text: str) -> set[str]:
    """Lowercase alphanumeric tokens minus stopwords."""
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if tok not in _STOPWORDS}


def recency_factor(
    updated_at: datetime,
    config: RankingConfig,
    *,
    now: datetime | None = None,
) -> float:
    """Exponential decay with a floor: halves every ``recency_half_life_days``."""
    moment = now or datetime.now(UTC)
    age_days = max(0.0, (moment - updated_at).total_seconds() / 86400.0)
    if config.recency_half_life_days <= 0:
        return 1.0
    decay = 0.5 ** (age_days / config.recency_half_life_days)
    return max(config.recency_floor, decay)


def title_match_factor(query_tokens: set[str], title: str, config: RankingConfig) -> float:
    """Boost when every query token appears in the title."""
    if not query_tokens:
        return 1.0
    title_tokens = tokenize(title)
    if query_tokens <= title_tokens:
        return config.title_match_boost
    return 1.0


def confidence_factor(meta: MimirPageMeta, config: RankingConfig) -> float:
    """Multiplier for the page's epistemic confidence (compiled-truth boost)."""
    if meta.confidence is None:
        return 1.0
    return config.confidence_boosts.get(meta.confidence.value, 1.0)


def page_type_factor(meta: MimirPageMeta, config: RankingConfig) -> float:
    """Multiplier per page type (directives/decisions outrank plain topics)."""
    if meta.page_type is None:
        return 1.0
    return config.page_type_weights.get(meta.page_type.value, 1.0)


def backlink_factor(backlink_count: int, config: RankingConfig) -> float:
    """Centrality boost: well-linked pages rank higher, log-scaled."""
    if backlink_count <= 0:
        return 1.0
    return 1.0 + config.backlink_alpha * math.log1p(backlink_count)


def zone_factor(section_heading: str, config: RankingConfig) -> float:
    """Chunk-zone multiplier: compiled truth (default 1.0) vs timeline evidence.

    Hierarchical recall (NIU-1062): synthesized knowledge answers first, the
    evidence trail second.
    """
    if "timeline" in section_heading.lower():
        return config.zone_weights.get("timeline", 1.0)
    return config.zone_weights.get("compiled_truth", 1.0)


def apply_boosts(
    base_score: float,
    meta: MimirPageMeta,
    query_tokens: set[str],
    config: RankingConfig,
    *,
    backlink_count: int = 0,
    graph_factor: float = 1.0,
    now: datetime | None = None,
) -> tuple[float, dict[str, float]]:
    """Apply all page-level boosts; returns (final_score, breakdown).

    The breakdown maps each factor name to its multiplier (plus ``base`` and
    ``final``) so ranking decisions are inspectable via ``?debug=true``.
    """
    breakdown: dict[str, float] = {"base": base_score}
    breakdown["recency"] = recency_factor(meta.updated_at, config, now=now)
    breakdown["title_match"] = title_match_factor(query_tokens, meta.title, config)
    breakdown["confidence"] = confidence_factor(meta, config)
    breakdown["page_type"] = page_type_factor(meta, config)
    breakdown["backlinks"] = backlink_factor(backlink_count, config)
    breakdown["graph"] = graph_factor

    final = base_score
    for name, factor in breakdown.items():
        if name == "base":
            continue
        final *= factor
    breakdown["final"] = final
    return final, breakdown
