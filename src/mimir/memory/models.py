"""Memory records, layers and evidence (docs/design/memory/README.md sections 3–4).

These are the gateway's own records. A memory system behind the
:class:`~mimir.ports.memory_backend.MemoryBackend` port stores and returns them;
trust levels and fact policy come from :mod:`niuu.domain.mimir_facts`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from niuu.domain.mimir_facts import Fact, FactCaller, FactKind, Omission, TrustLevel


class MemoryStoreError(Exception):
    """Base class for every memory failure; each message names its remedy."""


class MemoryValidationError(MemoryStoreError):
    """A request is malformed or lacks something required."""


class RecordNotFoundError(MemoryStoreError):
    """No record has this reference in this namespace."""


class RevisionConflictError(MemoryStoreError):
    """A page changed since the revision the writer read."""


class MemoryBackendError(MemoryStoreError):
    """A memory system refused or could not complete a call. Never read as 'no result'."""


class CapabilityMissingError(MemoryStoreError):
    """A layer was placed on a memory system that lacks a capability it needs."""


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------


class LayerKind(StrEnum):
    """The layers of step 1. Order of declaration is not distance; see :attr:`Layer.distance`."""

    SESSION = "session"
    PROJECT = "project"
    ORG = "org"


#: How far a layer is from the session; reads show the nearest first.
_DISTANCE = {LayerKind.SESSION: 0, LayerKind.PROJECT: 1, LayerKind.ORG: 2}

_LAYER_KEY = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(frozen=True)
class Layer:
    """One body of memory: ``org``, ``project:<id>`` or ``session:<id>``."""

    kind: LayerKind
    key: str = ""

    def __post_init__(self) -> None:
        if self.kind is LayerKind.ORG:
            if self.key:
                raise MemoryValidationError("the org layer has no key")
            return
        if not _LAYER_KEY.fullmatch(self.key):
            raise MemoryValidationError(
                f"a {self.kind.value} layer needs a lowercase id of letters, digits and "
                f"hyphens, not {self.key!r}"
            )

    @property
    def id(self) -> str:
        return self.kind.value if self.kind is LayerKind.ORG else f"{self.kind.value}:{self.key}"

    @property
    def namespace(self) -> str:
        """The layer's namespace inside its memory system (``org``, ``project-<id>``)."""
        return self.kind.value if self.kind is LayerKind.ORG else f"{self.kind.value}-{self.key}"

    @property
    def proposals_namespace(self) -> str:
        """Where proposals for this layer are kept (``_proposals/<namespace>``)."""
        return f"_proposals/{self.namespace}"

    @property
    def distance(self) -> int:
        return _DISTANCE[self.kind]

    @classmethod
    def parse(cls, layer_id: str) -> Layer:
        kind, sep, key = layer_id.strip().partition(":")
        try:
            layer_kind = LayerKind(kind)
        except ValueError:
            names = ", ".join(k.value for k in LayerKind)
            raise MemoryValidationError(
                f"unknown layer {layer_id!r}; layers are {names} (project:<id>, session:<id>)"
            ) from None
        if layer_kind is LayerKind.ORG and sep:
            raise MemoryValidationError("the org layer is named 'org', with no id")
        return cls(layer_kind, key)

    @classmethod
    def from_namespace(cls, namespace: str) -> Layer:
        kind, _, key = namespace.partition("-")
        try:
            return cls(LayerKind(kind), key)
        except ValueError:
            raise MemoryValidationError(f"{namespace!r} is not a layer namespace") from None

    def __str__(self) -> str:
        return self.id


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class EvidenceKind(StrEnum):
    """What a record can cite. Section 3: a reference an operator can open, never free text."""

    SOURCE = "source"
    SESSION_EVENT = "session_event"
    TOOL_CALL = "tool_call"
    ROOM_MESSAGE = "room_message"
    URL = "url"
    COMMIT = "commit"
    STATEMENT = "statement"
    PAGE = "page"
    FACT = "fact"
    IMPORT = "import"


_EVIDENCE_SHAPES: dict[EvidenceKind, tuple[re.Pattern[str], str]] = {
    EvidenceKind.SOURCE: (
        re.compile(r"^src_[0-9a-f]+$"),
        "a source id such as src_1a2b3c4d5e6f7a8b",
    ),
    EvidenceKind.SESSION_EVENT: (
        re.compile(r"^[^\s/]+/[^\s/]+$"),
        "<session id>/<event id> of a Forge session event",
    ),
    EvidenceKind.TOOL_CALL: (re.compile(r"^\S+$"), "the tool call id"),
    EvidenceKind.ROOM_MESSAGE: (re.compile(r"^[^\s/]+/[^\s/]+$"), "<room id>/<message id>"),
    EvidenceKind.URL: (re.compile(r"^https?://\S+$"), "an http(s) URL"),
    EvidenceKind.COMMIT: (
        re.compile(r"^\S+@[0-9a-f]{7,64}$"),
        "<repository>@<commit sha>, e.g. niuulabs/niuu@1f4f9622b",
    ),
    EvidenceKind.STATEMENT: (re.compile(r"^\S+$"), "the id of a person's statement in the UI"),
    EvidenceKind.PAGE: (re.compile(r"^\S+$"), "a page reference such as services/booking-api"),
    EvidenceKind.FACT: (re.compile(r"^\S+$"), "a fact id"),
    EvidenceKind.IMPORT: (re.compile(r"^\S.*$"), "the imported file"),
}


@dataclass(frozen=True)
class Evidence:
    """A typed reference to what a record was learned from: ``<kind>:<value>``."""

    kind: EvidenceKind
    value: str

    def __post_init__(self) -> None:
        pattern, shape = _EVIDENCE_SHAPES[self.kind]
        if not pattern.fullmatch(self.value):
            raise MemoryValidationError(
                f"evidence {self.ref!r} is not a valid {self.kind.value} reference: "
                f"use {self.kind.value}:{shape}"
            )

    @property
    def ref(self) -> str:
        return f"{self.kind.value}:{self.value}"

    @classmethod
    def parse(cls, ref: str) -> Evidence:
        kind, sep, value = ref.strip().partition(":")
        kinds = ", ".join(k.value for k in EvidenceKind if k is not EvidenceKind.IMPORT)
        if not sep:
            raise MemoryValidationError(
                f"evidence {ref!r} is free text; cite a reference as <kind>:<value>, "
                f"where kind is one of {kinds}"
            )
        try:
            evidence_kind = EvidenceKind(kind.strip())
        except ValueError:
            raise MemoryValidationError(
                f"evidence {ref!r} has an unknown kind {kind!r}; use one of {kinds}"
            ) from None
        return cls(evidence_kind, value.strip())

    @classmethod
    def parse_all(cls, refs: list[str] | tuple[str, ...]) -> tuple[Evidence, ...]:
        return tuple(cls.parse(ref) for ref in refs)


# ---------------------------------------------------------------------------
# Who wrote a record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Attribution:
    """Who wrote a record, at what trust, from what, and when.

    ``attested`` is False for a record whose sealed attribution is missing or does
    not verify (written to the memory system directly, or edited there): such a
    record reads at the lowest trust level.
    """

    author: str
    trust: TrustLevel
    evidence: tuple[Evidence, ...]
    written_at: datetime
    attested: bool = True

    @classmethod
    def by(cls, caller: FactCaller, evidence: tuple[Evidence, ...], at: datetime) -> Attribution:
        return cls(caller.author.ref, caller.trust, evidence, at)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class SourceKind(StrEnum):
    """Raw material a layer keeps. Tool output and diagnostics are not sources."""

    DOCUMENT = "document"
    WEB = "web"
    TICKET = "ticket"
    PULL_REQUEST = "pull_request"
    CHAT_THREAD = "chat_thread"
    TRANSCRIPT_EXCERPT = "transcript_excerpt"
    CONVERSATION = "conversation"
    RESEARCH = "research"


@dataclass(frozen=True)
class NewSource:
    title: str
    content: str
    kind: SourceKind
    origin_url: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise MemoryValidationError("a source needs a title")
        if not self.content.strip():
            raise MemoryValidationError(
                "a source needs its content, kept as it arrived; the gateway does not fetch URLs"
            )


@dataclass(frozen=True)
class SourceRecord:
    id: str
    namespace: str
    title: str
    kind: str
    content: str
    origin_url: str | None
    attribution: Attribution


@dataclass(frozen=True)
class SourceRef:
    id: str
    namespace: str
    title: str


@dataclass(frozen=True)
class NewTimelineRow:
    on: date
    summary: str
    evidence: tuple[Evidence, ...]
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise MemoryValidationError("a timeline entry needs a summary")
        if "\n" in self.summary:
            raise MemoryValidationError("a timeline summary is one line; put the rest in detail")
        if not self.evidence:
            raise MemoryValidationError(
                "a timeline entry needs evidence: cite what it was learned from as <kind>:<value>"
            )


@dataclass(frozen=True)
class TimelineRow:
    on: date
    summary: str
    detail: str
    attribution: Attribution


@dataclass(frozen=True)
class PageWrite:
    """A page's current state as a writer sets it. The timeline is never part of it."""

    compiled_truth: str
    title: str = ""
    page_type: str = ""
    evidence: tuple[Evidence, ...] = ()

    def __post_init__(self) -> None:
        if not self.compiled_truth.strip():
            raise MemoryValidationError("a page needs its compiled truth (the current state)")


@dataclass(frozen=True)
class PageRecord:
    slug: str
    namespace: str
    title: str
    page_type: str
    compiled_truth: str
    revision: str
    attribution: Attribution
    updated_at: datetime | None
    timeline: tuple[TimelineRow, ...] = ()
    timeline_omitted: str = ""


@dataclass(frozen=True)
class PageRef:
    slug: str
    namespace: str
    title: str
    updated_at: datetime | None = None


class HitKind(StrEnum):
    PAGE = "page"
    SOURCE = "source"


@dataclass(frozen=True)
class SearchHit:
    kind: HitKind
    ref: str
    namespace: str
    title: str
    snippet: str


@dataclass(frozen=True)
class LayeredFact:
    """A fact and the namespace its sealed provenance places it in."""

    fact: Fact
    namespace: str


@dataclass(frozen=True)
class LayeredRecall:
    facts: tuple[LayeredFact, ...]
    omitted: tuple[Omission, ...] = ()


@dataclass(frozen=True)
class RememberResult:
    fact: LayeredFact
    status: str


@dataclass(frozen=True)
class EntityLayer:
    """What one namespace knows about a subject."""

    namespace: str
    subject: str
    page: PageRecord | None
    aliases: tuple[str, ...]
    links: tuple[str, ...]
    facts: tuple[LayeredFact, ...]
    omitted: tuple[Omission, ...] = ()


@dataclass(frozen=True)
class ChangeSet:
    """What changed after a cursor, oldest first. ``cursor`` resumes after the last one."""

    pages: tuple[PageRef, ...]
    facts: tuple[LayeredFact, ...]
    cursor: str
    has_more: bool


@dataclass(frozen=True)
class NamespaceRemoval:
    pages_removed: int
    facts_withdrawn: int


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


class ProposalStatus(StrEnum):
    PENDING = "pending"


class ChangeAction(StrEnum):
    ADD_SOURCE = "add_source"
    WRITE_PAGE = "write_page"
    ADD_TIMELINE = "add_timeline"
    REMEMBER = "remember"
    RETIRE = "retire"


@dataclass(frozen=True)
class Proposal:
    """A change waiting for the target layer's owner. Approval comes in step 2."""

    id: str
    target_layer: str
    status: ProposalStatus
    action: ChangeAction
    change: dict[str, Any]
    evidence: tuple[Evidence, ...]
    proposer: str
    proposer_session: str
    session_owner: str
    trust: TrustLevel
    reason: str
    approver: str
    created_at: datetime
    attested: bool = True


@dataclass(frozen=True)
class SessionMemoryContext:
    """Forge's answer about a session (contract C1)."""

    session_id: str
    launch_id: str
    owner_id: str
    tenant_id: str
    project_id: str | None
    workflow_run_id: str | None
    project_write_prefixes: tuple[str, ...] = field(default_factory=tuple)


__all__ = [
    "Attribution",
    "CapabilityMissingError",
    "ChangeAction",
    "ChangeSet",
    "EntityLayer",
    "Evidence",
    "EvidenceKind",
    "FactKind",
    "HitKind",
    "Layer",
    "LayerKind",
    "LayeredFact",
    "LayeredRecall",
    "MemoryBackendError",
    "MemoryStoreError",
    "MemoryValidationError",
    "NamespaceRemoval",
    "NewSource",
    "NewTimelineRow",
    "PageRecord",
    "PageRef",
    "PageWrite",
    "Proposal",
    "ProposalStatus",
    "RecordNotFoundError",
    "RememberResult",
    "RevisionConflictError",
    "SearchHit",
    "SessionMemoryContext",
    "SourceKind",
    "SourceRecord",
    "SourceRef",
    "TimelineRow",
]
