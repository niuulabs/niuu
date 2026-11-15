"""Errors raised by the Mímir client side (adapters, tools, bridge and reflex)."""

from __future__ import annotations


class MimirUnavailableError(Exception):
    """Raised when Mímir could not answer, so absence cannot be concluded.

    "Not found" and "could not ask" are different answers. Collapsing them
    reported a transient 503 as "research page references missing source_ids",
    which reads as a permanent provenance defect and prescribes a remedy —
    ingest the sources — that cannot fix an outage.
    """


class MimirWriteRefusedError(Exception):
    """A write was refused because the mount it targets does not allow it.

    The mount is read-only, or the page is outside the prefixes it may write. The
    message names the mount, the operation and what the mount allows; the MCP tool
    returns it to the model as a tool error, and the composite counts it.
    """


class MimirConfigurationError(Exception):
    """Raised when a Mímir client is configured in a way it cannot honour."""
