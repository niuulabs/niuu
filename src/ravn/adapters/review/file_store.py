"""File-backed review queue store for local development and mini mode."""

from __future__ import annotations

from pathlib import Path

from ravn.odin.review import JsonReviewStore, ReviewItem
from ravn.ports.review_queue import ReviewQueueStore


class FileReviewQueueStore(ReviewQueueStore):
    """Durable single-file queue store wrapping :class:`JsonReviewStore`.

    The same JSON shape the resident outbox uses, so a local platform run
    needs no database while still surviving restarts.
    """

    def __init__(self, path: str | Path) -> None:
        self._store = JsonReviewStore(path)

    async def upsert(self, item: ReviewItem) -> ReviewItem:
        return self._store.save(item)

    async def get(self, item_id: str) -> ReviewItem | None:
        try:
            return self._store.get(item_id)
        except ValueError:
            return None

    async def list_items(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        environment_id: str | None = None,
        risk_class: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReviewItem]:
        rows = self._store.list(status=status, kind=kind, environment_id=environment_id)
        rows.reverse()
        if risk_class:
            rows = [item for item in rows if item.risk_class == risk_class]
        if query:
            needle = query.casefold()
            rows = [
                item
                for item in rows
                if needle
                in " ".join(
                    (
                        item.title,
                        item.summary,
                        item.environment_id,
                        item.valkyrie_id,
                        item.kind,
                        item.requested_action,
                    )
                ).casefold()
            ]
        rows = rows[max(offset, 0) :]
        if limit is not None:
            rows = rows[: max(limit, 0)]
        return rows

    async def counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for item in self._store.list():
            totals[item.status] = totals.get(item.status, 0) + 1
        return totals
