"""Mímir domain models — shared between the Mímir service and its clients.

These types define the wire contract for all Mímir operations.  Both
``src/mimir/`` (the standalone service) and the clients that call the service
import from here so that neither module depends on the other.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Compiled-truth page taxonomy enums
# ---------------------------------------------------------------------------


class PageType(StrEnum):
    """Controlled vocabulary for the ``type`` frontmatter field."""

    directive = "directive"
    decision = "decision"
    goal = "goal"
    preference = "preference"
    observation = "observation"
    entity = "entity"
    topic = "topic"


class PageConfidence(StrEnum):
    """Epistemic confidence level for a Mímir page."""

    high = "high"
    medium = "medium"
    low = "low"


class EntityType(StrEnum):
    """Sub-type for pages whose ``type`` is ``entity``."""

    person = "person"
    project = "project"
    concept = "concept"
    technology = "technology"
    organization = "organization"
    strategy = "strategy"


def compute_content_hash(content: str) -> str:
    """Return the SHA-256 hex digest of *content*."""
    return hashlib.sha256(content.encode()).hexdigest()


def compute_source_id(content: str) -> str:
    """Return the canonical identifier for an immutable raw source."""
    return "src_" + compute_content_hash(content)[:16]


# ---------------------------------------------------------------------------
# Slug utility
# ---------------------------------------------------------------------------


def slugify(title: str) -> str:
    """Convert *title* to a URL/filename-safe slug.

    Lowercased, ASCII-safe, hyphens instead of spaces/punctuation.
    """
    # Normalise unicode to ASCII-compatible form
    normalised = unicodedata.normalize("NFKD", title)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii")
    # Replace non-alphanumeric characters with hyphens
    slug = re.sub(r"[^\w\s-]", "", ascii_text).strip()
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.lower()


# ---------------------------------------------------------------------------
# Core domain models
# ---------------------------------------------------------------------------


@dataclass
class MimirSource:
    """An immutable raw source ingested into the Mímir knowledge base.

    Raw sources are never modified after ingestion.  ``content_hash`` (SHA-256)
    is stored so staleness can be detected when the same URL is re-fetched and
    its content has changed.
    """

    source_id: str
    title: str
    content: str
    source_type: Literal["web", "document", "conversation", "tool_output", "diagnostic", "research"]
    ingested_at: datetime
    content_hash: str  # SHA-256 hex — used for staleness detection
    origin_url: str | None = None


# ---------------------------------------------------------------------------
# Wiki page types
# ---------------------------------------------------------------------------


@dataclass
class MimirPageMeta:
    """Lightweight metadata record for a Mímir wiki page."""

    path: str  # e.g. "technical/volundr/auth.md"
    title: str
    summary: str  # one-line summary used in index.md
    category: str  # top-level category: "technical", "projects", etc.
    updated_at: datetime
    source_ids: list[str] = field(default_factory=list)
    # Compiled-truth frontmatter fields (additive — all optional for backwards compat)
    page_type: PageType | None = None
    confidence: PageConfidence | None = None
    entity_type: EntityType | None = None
    related_entities: list[str] = field(default_factory=list)
    # Populated by search only (NIU-1057): final ranking score and per-boost
    # attribution for debuggability. None outside search results.
    search_score: float | None = None
    score_breakdown: dict[str, float] | None = None


@dataclass
class MimirPage:
    """A single Mímir wiki page with full content and metadata."""

    meta: MimirPageMeta
    content: str  # full Markdown content


@dataclass
class MimirQueryResult:
    """Result returned by MimirPort.query()."""

    question: str
    answer: str  # LLM-synthesised answer from relevant pages
    sources: list[MimirPage] = field(default_factory=list)


# Source types that record the system's own activity rather than knowledge about
# the world: probe output, tool transcripts, run logs. They are never synthesised
# into wiki pages, so ingesting them leaves a source that can never become
# "processed" — the shared mount accumulated 61 of them, and the synthesis
# trigger re-swept every one of them every ten minutes indefinitely.
#
# This is a statement about what the store is *for*, not a judgement about what
# any particular observation means — that judgement stays with Ravn.
OPERATIONAL_SOURCE_TYPES: frozenset[str] = frozenset({"tool_output", "diagnostic"})


def refuse_operational_source_type(source_type: str) -> None:
    """Raise if *source_type* records system activity, which no page will synthesise.

    The one check every ingest path makes (REST ``/ingest``, the ``mimir_ingest``
    agent tool, ``python -m mimir ingest``), so an agent or a script cannot store
    what the API refuses.
    """
    if source_type not in OPERATIONAL_SOURCE_TYPES:
        return
    raise ValueError(
        f"Refusing to ingest source_type {source_type!r}: it records system activity, "
        "not knowledge, so nothing will ever synthesise it into a page and it would sit "
        "unprocessed forever. Write run output to logs instead."
    )


@dataclass
class MimirSourceMeta:
    """Lightweight metadata for a raw source — used by list_sources()."""

    source_id: str
    title: str
    ingested_at: datetime
    source_type: str
    origin_url: str | None = None
    mount_name: str | None = None  # set by CompositeMimirAdapter to identify origin mount


@dataclass
class MimirMountSummary:
    """Scale/health summary of a single mount — used by /mimir/mounts and /mimir/stats.

    This is polled by dashboards and by every mount listing, so it must stay
    cheap: adapters are expected to answer it from filesystem metadata and
    cached state, never by reading page or source bodies.
    """

    page_count: int
    source_count: int
    categories: list[str]
    last_write: datetime | None = None
