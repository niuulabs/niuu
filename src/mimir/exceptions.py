"""Errors raised by the Mímir client side (adapters, tools, bridge and reflex)."""

from __future__ import annotations


class MimirUnavailableError(Exception):
    """Raised when Mímir could not answer, so absence cannot be concluded.

    "Not found" and "could not ask" are different answers. Collapsing them
    reported a transient 503 as "research page references missing source_ids",
    which reads as a permanent provenance defect and prescribes a remedy —
    ingest the sources — that cannot fix an outage.
    """


class MimirConfigurationError(Exception):
    """Raised when a Mímir client is configured in a way it cannot honour."""
