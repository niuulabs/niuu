"""Port for extracting structured outcomes from agent/session output."""

from __future__ import annotations

from abc import ABC, abstractmethod

from niuu.domain.outcome import OutcomeSchema, ParsedOutcome


class OutcomeExtractorPort(ABC):
    """Port for extracting structured outcomes from text output."""

    @abstractmethod
    def extract(self, text: str, schema: OutcomeSchema | None = None) -> ParsedOutcome | None:
        """Extract structured outcome from text output."""
