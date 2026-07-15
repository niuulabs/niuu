"""Port for the central ODIN review queue storage."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ravn.odin.review import ReviewItem


class ReviewQueueStore(ABC):
    """Durable storage for ReviewItems awaiting and carrying human decisions."""

    @abstractmethod
    async def upsert(self, item: ReviewItem) -> ReviewItem:
        """Insert or replace one review item by ``item_id``."""

    @abstractmethod
    async def get(self, item_id: str) -> ReviewItem | None:
        """Return one item, or None when unknown."""

    @abstractmethod
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
        """Return items newest-first, optionally filtered."""

    @abstractmethod
    async def counts(self) -> dict[str, int]:
        """Return item counts keyed by status."""
