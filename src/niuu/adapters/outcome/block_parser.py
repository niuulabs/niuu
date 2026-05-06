"""Deterministic outcome block parser adapter."""

from __future__ import annotations

from niuu.domain.outcome import OutcomeSchema, ParsedOutcome, parse_outcome_block
from niuu.ports.outcome import OutcomeExtractorPort


class BlockParserAdapter(OutcomeExtractorPort):
    """Extracts structured outcomes by parsing ---outcome--- / ---end--- blocks.

    This is a deterministic, zero-cost adapter — no LLM calls required.
    """

    def extract(self, text: str, schema: OutcomeSchema | None = None) -> ParsedOutcome | None:
        """Extract structured outcome from text output."""
        return parse_outcome_block(text, schema)
