"""Mímir domain models — shared between the Mímir service and Ravn adapters.

These types define the wire contract for all Mímir operations.  Both
``src/mimir/`` (the standalone service) and ``src/ravn/`` (adapters that call
the service) import from here so that neither module depends on the other.
"""

from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
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
    source_type: Literal["web", "document", "conversation", "tool_output", "research"]
    ingested_at: datetime
    content_hash: str  # SHA-256 hex — used for staleness detection
    origin_url: str | None = None


# ---------------------------------------------------------------------------
# Thread types
# ---------------------------------------------------------------------------


class ThreadState(StrEnum):
    """Lifecycle state of a thread."""

    open = "open"
    pulling = "pulling"
    blocked = "blocked"
    waiting_for_peer = "waiting_for_peer"
    waiting_for_operator = "waiting_for_operator"
    closed = "closed"
    dissolved = "dissolved"


@dataclass
class ThreadContextRef:
    """A reference to an external artifact that provides context for a thread."""

    ref_type: Literal["conversation", "ingest", "observation", "search"]
    ref_id: str
    ref_summary: str


class ThreadOwnershipError(RuntimeError):
    """Raised when assign_thread_owner detects a conflicting owner."""

    def __init__(self, path: str, current_owner: str) -> None:
        super().__init__(f"Thread '{path}' already owned by '{current_owner}'")
        self.path = path
        self.current_owner = current_owner


# ---------------------------------------------------------------------------
# Wiki page types
# ---------------------------------------------------------------------------


@dataclass
class MimirPageMeta:
    """Lightweight metadata record for a Mímir wiki page or thread."""

    path: str  # e.g. "technical/volundr/auth.md" or "threads/retrieval-architecture"
    title: str
    summary: str  # one-line summary used in index.md
    category: str  # top-level category: "technical", "projects", "threads", etc.
    updated_at: datetime
    source_ids: list[str] = field(default_factory=list)
    # Compiled-truth frontmatter fields (additive — all optional for backwards compat)
    page_type: PageType | None = None
    confidence: PageConfidence | None = None
    entity_type: EntityType | None = None
    related_entities: list[str] = field(default_factory=list)
    is_thread: bool = False
    # Set by action shapes when they write artifacts derived from a thread.
    # The enricher skips pages with this flag to prevent feedback loops.
    produced_by_thread: bool = False
    # Thread-specific fields (None / empty for wiki pages)
    thread_state: ThreadState | None = None
    thread_weight: float | None = None
    thread_owner_id: str | None = None
    thread_context_refs: list[ThreadContextRef] = field(default_factory=list)
    thread_next_action_hint: str | None = None
    thread_resolved_artifact_path: str | None = None
    thread_weight_signals: dict | None = None


@dataclass
class MimirPage:
    """A single Mímir wiki page or thread with full content and metadata."""

    meta: MimirPageMeta
    content: str  # full Markdown content


@dataclass
class MimirQueryResult:
    """Result returned by MimirPort.query()."""

    question: str
    answer: str  # LLM-synthesised answer from relevant pages
    sources: list[MimirPage] = field(default_factory=list)


@dataclass
class MimirSourceMeta:
    """Lightweight metadata for a raw source — used by list_sources()."""

    source_id: str
    title: str
    ingested_at: datetime
    source_type: str
    mount_name: str | None = None  # set by CompositeMimirAdapter to identify origin mount


@dataclass
class LintIssue:
    """A single issue found during a Mímir wiki health check.

    Each issue has a machine-readable ``id`` (L01–L12), a ``severity``
    (``"error"``, ``"warning"``, or ``"info"``), a human-readable ``message``,
    the ``page_path`` of the affected page (relative to the wiki root), and an
    ``auto_fixable`` flag indicating whether the adapter can correct the issue
    automatically when ``lint(fix=True)`` is called.
    """

    id: str  # L01–L12
    severity: str  # "error", "warning", "info"
    message: str
    page_path: str
    auto_fixable: bool = False


@dataclass
class MimirLintReport:
    """Health-check report produced by MimirPort.lint().

    ``issues`` contains all findings across the 12 check types.
    ``summary`` provides per-severity counts for quick filtering.
    ``issues_found`` is True when any issue is present.
    """

    issues: list[LintIssue]
    pages_checked: int

    @property
    def issues_found(self) -> bool:
        return bool(self.issues)

    @property
    def summary(self) -> dict[str, int]:
        """Count of issues per severity: error, warning, info."""
        counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Thread YAML schema
# ---------------------------------------------------------------------------

_VALID_REF_TYPES: frozenset[str] = frozenset({"conversation", "ingest", "observation", "search"})


class ThreadSchemaError(Exception):
    """Raised when a thread YAML file fails validation."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"Invalid thread schema at {path!r}: {reason}")


@dataclass
class ThreadYamlSchema:
    """Canonical, validated representation of a thread's ``.yaml`` file.

    Shared by all adapters (``MarkdownMimirAdapter``, ``HttpMimirAdapter``)
    and the Mímir service so that parsing and serialisation logic lives in
    exactly one place.
    """

    title: str
    state: ThreadState
    weight: float
    created_at: datetime
    updated_at: datetime
    owner_id: str | None = None
    next_action_hint: str | None = None
    resolved_artifact_path: str | None = None
    context_refs: list[ThreadContextRef] = field(default_factory=list)
    weight_signals: dict = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: Path) -> ThreadYamlSchema:
        """Parse and validate a thread YAML file.

        Raises :class:`ThreadSchemaError` on missing required fields, invalid
        state, or malformed dates.
        """
        import yaml  # local import — PyYAML is an optional dep for non-file adapters

        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ThreadSchemaError(str(path), f"cannot read file: {exc}") from exc

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise ThreadSchemaError(str(path), f"YAML parse error: {exc}") from exc

        if not isinstance(data, dict):
            raise ThreadSchemaError(str(path), "expected a YAML mapping at top level")

        return cls._validate(data, str(path))

    @classmethod
    def from_dict(cls, data: dict) -> ThreadYamlSchema:
        """Parse and validate from a dict (for HTTP response deserialisation)."""
        return cls._validate(data, "<dict>")

    # ------------------------------------------------------------------
    # Instance methods
    # ------------------------------------------------------------------

    def to_yaml(self, path: Path) -> None:
        """Serialise to a YAML file atomically (write to ``.tmp``, then rename).

        The atomic write prevents corrupt YAML on disk if the process crashes
        mid-write.
        """
        import yaml  # local import

        target = Path(path)
        tmp = Path(str(path) + ".tmp")

        tmp.write_text(
            yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        os.replace(tmp, target)

    def to_dict(self) -> dict:
        """Serialise to a dict (for HTTP request serialisation)."""
        return {
            "title": self.title,
            "state": self.state.value,
            "weight": self.weight,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "owner_id": self.owner_id,
            "next_action_hint": self.next_action_hint,
            "resolved_artifact_path": self.resolved_artifact_path,
            "context_refs": [
                {
                    "ref_type": ref.ref_type,
                    "ref_id": ref.ref_id,
                    "ref_summary": ref.ref_summary,
                }
                for ref in self.context_refs
            ],
            "weight_signals": self.weight_signals,
        }

    def to_page_meta(self, slug: str) -> MimirPageMeta:
        """Produce a :class:`MimirPageMeta` for use in :class:`MimirPage` responses."""
        return MimirPageMeta(
            path=f"threads/{slug}.yaml",
            title=self.title,
            summary=self.next_action_hint or "",
            category="threads",
            updated_at=self.updated_at,
            thread_state=self.state,
            thread_weight=self.weight,
            thread_owner_id=self.owner_id,
            thread_context_refs=list(self.context_refs),
            thread_next_action_hint=self.next_action_hint,
            thread_resolved_artifact_path=self.resolved_artifact_path,
            thread_weight_signals=self.weight_signals or None,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @classmethod
    def _validate(cls, data: dict, path: str) -> ThreadYamlSchema:
        """Validate *data* and return a :class:`ThreadYamlSchema` instance."""
        title = data.get("title")
        if not title or not isinstance(title, str) or not title.strip():
            raise ThreadSchemaError(path, "'title' must be a non-empty string")

        raw_state = data.get("state")
        if raw_state is None:
            raise ThreadSchemaError(path, "'state' is required")
        try:
            state = ThreadState(raw_state)
        except ValueError as exc:
            valid = ", ".join(s.value for s in ThreadState)
            raise ThreadSchemaError(
                path,
                f"'state' {raw_state!r} is not a valid ThreadState; valid: {valid}",
            ) from exc

        raw_weight = data.get("weight")
        if raw_weight is None:
            raise ThreadSchemaError(path, "'weight' is required")
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError) as exc:
            raise ThreadSchemaError(path, f"'weight' must be a float, got {raw_weight!r}") from exc
        if weight < 0.0:
            raise ThreadSchemaError(path, f"'weight' must be >= 0.0, got {weight!r}")

        created_at = cls._parse_datetime(data.get("created_at"), "created_at", path)
        updated_at = cls._parse_datetime(data.get("updated_at"), "updated_at", path)

        raw_refs = data.get("context_refs", [])
        if not isinstance(raw_refs, list):
            raise ThreadSchemaError(path, "'context_refs' must be a list")
        context_refs = [cls._parse_context_ref(item, path) for item in raw_refs]

        raw_signals = data.get("weight_signals", {})
        if not isinstance(raw_signals, dict):
            raise ThreadSchemaError(path, "'weight_signals' must be a mapping")

        return cls(
            title=title.strip(),
            state=state,
            weight=weight,
            created_at=created_at,
            updated_at=updated_at,
            owner_id=data.get("owner_id"),
            next_action_hint=data.get("next_action_hint"),
            resolved_artifact_path=data.get("resolved_artifact_path"),
            context_refs=context_refs,
            weight_signals=raw_signals,
        )

    @staticmethod
    def _parse_datetime(value: object, field_name: str, path: str) -> datetime:
        if value is None:
            raise ThreadSchemaError(path, f"'{field_name}' is required")
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str):
            raise ThreadSchemaError(
                path,
                f"'{field_name}' must be an ISO-8601 string, got {value!r}",
            )
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ThreadSchemaError(
                path, f"'{field_name}' is not a valid ISO-8601 datetime: {exc}"
            ) from exc

    @staticmethod
    def _parse_context_ref(item: object, path: str) -> ThreadContextRef:
        if not isinstance(item, dict):
            raise ThreadSchemaError(
                path,
                f"each 'context_refs' entry must be a mapping, got {item!r}",
            )
        for key in ("ref_type", "ref_id", "ref_summary"):
            if key not in item:
                raise ThreadSchemaError(path, f"context_refs entry missing required key {key!r}")
        if item["ref_type"] not in _VALID_REF_TYPES:
            raise ThreadSchemaError(
                path,
                f"context_refs entry has invalid ref_type {item['ref_type']!r}; "
                f"valid: {', '.join(sorted(_VALID_REF_TYPES))}",
            )
        return ThreadContextRef(
            ref_type=item["ref_type"],
            ref_id=item["ref_id"],
            ref_summary=item["ref_summary"],
        )
