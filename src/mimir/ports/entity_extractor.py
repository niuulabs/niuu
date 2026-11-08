"""EntityExtractorPort — interface for entity detection during Mímir ingest.

After a raw source is persisted, the ingest tool hands it to an extractor that
upserts entity pages for what the source names. How entities are found (which
model, which prompt) is the implementation's business; the ingest tool only
needs the paths that were created or updated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.mimir import MimirSource


class EntityExtractorPort(ABC):
    """Extract entities from an ingested source and apply them to the wiki."""

    @abstractmethod
    async def run(self, source: MimirSource) -> list[str]:
        """Extract entities from *source* and upsert entity pages.

        Returns the wiki paths that were created or updated. Raises when
        extraction fails: an empty list means the source names no entities.
        """
        raise NotImplementedError
