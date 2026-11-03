"""Artifact publications and their bursts (control plane Slice 0: F1, F1n, F1t).

Forge records every artifact a session publishes as a durable *publication*, read from
the stored session-log rows (design §5.2):

1. ``claude_hook`` PostToolUse rows of the claude.ai ``Artifact`` tool that publish a
   page (never an asset upload, a failure or a refused publish);
2. ``conversation.turn`` rows with a ``present_file`` part (every engine's present-file,
   Codex included);
3. ``conversation.turn`` rows whose notification links an artifact (``kind: artifact``);
4. ``conversation.turn`` rows written by the ``report_artifact`` MCP tool.

Publications of claude.ai artifacts from shapes 1 and 4 are grouped into *bursts*
(``model/bursts.md``): keyed by ``(host, provider_key, origin)``, a sliding window, an
id minted once from the seed publication and never recomputed. A live burst announces
itself with one notice after a durable hold unless an agent already linked the artifact.

This module is pure: extraction, identities and the membership rule. Persistence is the
:class:`~volundr.domain.artifact_ports.ArtifactPublicationRepository` port.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from niuu.domain.artifact_reports import REPORT_ARTIFACT_TOOL_NAME, report_from_turn
from niuu.domain.notifications import NOTIFICATION_TURN_KIND
from volundr.domain.models import SessionLogEntry
from volundr.domain.presented_files import validate_public_base_url

#: Fixed namespaces: ids derived from an occurrence are stable across replays, restarts and
#: releases. ``NS_BURST`` is the platform reference's (``platform/api/bursts.py``).
NS_BURST = uuid.UUID("6f7d2c1e-8a5b-4c3d-9e2f-1a0b3c4d5e6f")
NS_PUBLICATION = uuid.UUID("0b8f6a52-6c1e-4d0a-9a37-5e1c2f7d9b40")

#: Prefixes of Slice 0's own notices (design §V1): the correlation id names the burst and the
#: dedupe key makes a retried emission idempotent.
BURST_CORRELATION_PREFIX = "art-burst:"
BURST_DEDUPE_PREFIX = "artifact-burst:"

#: ``suppress_reason`` values (``forge_artifact_bursts``).
AGENT_NOTICE_REASON = "agent_notice"
SESSION_GONE_REASON = "session_gone"
EMIT_FAILED_REASON = "emit_failed"

ARTIFACT_TOOL_NAME = "Artifact"
PRESENT_FILE_TOOL_NAME = "present_file"
HOOK_KIND = "claude_hook"
TURN_KIND = "conversation.turn"

_CLAUDE_ARTIFACT_URL = re.compile(
    r"^https://claude\.ai/(?:code/)?artifact/(?P<key>[A-Za-z0-9]+)(?:[/?#].*)?$"
)


class PublicationSource(StrEnum):
    """Which durable row shape a publication was read from."""

    ARTIFACT_TOOL = "artifact_tool"
    PRESENT_FILE = "present_file"
    NOTIFY_LINK = "notify_link"
    REPORT_ARTIFACT = "report_artifact"


class Provider(StrEnum):
    CLAUDE_AI = "claude_ai"
    FORGE_FILE = "forge_file"


class Origin(StrEnum):
    """``live`` when ingest saw the row soon after it happened; else ``backfill``."""

    LIVE = "live"
    BACKFILL = "backfill"


class ArtifactType(StrEnum):
    """``Artifact.type`` (cp-api-v1 1.0.0)."""

    DECK = "deck"
    DESIGN = "design"
    DOC = "doc"
    DESIGN_SYSTEM = "design_system"
    PAGE = "page"
    FILE = "file"
    UNKNOWN = "unknown"


#: ``Artifact.marker``: shown only where the type is one a reader can act on (F1t).
_MARKED_TYPES = frozenset(
    {ArtifactType.DECK, ArtifactType.DESIGN, ArtifactType.DOC, ArtifactType.FILE}
)

#: Burst-forming sources: real publishes, never announcements (model/bursts.md) or files.
BURST_SOURCES = frozenset({PublicationSource.ARTIFACT_TOOL, PublicationSource.REPORT_ARTIFACT})


def artifact_marker(artifact_type: ArtifactType | str) -> str | None:
    kind = ArtifactType(artifact_type)
    return kind.value if kind in _MARKED_TYPES else None


def claude_artifact_key(url: str | None) -> str | None:
    """The claude.ai artifact id in ``url``, or ``None`` when it names no artifact."""
    if not isinstance(url, str):
        return None
    match = _CLAUDE_ARTIFACT_URL.match(url.strip())
    return match.group("key") if match else None


@dataclass(frozen=True)
class TypeCatalog:
    """Maps an Artifact type to ``Artifact.type`` (F1t): by the type's URL, else its name.

    The name comes from the type instructions' front matter (``name: slides``), which the
    creating publish returns; later publishes return only the type's URL.
    """

    by_url_key: Mapping[str, ArtifactType] = field(default_factory=dict)
    by_name: Mapping[str, ArtifactType] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls, type_urls: Mapping[str, str], type_names: Mapping[str, str]
    ) -> TypeCatalog:
        by_key: dict[str, ArtifactType] = {}
        for url, value in type_urls.items():
            key = claude_artifact_key(url) or url
            by_key[key] = ArtifactType(value)
        return cls(
            by_url_key=by_key,
            by_name={name.lower(): ArtifactType(value) for name, value in type_names.items()},
        )

    def classify(self, type_url: str | None, type_name: str | None) -> ArtifactType:
        if not type_url and not type_name:
            return ArtifactType.PAGE
        key = claude_artifact_key(type_url) or type_url
        if key and key in self.by_url_key:
            return self.by_url_key[key]
        if type_name and type_name.lower() in self.by_name:
            return self.by_name[type_name.lower()]
        return ArtifactType.UNKNOWN


_FRONT_MATTER_NAME = re.compile(r"^\s*-{3}\s*\n(?:.*\n)*?\s*name:\s*([A-Za-z0-9_-]+)", re.M)


def _type_name(instructions: Any) -> str | None:
    if not isinstance(instructions, str):
        return None
    match = _FRONT_MATTER_NAME.match(instructions[:2000])
    return match.group(1) if match else None


@dataclass(frozen=True)
class PublicationCandidate:
    """One publication read from one stored log row, before origin and identity."""

    session_id: UUID
    session_seq: int
    ordinal: int
    source: PublicationSource
    provider: Provider
    provider_key: str
    at: datetime
    url: str | None = None
    title: str | None = None
    tool_use_id: str | None = None
    provider_version: str | None = None
    provider_seq: int | None = None
    artifact_type: ArtifactType = ArtifactType.UNKNOWN
    type_url: str | None = None
    mime: str | None = None
    subagent: bool = False
    version_label: str | None = None

    @property
    def forms_bursts(self) -> bool:
        return self.provider is Provider.CLAUDE_AI and self.source in BURST_SOURCES


@dataclass(frozen=True)
class ArtifactPublication:
    """A stored publication (``forge_artifact_publications``)."""

    publication_id: UUID
    seq: int
    host: str
    session_id: UUID
    session_seq: int
    ordinal: int
    source: PublicationSource
    provider: Provider
    provider_key: str
    revision_id: str
    at: datetime
    origin: Origin
    url: str | None
    title: str | None
    tool_use_id: str | None
    provider_version: str | None
    provider_seq: int | None
    artifact_type: ArtifactType
    type_url: str | None
    mime: str | None
    subagent: bool
    version_label: str | None
    burst_id: UUID | None
    created_at: datetime

    @property
    def marker(self) -> str | None:
        return artifact_marker(self.artifact_type)

    def wire(self) -> dict[str, Any]:
        return {
            "publication_id": str(self.publication_id),
            "seq": self.seq,
            "host": self.host,
            "session_id": str(self.session_id),
            "session_seq": self.session_seq,
            "ordinal": self.ordinal,
            "occurrence": [self.host, "log", str(self.session_id), self.session_seq],
            "source": self.source.value,
            "provider": self.provider.value,
            "provider_key": self.provider_key,
            "provider_version": self.provider_version,
            "provider_seq": self.provider_seq,
            "revision_id": self.revision_id,
            "title": self.title,
            "url": self.url,
            "at": self.at.isoformat(),
            "origin": self.origin.value,
            "tool_use_id": self.tool_use_id,
            "type": self.artifact_type.value,
            "marker": self.marker,
            "type_url": self.type_url,
            "mime": self.mime,
            "subagent": self.subagent,
            "version_label": self.version_label,
            "burst_id": str(self.burst_id) if self.burst_id else None,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class Burst:
    """A persisted burst (``forge_artifact_bursts``); its id never changes."""

    burst_id: UUID
    host: str
    provider_key: str
    origin: Origin
    started_at: datetime
    last_at: datetime
    publications: int
    seed_session_id: UUID
    seed_session_seq: int
    due_at: datetime | None
    notification_id: UUID | None = None
    suppressed_by: UUID | None = None
    suppress_reason: str | None = None
    emitted_at: datetime | None = None

    @property
    def pending(self) -> bool:
        return (
            self.due_at is not None
            and self.notification_id is None
            and self.suppressed_by is None
            and self.suppress_reason is None
        )

    def window_contains(self, at: datetime, window: timedelta) -> bool:
        return self.started_at - window <= at <= self.last_at + window

    def wire(self) -> dict[str, Any]:
        return {
            "burst_id": str(self.burst_id),
            "host": self.host,
            "provider_key": self.provider_key,
            "origin": self.origin.value,
            "started_at": self.started_at.isoformat(),
            "last_at": self.last_at.isoformat(),
            "publications": self.publications,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "notification_id": str(self.notification_id) if self.notification_id else None,
            "suppressed_by": str(self.suppressed_by) if self.suppressed_by else None,
            "suppress_reason": self.suppress_reason,
        }


@dataclass(frozen=True)
class BurstChoice:
    """Where a burst-forming publication goes: an existing burst, or a new one."""

    burst: Burst | None
    bridged: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class BurstPolicy:
    """The tunables of model/bursts.md, from config (``artifacts``)."""

    window: timedelta
    hold: timedelta
    live_lag: timedelta


# -- identities ----------------------------------------------------------------


def occurrence(host: str, session_id: UUID | str, session_seq: int) -> tuple[str, str, str, int]:
    """The publication occurrence ``(host, "log", session, seq)`` (design §V10.2)."""
    return (host, "log", str(session_id), int(session_seq))


def publication_id(host: str, session_id: UUID | str, session_seq: int, ordinal: int) -> UUID:
    return uuid.uuid5(NS_PUBLICATION, f"{host}|{session_id}|{session_seq}|{ordinal}")


def burst_id_for(host: str, provider_key: str, session_id: UUID | str, session_seq: int) -> UUID:
    """Minted once when a burst opens, from its seed; the reference's exact formula."""
    seed = occurrence(host, session_id, session_seq)
    return uuid.uuid5(NS_BURST, f"{host}|{provider_key}|{seed}")


def revision_id(candidate: PublicationCandidate, host: str) -> str:
    """One revision per provider version (a republish is a second publication of it);
    without a version, the publication is its own revision (design §5.1)."""
    scope = f"{candidate.provider.value}:{candidate.provider_key}"
    if candidate.provider_version:
        basis = f"{scope}|{candidate.provider_version}"
    else:
        handle = candidate.tool_use_id or f"{candidate.session_seq}.{candidate.ordinal}"
        basis = f"{scope}|{host}|{candidate.session_id}|{handle}"
    return "rev_" + hashlib.sha256(basis.encode()).hexdigest()[:32]


def burst_dedupe_key(burst_id: UUID) -> str:
    return f"{BURST_DEDUPE_PREFIX}{burst_id}"


def burst_correlation_id(burst_id: UUID) -> str:
    return f"{BURST_CORRELATION_PREFIX}{burst_id}"


def classify_origin(at: datetime, seen_at: datetime, live_lag: timedelta) -> Origin:
    """``live`` when the projector sees the row within ``live_lag`` of its timestamp."""
    return Origin.LIVE if seen_at - at <= live_lag else Origin.BACKFILL


# -- the membership rule (model/bursts.md rules 4-6) -----------------------------


def choose_burst(bursts: Iterable[Burst], at: datetime, window: timedelta) -> BurstChoice:
    """Pick the burst a publication at ``at`` joins among ``bursts`` of its own key and origin.

    It joins the burst whose window contains it; inside two, the most recent (greatest
    ``last_at``, ties by id) and the other is recorded as bridged. Bursts never merge.
    """
    containing = [burst for burst in bursts if burst.window_contains(at, window)]
    if not containing:
        return BurstChoice(burst=None)
    containing.sort(key=lambda burst: (-burst.last_at.timestamp(), str(burst.burst_id)))
    return BurstChoice(
        burst=containing[0], bridged=tuple(burst.burst_id for burst in containing[1:])
    )


def widened(burst: Burst, at: datetime) -> Burst:
    """``burst`` after a publication at ``at`` joined: the window widens, the id stays."""
    return Burst(
        burst_id=burst.burst_id,
        host=burst.host,
        provider_key=burst.provider_key,
        origin=burst.origin,
        started_at=min(burst.started_at, at),
        last_at=max(burst.last_at, at),
        publications=burst.publications + 1,
        seed_session_id=burst.seed_session_id,
        seed_session_seq=burst.seed_session_seq,
        due_at=burst.due_at,
        notification_id=burst.notification_id,
        suppressed_by=burst.suppressed_by,
        suppress_reason=burst.suppress_reason,
        emitted_at=burst.emitted_at,
    )


def opened(host: str, candidate: PublicationCandidate, origin: Origin, hold: timedelta) -> Burst:
    """A new burst seeded by ``candidate``; only a live one is due to announce itself."""
    return Burst(
        burst_id=burst_id_for(
            host, candidate.provider_key, candidate.session_id, candidate.session_seq
        ),
        host=host,
        provider_key=candidate.provider_key,
        origin=origin,
        started_at=candidate.at,
        last_at=candidate.at,
        publications=1,
        seed_session_id=candidate.session_id,
        seed_session_seq=candidate.session_seq,
        due_at=candidate.at + hold if origin is Origin.LIVE else None,
    )


def announces(
    *,
    source: str,
    correlation_id: str | None,
    link_urls: Iterable[str | None],
    created_at: datetime,
    burst: Burst,
    window: timedelta,
    until: datetime | None = None,
) -> bool:
    """Whether a notice announces ``burst`` (model/bursts.md, "Announcement").

    An ``agent`` notice from any session that links the artifact, created inside the
    burst's current window. Slice 0's own notices never count. ``until`` narrows the
    window's end (the hold rechecks only notices that exist at emission).
    """
    if source != "agent":
        return False
    if correlation_id and correlation_id.startswith(BURST_CORRELATION_PREFIX):
        return False
    if not any(claude_artifact_key(url) == burst.provider_key for url in link_urls):
        return False
    start, end = suppression_bounds(burst, window, until)
    return start <= created_at <= end


def suppression_bounds(
    burst: Burst, window: timedelta, until: datetime | None
) -> tuple[datetime, datetime]:
    end = burst.last_at + window
    if until is not None and until < end:
        end = until
    return burst.started_at - window, end


# -- extraction from stored log rows ---------------------------------------------


def publications_from_entry(
    entry: SessionLogEntry, catalog: TypeCatalog
) -> list[PublicationCandidate]:
    """Every publication one stored log row carries (often none). Never raises."""
    payload = entry.payload
    if not isinstance(payload, dict):
        return []
    try:
        if payload.get("type") == HOOK_KIND or entry.kind == HOOK_KIND:
            found = _from_hook(entry, payload, catalog)
            return [found] if found is not None else []
        if payload.get("type") == TURN_KIND:
            return _from_turn(entry, payload)
    except (TypeError, ValueError, AttributeError):
        return []
    return []


def is_publication_row(payload: Any) -> bool:
    """Cheap pre-filter on a submitted frame (the stored row is read back before projection)."""
    if not isinstance(payload, dict):
        return False
    if payload.get("type") == HOOK_KIND:
        inner = payload.get("payload")
        return (
            payload.get("hook_event_name") == "PostToolUse"
            and isinstance(inner, dict)
            and inner.get("tool_name") == ARTIFACT_TOOL_NAME
        )
    if payload.get("type") != TURN_KIND:
        return False
    turn = payload.get("turn")
    if not isinstance(turn, dict):
        return False
    return (
        any(
            isinstance(part, dict)
            and part.get("type") == "tool_use"
            and part.get("name") in (PRESENT_FILE_TOOL_NAME, REPORT_ARTIFACT_TOOL_NAME)
            for part in turn.get("parts") or []
        )
        or _notification_links(turn) != []
    )


def _from_hook(
    entry: SessionLogEntry, payload: dict, catalog: TypeCatalog
) -> PublicationCandidate | None:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    inner = payload.get("payload")
    if not isinstance(inner, dict) or inner.get("tool_name") != ARTIFACT_TOOL_NAME:
        return None
    tool_input = inner.get("tool_input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    if (tool_input.get("action") or "publish") != "publish":
        return None
    if tool_input.get("asset") is True or tool_input.get("asset") == "true":
        return None
    response = inner.get("tool_response")
    if not isinstance(response, dict):
        return None
    if response.get("files_read"):
        return None  # a refused publish: the tool asked to read files first
    url = response.get("url")
    key = claude_artifact_key(url)
    if key is None:
        return None
    type_info = response.get("type")
    type_url = type_info.get("url") if isinstance(type_info, dict) else None
    type_url = type_url or tool_input.get("type_url") or None
    provider_seq = response.get("seq")
    return PublicationCandidate(
        session_id=entry.session_id,
        session_seq=entry.seq,
        ordinal=0,
        source=PublicationSource.ARTIFACT_TOOL,
        provider=Provider.CLAUDE_AI,
        provider_key=key,
        at=_aware(entry.ts),
        url=url,
        title=_text(response.get("title")) or _text(tool_input.get("title")),
        tool_use_id=_text(inner.get("tool_use_id")),
        provider_version=_text(response.get("version")),
        provider_seq=provider_seq if isinstance(provider_seq, int) else None,
        artifact_type=catalog.classify(type_url, _type_name(response.get("instructions"))),
        type_url=type_url,
        subagent=bool(inner.get("agent_id")),
    )


def _from_turn(entry: SessionLogEntry, payload: dict) -> list[PublicationCandidate]:
    turn = payload.get("turn")
    if not isinstance(turn, dict):
        return []
    found: list[PublicationCandidate] = []
    ordinal = 0
    reported_once = False  # a report turn carries one report (its metadata), however many parts
    for part in turn.get("parts") or []:
        if not isinstance(part, dict) or part.get("type") != "tool_use":
            continue
        name = part.get("name")
        if name == PRESENT_FILE_TOOL_NAME:
            presented = _present_file(entry, part, ordinal)
            if presented is not None:
                found.append(presented)
                ordinal += 1
            continue
        if name == REPORT_ARTIFACT_TOOL_NAME and not reported_once:
            reported_once = True
            reported = _reported(entry, turn, ordinal)
            if reported is not None:
                found.append(reported)
                ordinal += 1
    for link in _notification_links(turn):
        key = claude_artifact_key(link.get("url"))
        if key is None:
            continue
        found.append(
            PublicationCandidate(
                session_id=entry.session_id,
                session_seq=entry.seq,
                ordinal=ordinal,
                source=PublicationSource.NOTIFY_LINK,
                provider=Provider.CLAUDE_AI,
                provider_key=key,
                at=_aware(entry.ts),
                url=link.get("url"),
                title=_text(link.get("label")),
                tool_use_id=_text(turn.get("id")),
            )
        )
        ordinal += 1
    return found


def _durable_url(value: Any) -> str | None:
    """A present_file turn's durable URL (F2), only if it is a valid tailnet http(s) URL."""
    url = _text(value)
    if not url:
        return None
    try:
        return validate_public_base_url(url) or None
    except ValueError:
        return None


def _present_file(entry: SessionLogEntry, part: dict, ordinal: int) -> PublicationCandidate | None:
    tool_input = part.get("input")
    tool_input = tool_input if isinstance(tool_input, dict) else {}
    file_id = _text(tool_input.get("file_id")) or _text(part.get("id"))
    if not file_id or not file_id.startswith("pf_"):
        return None
    return PublicationCandidate(
        session_id=entry.session_id,
        session_seq=entry.seq,
        ordinal=ordinal,
        source=PublicationSource.PRESENT_FILE,
        provider=Provider.FORGE_FILE,
        provider_key=file_id,
        at=_aware(entry.ts),
        # F2: the durable, phone-reachable URL the broker recorded, else the route.
        url=_durable_url(tool_input.get("url"))
        or f"/api/v1/forge/sessions/{entry.session_id}/files/presented/{file_id}",
        title=_text(tool_input.get("name")),
        tool_use_id=file_id,
        artifact_type=ArtifactType.FILE,
        mime=_text(tool_input.get("mime")),
    )


def _reported(entry: SessionLogEntry, turn: dict, ordinal: int) -> PublicationCandidate | None:
    report = report_from_turn(turn)
    if report is None:
        return None
    key = claude_artifact_key(report.url)
    if key is None:
        return None
    return PublicationCandidate(
        session_id=entry.session_id,
        session_seq=entry.seq,
        ordinal=ordinal,
        source=PublicationSource.REPORT_ARTIFACT,
        provider=Provider.CLAUDE_AI,
        provider_key=key,
        at=_aware(entry.ts),
        url=report.url,
        title=report.title,
        tool_use_id=_text(turn.get("id")),
        provider_version=report.provider_version,
        artifact_type=ArtifactType(report.type) if report.type else ArtifactType.UNKNOWN,
        version_label=report.version_label,
    )


def _notification_links(turn: dict) -> list[dict]:
    metadata = turn.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("kind") != NOTIFICATION_TURN_KIND:
        return []
    notification = metadata.get("notification")
    if not isinstance(notification, dict):
        return []
    links = notification.get("links")
    if not isinstance(links, list):
        return []
    return [
        link
        for link in links
        if isinstance(link, dict) and link.get("kind") == "artifact" and link.get("url")
    ]


def _aware(at: datetime) -> datetime:
    """Stored rows are aware; a naive producer timestamp is read as UTC."""
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
