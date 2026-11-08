"""Shared retrieval utilities: cosine similarity and Reciprocal Rank Fusion (RRF).

Both the SQLite and Postgres search adapters in ``niuu`` share them, and so do
Ravn's memory adapters.
"""

from __future__ import annotations

import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors.

    Returns a value in ``[-1, 1]`` (typically ``[0, 1]`` for non-negative
    embedding spaces).  Returns ``0.0`` for zero-length or mismatched vectors.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    *,
    k: int = 60,
) -> dict[str, float]:
    """Merge multiple ranked lists using Reciprocal Rank Fusion (RRF).

    Each inner list in *rankings* is an ordered sequence of document IDs from
    best to worst.  The returned dict maps document ID to its aggregated RRF
    score — higher is better.

    Args:
        rankings: One or more ranked lists of document IDs (best first).
        k: Smoothing constant.  Typically 60; higher values reduce the
           advantage of top positions.

    Returns:
        Dict mapping document_id to aggregated RRF score.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return scores
