"""Reranking port — second-stage ordering for retrieved documents.

Retrieval and ranking are different jobs. An embedding is computed once, without knowing the
question, so it must be a general-purpose summary of a document; a reranker sees the query and the
document together and can judge *this* document against *this* question. The usual shape is to
retrieve generously by embedding and then let the reranker decide the order of the few that matter.

Configured backend failures must raise; only empty input returns an empty list.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RerankerPort(Protocol):
    """Score *documents* against *query*, best first."""

    async def rerank(
        self, query: str, documents: list[str], *, top_n: int | None = None
    ) -> list[tuple[int, float]]:
        """Return ``(original_index, score)`` pairs ordered best-first.

        Raise when a configured backend cannot produce complete scores.
        """
        ...
