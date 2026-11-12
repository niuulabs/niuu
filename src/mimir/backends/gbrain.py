"""GBrainBackend — MemoryBackend over one org's gbrain, reached over MCP.

One gbrain per org (design section 7, Tenancy). gbrain sends every remote write
to the one source the client token is bound to, so a layer is not a gbrain
source but a **slug namespace** in it:

=====================  ==========================================================
Record                 gbrain slug
=====================  ==========================================================
page                   ``l/<namespace>/<slug>``
source                 ``l/<namespace>/_sources/<source id>``
fact (entity)          facts are remembered on entity ``l/<namespace>/<subject>``
fact index             ``l/<namespace>/_facts/<gbrain fact id>``, one per fact,
                       so deleting a namespace can withdraw its facts
namespace marker       ``l/<namespace>/_namespace``
proposal               ``l/_proposals/<layer namespace>/<proposal id>`` (the
                       gateway writes these as pages of that namespace)
=====================  ==========================================================

Segments starting with ``_`` are the backend's own; callers' slugs may not use them.

**Attribution is sealed.** Every page, source and timeline row carries who wrote
it, at what trust, from what evidence and in which namespace, sealed with an HMAC
key only Mímir holds (``Sealer``) and bound to a hash of the content. A record
whose seal is missing, does not verify, names another namespace or no longer
matches its content reads as unattested, at the lowest trust. Facts carry the
same in their provenance (``FactCodec``, shared with ``GBrainFactAdapter``).

**Reads filter by namespace.** gbrain cannot filter by slug prefix, so reads page
through gbrain's results with ``offset`` and keep only the caller's namespaces,
until the caller's requested count is met or gbrain has no more. The bound is
always the caller's count. Two gbrain limits remain and are reported when hit:
``recall`` returns at most ``recall_limit`` facts per call and does not page,
and ``get_timeline`` takes a limit and no offset (``timeline_limit``).

gbrain's ``think`` is never called: it would read across every namespace.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

import yaml

from mimir.adapters.gbrain_facts import FactCodec, fact_id, split_fact_id
from mimir.adapters.gbrain_mcp import (
    GBrainMcpClient,
    GBrainTransportError,
    Sealer,
    read_secret,
    result_payload,
    text_result,
)
from mimir.memory.models import (
    Attribution,
    ChangeSet,
    EntityLayer,
    Evidence,
    HitKind,
    LayeredFact,
    LayeredRecall,
    MemoryBackendError,
    MemoryValidationError,
    NamespaceRemoval,
    NewSource,
    NewTimelineRow,
    PageRecord,
    PageRef,
    PageWrite,
    RecordNotFoundError,
    RememberResult,
    RevisionConflictError,
    SearchHit,
    SourceRecord,
    SourceRef,
    TimelineRow,
)
from mimir.ports.memory_backend import ALL_CAPABILITIES, Capability, MemoryBackend
from niuu.cap_telemetry import record_cap_hit
from niuu.domain.mimir import compute_content_hash, compute_source_id
from niuu.domain.mimir_facts import (
    FactError,
    NewFact,
    Omission,
    OmissionReason,
    TrustError,
    TrustLevel,
    select_facts,
)

_ROOT = "l"
_SOURCES = "_sources"
_FACTS = "_facts"
_MARKER = "_namespace"
_PAGE_SEAL = "mp1"
_ROW_SEAL = "mt1"
_TIMELINE_SENTINEL = "<!-- timeline -->"
_ATTRIBUTION_KEY = "niuu_attribution"
_UNATTESTED_AUTHOR = "external:gbrain"
_SYSTEM_AUTHOR = "operator:memory-gateway"
_RECALL_KNOB = "memory.systems[].recall_limit"
_TIMELINE_KNOB = "memory.systems[].timeline_limit"
_SLUG_KNOB = "memory.systems[].max_slug_chars"

_NAMESPACE = re.compile(r"^_?[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9-]*)?$")
_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_JSON_FENCE = re.compile(r"```json\n(?P<body>.*)\n```", re.DOTALL)
_NOT_FOUND_CODES = frozenset({"page_not_found", "not_found"})
_CONFLICT_CODES = frozenset({"revision_conflict", "revision_required"})


def _digest(text: str) -> str:
    """A content hash insensitive to how gbrain re-spaces markdown."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:32]


def _moment(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """The list of row objects a tool answered with, bare or under one of *keys*."""
    candidate = payload
    if isinstance(payload, dict):
        candidate = next((payload[k] for k in keys if isinstance(payload.get(k), list)), None)
    if not isinstance(candidate, list):
        raise MemoryBackendError(
            f"gbrain answered without the expected list ({', '.join(keys)}): {str(payload)[:200]}"
        )
    return [row for row in candidate if isinstance(row, dict)]


class GBrainBackend(MemoryBackend):
    """One org's gbrain.

    Args:
        mcp_url: gbrain MCP endpoint (``http://acme-brain:3131/mcp``).
        api_token / api_token_file: bearer token with read and write scope.
        signing_key / signing_key_file: the secret that seals attribution; keep it
            with Mímir only. Required.
        protocol_version: the memory-verbs protocol this backend speaks (only 1).
        timeout_seconds: HTTP timeout per call.
        page_size: rows asked of gbrain per call while paging. Not a limit on what a
            caller gets: paging continues until the caller's count is met or gbrain
            has no more.
        recall_limit: gbrain's documented per-call cap on ``recall`` rows (100).
            A recall that returns that many is reported as an omission and counted.
        timeline_limit: rows asked of ``get_timeline``, which does not page. A page
            whose timeline returns that many is reported as cut and counted.
        provenance_max_chars: gbrain's limit on a fact's ``provenance`` (500).
        max_slug_chars: gbrain's limit on a page slug (255).
    """

    def __init__(
        self,
        mcp_url: str,
        api_token: str = "",
        *,
        api_token_file: str = "",
        signing_key: str = "",
        signing_key_file: str = "",
        protocol_version: int = 1,
        timeout_seconds: float = 30.0,
        page_size: int = 100,
        recall_limit: int = 100,
        timeline_limit: int = 1000,
        provenance_max_chars: int = 500,
        max_slug_chars: int = 255,
    ) -> None:
        if protocol_version != 1:
            raise ValueError(
                f"GBrainBackend speaks memory verbs protocol 1, not {protocol_version}; "
                "pin a gbrain that serves protocol_version 1"
            )
        api_token = read_secret(api_token, api_token_file, what="gbrain API token")
        signing_key = read_secret(signing_key, signing_key_file, what="memory signing key")
        if not signing_key:
            raise ValueError(
                "GBrainBackend requires a signing key (signing_key or signing_key_file): it "
                "seals each record's trust and layer so a row written to gbrain directly "
                "cannot claim one"
            )
        for knob, value in (
            ("page_size", page_size),
            ("recall_limit", recall_limit),
            ("timeline_limit", timeline_limit),
            ("max_slug_chars", max_slug_chars),
        ):
            if value < 1:
                raise ValueError(f"{knob} must be positive")
        self._mcp = GBrainMcpClient(mcp_url, api_token, timeout_seconds=timeout_seconds)
        self._sealer = Sealer(signing_key)
        self._codec = FactCodec(self._sealer, provenance_max_chars=provenance_max_chars)
        self._page_size = page_size
        self._recall_limit = recall_limit
        self._timeline_limit = timeline_limit
        self._max_slug = max_slug_chars

    @property
    def capabilities(self) -> frozenset[Capability]:
        return ALL_CAPABILITIES

    async def close(self) -> None:
        await self._mcp.close()

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        try:
            result = await self._mcp.call(name, arguments)
        except GBrainTransportError as exc:
            raise MemoryBackendError(
                f"{exc}. Check that the brain is running and the token has read and write scope"
            ) from exc
        payload = result_payload(result)
        error = payload if isinstance(payload, dict) and "error" in payload else None
        if not result.get("isError") and error is None:
            return payload
        code = str((error or {}).get("error") or "error")
        message = str((error or {}).get("message") or text_result(result))
        suggestion = str((error or {}).get("suggestion") or "")
        text = f"gbrain {name} failed ({code}): {message}" + (
            f" — {suggestion}" if suggestion else ""
        )
        if code in _NOT_FOUND_CODES:
            raise RecordNotFoundError(text)
        if code in _CONFLICT_CODES:
            raise RevisionConflictError(text)
        raise MemoryBackendError(text)

    # ------------------------------------------------------------------
    # Slugs
    # ------------------------------------------------------------------

    @staticmethod
    def _check_namespace(namespace: str) -> str:
        if not _NAMESPACE.fullmatch(namespace):
            raise MemoryValidationError(f"{namespace!r} is not a memory namespace")
        return namespace

    def _prefix(self, namespace: str) -> str:
        return f"{_ROOT}/{self._check_namespace(namespace)}/"

    def _full(self, namespace: str, slug: str, *, internal: bool = False) -> str:
        cleaned = slug.strip().strip("/")
        segments = cleaned.split("/") if cleaned else []
        if not segments:
            raise MemoryValidationError("a page reference cannot be empty")
        for index, segment in enumerate(segments):
            if internal and index == 0 and segment.startswith("_"):
                if _SEGMENT.fullmatch(segment[1:]):
                    continue
            if not _SEGMENT.fullmatch(segment):
                raise MemoryValidationError(
                    f"page reference {slug!r} is not valid: use lowercase segments of letters, "
                    "digits, '.', '_' and '-', starting with a letter or digit, joined by '/'"
                )
        full = self._prefix(namespace) + "/".join(segments)
        if len(full) > self._max_slug:
            record_cap_hit(
                _SLUG_KNOB,
                f"page slug {full!r} is {len(full)} characters; gbrain keeps at most "
                f"{self._max_slug}",
            )
            raise MemoryValidationError(
                f"page reference {slug!r} is too long: gbrain slugs hold at most "
                f"{self._max_slug} characters including the layer prefix (knob {_SLUG_KNOB})"
            )
        return full

    def _namespace_of(self, full_slug: str, namespaces: Sequence[str]) -> str | None:
        for namespace in namespaces:
            if full_slug.startswith(self._prefix(namespace)):
                return namespace
        return None

    @staticmethod
    def _is_internal(relative: str) -> bool:
        return relative.startswith("_")

    # ------------------------------------------------------------------
    # Seals
    # ------------------------------------------------------------------

    def _seal_attribution(self, namespace: str, attribution: Attribution, content: str) -> str:
        return self._sealer.seal(
            _PAGE_SEAL,
            {
                "ns": namespace,
                "a": attribution.author,
                "t": int(attribution.trust),
                "ev": [e.ref for e in attribution.evidence],
                "w": attribution.written_at.isoformat(),
                "h": _digest(content),
            },
        )

    def _open_attribution(
        self, tag: str, namespace: str, text: Any, content: str, fallback: datetime | None
    ) -> Attribution:
        payload = self._sealer.open(tag, text if isinstance(text, str) else None)
        if (
            payload is not None
            and payload.get("ns") == namespace
            and payload.get("h") == _digest(content)
        ):
            try:
                return Attribution(
                    author=str(payload["a"]),
                    trust=TrustLevel.parse(payload["t"]),
                    evidence=Evidence.parse_all(list(payload.get("ev", []))),
                    written_at=_moment(payload.get("w")) or fallback or datetime.now(UTC),
                )
            except (KeyError, FactError, MemoryValidationError):
                pass
        return Attribution(
            author=_UNATTESTED_AUTHOR,
            trust=TrustLevel.AGENT,
            evidence=(),
            written_at=fallback or datetime.now(UTC),
            attested=False,
        )

    @staticmethod
    def _markdown(front: dict[str, Any], body: str) -> str:
        header = yaml.safe_dump(front, sort_keys=False, allow_unicode=True)
        return f"---\n{header}---\n\n{body.strip()}\n"

    # ------------------------------------------------------------------
    # Paging over the whole brain
    # ------------------------------------------------------------------

    async def _scan(
        self, prefix: str, *, want: int | None, include_internal: bool
    ) -> list[dict[str, Any]]:
        """Every page under *prefix* (or the first *want*), by paging ``list_pages``."""
        found: list[dict[str, Any]] = []
        offset = 0
        while want is None or len(found) < want:
            rows = _rows(
                await self._call(
                    "list_pages", {"limit": self._page_size, "offset": offset, "sort": "slug"}
                ),
                "pages",
                "results",
            )
            if not rows:
                break
            offset += len(rows)
            for row in rows:
                slug = str(row.get("slug") or "")
                if not slug.startswith(prefix):
                    continue
                if not include_internal and self._is_internal(slug[len(prefix) :]):
                    continue
                found.append(row)
                if want is not None and len(found) >= want:
                    break
        return found

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    async def _raw_page(self, full: str) -> dict[str, Any]:
        payload = await self._call("get_page", {"slug": full, "include_content": True})
        if not isinstance(payload, dict) or str(payload.get("slug") or full) != full:
            raise MemoryBackendError(f"gbrain get_page answered without page {full}")
        return payload

    def _page_from(self, namespace: str, slug: str, raw: dict[str, Any]) -> PageRecord:
        front = raw.get("frontmatter") if isinstance(raw.get("frontmatter"), dict) else {}
        truth = str(raw.get("compiled_truth") or "").strip()
        updated = _moment(raw.get("updated_at"))
        return PageRecord(
            slug=slug,
            namespace=namespace,
            title=str(raw.get("title") or front.get("title") or slug),
            page_type=str(raw.get("type") or front.get("type") or ""),
            compiled_truth=truth,
            revision=str(raw.get("revision") or ""),
            attribution=self._open_attribution(
                _PAGE_SEAL, namespace, front.get(_ATTRIBUTION_KEY), truth, updated
            ),
            updated_at=updated,
        )

    async def get_page(self, namespace: str, slug: str) -> PageRecord:
        full = self._full(namespace, slug)
        page = self._page_from(namespace, slug, await self._raw_page(full))
        rows = _rows(
            await self._call("get_timeline", {"slug": full, "limit": self._timeline_limit}),
            "entries",
            "timeline",
        )
        omitted = ""
        if len(rows) >= self._timeline_limit:
            omitted = (
                f"gbrain returned {len(rows)} timeline rows for {full}, the requested limit; "
                f"older rows may be missing. Raise {_TIMELINE_KNOB}"
            )
            record_cap_hit(_TIMELINE_KNOB, omitted)
        timeline = tuple(self._row_from(namespace, row) for row in rows)
        return replace(page, timeline=timeline, timeline_omitted=omitted)

    def _row_from(self, namespace: str, row: dict[str, Any]) -> TimelineRow:
        summary = str(row.get("summary") or "")
        detail = str(row.get("detail") or "")
        on = row.get("date")
        try:
            day = date.fromisoformat(str(on)[:10])
        except ValueError as exc:
            raise MemoryBackendError(
                f"gbrain returned a timeline row without a date: {row}"
            ) from exc
        return TimelineRow(
            on=day,
            summary=summary,
            detail=detail,
            attribution=self._open_attribution(
                _ROW_SEAL,
                namespace,
                row.get("source"),
                summary + "\n" + detail,
                _moment(row.get("created_at")),
            ),
        )

    async def put_page(
        self,
        namespace: str,
        slug: str,
        page: PageWrite,
        attribution: Attribution,
        *,
        expected_revision: str | None,
    ) -> PageRecord:
        full = self._full(namespace, slug)
        return await self._write_page(
            namespace, slug, full, page, attribution, expected_revision=expected_revision
        )

    async def _write_page(
        self,
        namespace: str,
        slug: str,
        full: str,
        page: PageWrite,
        attribution: Attribution,
        *,
        expected_revision: str | None,
    ) -> PageRecord:
        tail = ""
        if expected_revision is not None:
            current = await self._raw_page(full)
            if str(current.get("revision") or "") != expected_revision:
                raise RevisionConflictError(
                    f"page {slug} is at revision {current.get('revision')}, not "
                    f"{expected_revision}: read it again and rewrite from what is there now"
                )
            content = str(current.get("content") or "")
            at = content.find(_TIMELINE_SENTINEL)
            tail = "\n" + content[at:] if at >= 0 else ""
        front: dict[str, Any] = {"title": page.title or slug}
        if page.page_type:
            front["type"] = page.page_type
        front[_ATTRIBUTION_KEY] = self._seal_attribution(
            namespace, attribution, page.compiled_truth.strip()
        )
        arguments: dict[str, Any] = {
            "slug": full,
            "content": self._markdown(front, page.compiled_truth) + tail,
        }
        if expected_revision is not None:
            arguments["expected_revision"] = expected_revision
        try:
            await self._call("put_page", arguments)
        except RevisionConflictError as exc:
            if expected_revision is None:
                raise RevisionConflictError(
                    f"page {slug} already exists; read it and pass its revision to rewrite it"
                ) from exc
            raise
        return self._page_from(namespace, slug, await self._raw_page(full))

    async def append_timeline(
        self, namespace: str, slug: str, row: NewTimelineRow, attribution: Attribution
    ) -> None:
        full = self._full(namespace, slug)
        seal = self._sealer.seal(
            _ROW_SEAL,
            {
                "ns": namespace,
                "a": attribution.author,
                "t": int(attribution.trust),
                "ev": [e.ref for e in attribution.evidence],
                "w": attribution.written_at.isoformat(),
                "h": _digest(row.summary + "\n" + row.detail),
            },
        )
        arguments: dict[str, Any] = {
            "slug": full,
            "date": row.on.isoformat(),
            "summary": row.summary,
            "source": seal,
        }
        if row.detail:
            arguments["detail"] = row.detail
        await self._call("add_timeline_entry", arguments)

    async def delete_page(self, namespace: str, slug: str) -> None:
        await self._call("delete_page", {"slug": self._full(namespace, slug), "force": True})

    async def list_pages(self, namespace: str, *, limit: int) -> list[PageRef]:
        prefix = self._prefix(namespace)
        rows = await self._scan(prefix, want=limit, include_internal=False)
        return [
            PageRef(
                slug=str(row["slug"])[len(prefix) :],
                namespace=namespace,
                title=str(row.get("title") or row["slug"]),
                updated_at=_moment(row.get("updated_at")),
            )
            for row in rows
        ]

    async def backlinks(self, namespace: str, slug: str) -> list[PageRef]:
        full = self._full(namespace, slug)
        prefix = self._prefix(namespace)
        rows = _rows(await self._call("get_backlinks", {"slug": full}), "backlinks", "links")
        refs: dict[str, PageRef] = {}
        for row in rows:
            source = str(row.get("from_slug") or row.get("source_slug") or row.get("slug") or "")
            relative = source[len(prefix) :]
            if not source.startswith(prefix) or self._is_internal(relative):
                continue
            refs.setdefault(
                relative,
                PageRef(
                    slug=relative, namespace=namespace, title=str(row.get("title") or relative)
                ),
            )
        return list(refs.values())

    # ------------------------------------------------------------------
    # Sources
    # ------------------------------------------------------------------

    async def put_source(
        self, namespace: str, source: NewSource, attribution: Attribution
    ) -> tuple[SourceRecord, bool]:
        source_id = compute_source_id(source.content)
        full = self._full(namespace, f"{_SOURCES}/{source_id}", internal=True)
        body = {
            "source_id": source_id,
            "title": source.title,
            "kind": source.kind.value,
            "content": source.content,
            "origin_url": source.origin_url,
            "content_hash": compute_content_hash(source.content),
        }
        fenced = f"```json\n{json.dumps(body, ensure_ascii=False)}\n```"
        front = {
            "title": source.title,
            "type": "source",
            _ATTRIBUTION_KEY: self._seal_attribution(namespace, attribution, fenced),
        }
        try:
            await self._call("put_page", {"slug": full, "content": self._markdown(front, fenced)})
        except RevisionConflictError:
            return await self.get_source(namespace, source_id), False
        return await self.get_source(namespace, source_id), True

    async def get_source(self, namespace: str, source_id: str) -> SourceRecord:
        full = self._full(namespace, f"{_SOURCES}/{source_id}", internal=True)
        raw = await self._raw_page(full)
        truth = str(raw.get("compiled_truth") or "").strip()
        match = _JSON_FENCE.search(truth)
        try:
            body = json.loads(match.group("body")) if match else None
        except json.JSONDecodeError:
            body = None
        if (
            not isinstance(body, dict)
            or body.get("source_id") != source_id
            or compute_content_hash(str(body.get("content", ""))) != body.get("content_hash")
        ):
            raise MemoryBackendError(
                f"source {source_id} in {namespace} failed its integrity check: its stored "
                "content no longer matches its id; store the original again"
            )
        front = raw.get("frontmatter") if isinstance(raw.get("frontmatter"), dict) else {}
        return SourceRecord(
            id=source_id,
            namespace=namespace,
            title=str(body.get("title") or source_id),
            kind=str(body.get("kind") or ""),
            content=str(body["content"]),
            origin_url=body.get("origin_url"),
            attribution=self._open_attribution(
                _PAGE_SEAL,
                namespace,
                front.get(_ATTRIBUTION_KEY),
                match.group(0) if match else truth,
                _moment(raw.get("updated_at")),
            ),
        )

    async def list_sources(self, namespace: str, *, limit: int) -> list[SourceRef]:
        prefix = self._prefix(namespace) + f"{_SOURCES}/"
        rows = await self._scan(prefix, want=limit, include_internal=True)
        return [
            SourceRef(
                id=str(row["slug"])[len(prefix) :],
                namespace=namespace,
                title=str(row.get("title") or row["slug"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def _entity(self, namespace: str, subject: str) -> str:
        return self._full(namespace, subject)

    def _subject_of(self, entity_slug: str) -> tuple[str, str] | None:
        """``(namespace, subject)`` of an entity slug this backend made, else None."""
        root, _, rest = entity_slug.partition("/")
        namespace, _, subject = rest.partition("/")
        if root != _ROOT or not namespace or not subject or not _NAMESPACE.fullmatch(namespace):
            return None
        return namespace, subject

    def _decode(self, row: dict[str, Any]) -> LayeredFact | None:
        """The fact if it is ours: sealed, and sealed into the namespace its entity names."""
        located = self._subject_of(str(row.get("entity_slug") or ""))
        if located is None:
            return None
        namespace, subject = located
        fact, sealed = self._codec.decode(row, subject=subject)
        if sealed != namespace:
            return None
        return LayeredFact(fact=fact, namespace=namespace)

    async def _verb(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload = await self._call(name, arguments)
        if not isinstance(payload, dict):
            raise MemoryBackendError(f"gbrain {name} returned an unusable result: {payload}")
        return payload

    async def _fact_rows(
        self, entity: str | None, *, include_expired: bool, contains: str | None
    ) -> tuple[list[dict[str, Any]], list[Omission]]:
        arguments: dict[str, Any] = {
            "limit": self._recall_limit,
            "include_expired": include_expired,
        }
        if entity:
            arguments["entity"] = entity
        if contains:
            arguments["grep"] = contains
        rows = _rows(await self._verb("recall", arguments), "facts")
        omitted: list[Omission] = []
        if len(rows) >= self._recall_limit:
            message = (
                f"gbrain returned {len(rows)} facts for {entity or 'the brain'}, its per-call "
                "cap; there may be more. Narrow the request or retire stale facts"
            )
            record_cap_hit(_RECALL_KNOB, message)
            omitted.append(Omission(OmissionReason.BACKEND_LIMIT, 0, message))
        return rows, omitted

    async def remember(self, namespace: str, new: NewFact) -> RememberResult:
        entity = self._entity(namespace, new.draft.subject)
        draft = new.draft
        arguments: dict[str, Any] = {
            "fact": draft.statement,
            "provenance": self._codec.provenance_text(new, datetime.now(UTC), namespace=namespace),
            "entity": entity,
            "kind": draft.kind.value,
            "visibility": "world",
        }
        if draft.valid_until:
            arguments["ttl"] = draft.valid_until.astimezone(UTC).isoformat()
        payload = await self._verb("remember", arguments)
        raw_id, slug, status = payload.get("id"), payload.get("entity_slug"), payload.get("status")
        if (
            raw_id in (None, "")
            or not slug
            or status not in ("inserted", "duplicate", "superseded")
        ):
            raise MemoryBackendError(f"gbrain remember returned an unusable result: {payload}")
        if slug != entity:
            await self._call(
                "forget",
                {
                    "id": str(raw_id),
                    "reason": self._codec.retirement_note(
                        {"by": _SYSTEM_AUTHOR, "why": f"filed under {slug}, not {entity}"}
                    ),
                },
            )
            raise MemoryBackendError(
                f"gbrain filed the fact under entity {slug!r} instead of {entity!r}, outside "
                f"layer namespace {namespace}; it was withdrawn. Remember it with a subject "
                "that is not an alias of another page"
            )
        stored = await self.get_fact(fact_id(raw_id, slug))
        if status != "duplicate":
            await self._index_fact(namespace, str(raw_id), stored.fact.id)
        if status == "superseded":
            await self._refuse_trust_inversion(stored)
        return RememberResult(fact=stored, status=status)

    async def _index_fact(self, namespace: str, raw_id: str, full_id: str) -> None:
        full = self._full(namespace, f"{_FACTS}/{raw_id}", internal=True)
        front = {"title": full_id, "type": "niuu-fact-ref", "fact_id": full_id}
        try:
            await self._call("put_page", {"slug": full, "content": self._markdown(front, full_id)})
        except RevisionConflictError:
            return
        except MemoryBackendError as exc:
            raise MemoryBackendError(
                f"fact {full_id} is stored, but its index page in {namespace} could not be "
                f"written ({exc}); deleting the namespace would not withdraw it. Retire the "
                "fact and remember it again"
            ) from exc

    async def _refuse_trust_inversion(self, new: LayeredFact) -> None:
        _, entity = split_fact_id(new.fact.id)
        payload = await self._verb(
            "recall", {"entity": entity, "supersessions": True, "limit": self._recall_limit}
        )
        for row in _rows(payload, "facts"):
            if row.get("superseded_by") in (None, ""):
                continue
            if fact_id(row["superseded_by"], entity) != new.fact.id:
                continue
            retired = self._decode(row)
            if retired is not None and retired.fact.trust > new.fact.trust:
                raise TrustError(
                    f"gbrain retired {retired.fact.trust.label}-level fact {retired.fact.id} "
                    f"because the {new.fact.trust.label}-level fact {new.fact.id} is similar to "
                    "it. Re-remember the old claim at its level through a caller of that level"
                )

    async def get_fact(self, fact_id_: str) -> LayeredFact:
        raw, entity = split_fact_id(fact_id_)
        if self._subject_of(entity) is None:
            raise RecordNotFoundError(f"no fact {fact_id_}")
        rows, omitted = await self._fact_rows(entity, include_expired=True, contains=None)
        for row in rows:
            if str(row.get("fact_id") or row.get("id")) != raw:
                continue
            found = self._decode(row)
            if found is None:
                raise RecordNotFoundError(f"no fact {fact_id_} written through the gateway")
            return found
        if omitted:
            raise MemoryBackendError(
                f"fact {fact_id_} was not among the {len(rows)} facts gbrain returned for "
                f"{entity}, which is its per-call cap; it may exist beyond it"
            )
        raise RecordNotFoundError(f"no fact {fact_id_}")

    async def recall(
        self,
        namespaces: Sequence[str],
        *,
        subject: str | None,
        contains: str | None = None,
        as_of: datetime | None = None,
    ) -> LayeredRecall:
        now = datetime.now(UTC)
        moment = as_of or now
        wanted = {self._check_namespace(ns) for ns in namespaces}
        found: list[LayeredFact] = []
        omitted: list[Omission] = []
        entities = [self._entity(ns, subject) for ns in namespaces] if subject else [None]
        for entity in entities:
            rows, cut = await self._fact_rows(
                entity, include_expired=moment < now, contains=contains
            )
            omitted.extend(cut)
            for row in rows:
                fact = self._decode(row)
                if fact is not None and fact.namespace in wanted:
                    found.append(fact)
        kept, dropped = select_facts(
            [f.fact for f in found], min_trust=TrustLevel.AGENT, as_of=moment
        )
        keep_ids = {f.id for f in kept}
        return LayeredRecall(
            facts=tuple(f for f in found if f.fact.id in keep_ids),
            omitted=(*omitted, *dropped),
        )

    async def retire(
        self, namespace: str, fact_id_: str, *, by: str, why: str, superseded_by: str | None = None
    ) -> LayeredFact:
        current = await self.get_fact(fact_id_)
        if current.namespace != namespace:
            raise RecordNotFoundError(f"no fact {fact_id_} in {namespace}")
        if current.fact.retired is not None:
            return current
        raw, _ = split_fact_id(fact_id_)
        note: dict[str, Any] = {"by": by, "why": why}
        if superseded_by:
            note["sb"] = superseded_by
        payload = await self._verb(
            "forget", {"id": raw, "reason": self._codec.retirement_note(note)}
        )
        if "expired" not in payload:
            raise MemoryBackendError(f"gbrain forget returned an unusable result: {payload}")
        return await self.get_fact(fact_id_)

    async def entity(self, namespace: str, subject: str) -> EntityLayer:
        full = self._entity(namespace, subject)
        prefix = self._prefix(namespace)
        try:
            page: PageRecord | None = await self.get_page(namespace, subject)
        except RecordNotFoundError:
            page = None
        payload = await self._verb("entity", {"name": full})
        card = payload.get("card") if payload.get("found") else None
        aliases: tuple[str, ...] = ()
        links: list[str] = []
        if isinstance(card, dict) and (card.get("entity") or {}).get("slug") == full:
            aliases = tuple(str(a) for a in card.get("aka") or [] if a)
            for edge in card.get("edges") or []:
                if not isinstance(edge, dict):
                    continue
                target = str(edge.get("to_slug") or edge.get("slug") or "")
                relative = target[len(prefix) :]
                if target.startswith(prefix) and not self._is_internal(relative):
                    links.append(relative)
        recalled = await self.recall([namespace], subject=subject)
        return EntityLayer(
            namespace=namespace,
            subject=subject,
            page=page,
            aliases=aliases,
            links=tuple(dict.fromkeys(links)),
            facts=recalled.facts,
            omitted=recalled.omitted,
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        namespaces: Sequence[str],
        query: str,
        *,
        kinds: frozenset[HitKind],
        limit: int,
    ) -> list[SearchHit]:
        if not query.strip():
            raise MemoryValidationError("a search needs a query")
        hits: dict[str, SearchHit] = {}
        offset = 0
        while len(hits) < limit:
            rows = _rows(
                await self._call(
                    "query",
                    {
                        "query": query,
                        "limit": self._page_size,
                        "offset": offset,
                        # Variants are generated per call, so paging over them would skip
                        # and repeat results; autocut would end a page early.
                        "expand": False,
                        "autocut": False,
                    },
                ),
                "results",
            )
            if not rows:
                break
            offset += len(rows)
            for row in rows:
                hit = self._hit(row, namespaces, kinds)
                if hit is None or f"{hit.namespace}/{hit.ref}" in hits:
                    continue
                hits[f"{hit.namespace}/{hit.ref}"] = hit
                if len(hits) >= limit:
                    break
        return list(hits.values())

    def _hit(
        self, row: dict[str, Any], namespaces: Sequence[str], kinds: frozenset[HitKind]
    ) -> SearchHit | None:
        slug = str(row.get("slug") or "")
        namespace = self._namespace_of(slug, namespaces)
        if namespace is None:
            return None
        relative = slug[len(self._prefix(namespace)) :]
        kind = HitKind.PAGE
        if relative.startswith(f"{_SOURCES}/"):
            kind, relative = HitKind.SOURCE, relative[len(_SOURCES) + 1 :]
        elif self._is_internal(relative):
            return None
        if kind not in kinds:
            return None
        snippet = row.get("chunk_text") or row.get("chunk") or row.get("snippet") or ""
        return SearchHit(
            kind=kind,
            ref=relative,
            namespace=namespace,
            title=str(row.get("title") or relative),
            snippet=str(snippet),
        )

    # ------------------------------------------------------------------
    # Changes
    # ------------------------------------------------------------------

    async def delta(self, namespaces: Sequence[str], *, since: str, limit: int) -> ChangeSet:
        moment, _, after_slug = since.partition("|")
        pages: list[PageRef] = []
        facts: dict[str, LayeredFact] = {}
        has_more = True
        while has_more and len(pages) < limit:
            arguments: dict[str, Any] = {"since": moment}
            if after_slug:
                arguments["since_slug"] = after_slug
            payload = await self._verb("delta", arguments)
            for row in _rows(payload, "facts"):
                fact = self._decode(row)
                if fact is not None and fact.namespace in namespaces:
                    facts.setdefault(fact.fact.id, fact)
            batch = _rows(payload, "pages")
            stopped_inside = False
            for index, row in enumerate(batch):
                slug = str(row.get("slug") or "")
                # The cursor moves past every page read, ours or not: delivery resumes after it.
                moment, after_slug = str(row.get("updated_at") or moment), slug
                namespace = self._namespace_of(slug, namespaces)
                relative = slug[len(self._prefix(namespace)) :] if namespace else ""
                if namespace is not None and not self._is_internal(relative):
                    pages.append(
                        PageRef(
                            slug=relative,
                            namespace=namespace,
                            title=str(row.get("title") or relative),
                            updated_at=_moment(row.get("updated_at")),
                        )
                    )
                if len(pages) >= limit:
                    stopped_inside = index < len(batch) - 1
                    break
            if stopped_inside:
                break
            next_cursor = payload.get("next_cursor") or {}
            moment = str(next_cursor.get("since") or moment)
            after_slug = str(next_cursor.get("slug") or after_slug)
            has_more = bool(payload.get("has_more")) and bool(batch)
        return ChangeSet(
            pages=tuple(pages),
            facts=tuple(facts.values()),
            cursor=f"{moment}|{after_slug}" if after_slug else moment,
            has_more=has_more,
        )

    # ------------------------------------------------------------------
    # Namespaces
    # ------------------------------------------------------------------

    async def create_namespace(self, namespace: str) -> bool:
        full = self._full(namespace, _MARKER, internal=True)
        front = {
            "title": f"namespace {namespace}",
            "type": "niuu-namespace",
            _ATTRIBUTION_KEY: self._sealer.seal(_PAGE_SEAL, {"ns": namespace, "kind": "marker"}),
        }
        try:
            await self._call(
                "put_page", {"slug": full, "content": self._markdown(front, namespace)}
            )
        except RevisionConflictError:
            return False
        return True

    async def list_namespaces(self) -> list[str]:
        """Namespaces with a marker page, found by paging over every page under ``l/``."""
        root = f"{_ROOT}/"
        marker = f"/{_MARKER}"
        found: list[str] = []
        for row in await self._scan(root, want=None, include_internal=True):
            slug = str(row.get("slug") or "")
            if not slug.endswith(marker):
                continue
            namespace = slug[len(root) : -len(marker)]
            if _NAMESPACE.fullmatch(namespace):
                found.append(namespace)
        return found

    async def delete_namespace(self, namespace: str) -> NamespaceRemoval:
        prefix = self._prefix(namespace)
        rows = await self._scan(prefix, want=None, include_internal=True)
        withdrawn = 0
        note = self._codec.retirement_note(
            {"by": _SYSTEM_AUTHOR, "why": f"layer namespace {namespace} was deleted"}
        )
        for row in rows:
            relative = str(row["slug"])[len(prefix) :]
            if not relative.startswith(f"{_FACTS}/"):
                continue
            try:
                payload = await self._verb(
                    "forget", {"id": relative[len(_FACTS) + 1 :], "reason": note}
                )
            except RecordNotFoundError:
                continue
            withdrawn += 1 if payload.get("expired") else 0
        for row in rows:
            await self._call("delete_page", {"slug": str(row["slug"]), "force": True})
        return NamespaceRemoval(pages_removed=len(rows), facts_withdrawn=withdrawn)
