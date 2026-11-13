"""GBrainBackend — MemoryBackend over one org's gbrain, reached over MCP.

One gbrain per org (design section 7, Tenancy). **Each layer is a gbrain source** of
its own and Mímir reaches it with a gbrain client bound to that source
(:mod:`mimir.adapters.gbrain_layers`, which also says how a namespace maps to a
source id and where the client's credentials come from). gbrain itself therefore
scopes every read and write to the layer; nothing here filters one layer's rows out
of another's.

Inside a layer's source a page is stored under its own slug, and the backend's own
records under slugs that start with ``_`` (callers' slugs may not):

=====================  ==========================================================
Record                 slug in the layer's source
=====================  ==========================================================
page                   ``<slug>``
source (raw material)  ``_sources/<source id>``
fact (entity)          facts are remembered on entity ``<subject>``
fact index             ``_facts/<gbrain fact id>``, one per fact, so removing a
                       layer can withdraw its facts
proposal               ``_proposals/<proposal id>`` (the namespace
                       ``_proposals/<layer namespace>``)
decided proposal       ``_decided/<proposal id>`` (``_decided/<layer namespace>``)
=====================  ==========================================================

A fact's id is ``<gbrain id>@<layer namespace>/<subject>``: the layer says which
source to ask.

**Attribution is sealed.** Every page, source and timeline row carries who wrote
it, at what trust, from what evidence and in which namespace, sealed with an HMAC
key only Mímir holds (``Sealer``) and bound to a hash of the content. A record
whose seal is missing, does not verify, names another namespace or no longer
matches its content reads as unattested, at the lowest trust. Facts carry the
same in their provenance (``FactCodec``, shared with ``GBrainFactAdapter``).

**Search sends no limit.** One ``query`` per layer source, with the limit gbrain's
own search mode and ``search.searchLimit`` give it, unless the caller names one.
Results of several layers are interleaved by rank.

Two gbrain limits remain and are reported when hit: ``recall`` returns at most
``recall_limit`` facts per call and does not page, and ``get_timeline`` takes a limit
and no offset (``timeline_limit``).

gbrain's ``think`` is never called: Mímir answers nothing itself and the people's
search reads the ranked rows.
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
from mimir.adapters.gbrain_layers import (
    Address,
    GBrainLayers,
    call_tool,
    parse_namespace,
)
from mimir.adapters.gbrain_mcp import GBrainMcpClient, Sealer, read_secret
from mimir.backends.gbrain_snippets import readable
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

_SOURCES = "_sources"
_FACTS = "_facts"
_PAGE_SEAL = "mp1"
_ROW_SEAL = "mt1"
_TIMELINE_SENTINEL = "<!-- timeline -->"
_ATTRIBUTION_KEY = "niuu_attribution"
_UNATTESTED_AUTHOR = "external:gbrain"
_SYSTEM_AUTHOR = "operator:memory-gateway"
_RECALL_KNOB = "memory.systems[].recall_limit"
_TIMELINE_KNOB = "memory.systems[].timeline_limit"
_SLUG_KNOB = "memory.systems[].max_slug_chars"

_SEGMENT = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_JSON_FENCE = re.compile(r"```json\n(?P<body>.*)\n```", re.DOTALL)


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


def _server_root(mcp_url: str, base_url: str) -> str:
    """gbrain's server root: *base_url*, or the MCP URL without its ``/mcp``."""
    if base_url:
        return base_url
    stripped = mcp_url.rstrip("/")
    if not stripped.endswith("/mcp"):
        raise ValueError(
            f"cannot derive gbrain's server root from mcp_url {mcp_url!r}, which does not end "
            "in /mcp; set base_url to the server root (where /token and /admin live)"
        )
    return stripped[: -len("/mcp")]


class GBrainBackend(MemoryBackend):
    """One org's gbrain.

    Args:
        mcp_url: gbrain MCP endpoint (``http://acme-brain:3131/mcp``).
        admin_token / admin_token_file: gbrain's owner bootstrap credential
            (``GBRAIN_ADMIN_BOOTSTRAP_TOKEN`` of the server). It is how Mímir creates a
            layer's source and client and recovers the client's secret; it is the only
            gbrain secret Mímir holds. Required.
        base_url: gbrain's server root, for ``/token`` and ``/admin/*``. Defaults to
            ``mcp_url`` without its ``/mcp``.
        signing_key / signing_key_file: the secret that seals attribution; keep it
            with Mímir only. Required.
        protocol_version: the memory-verbs protocol this backend speaks (only 1).
        timeout_seconds: HTTP timeout per call.
        token_refresh_margin_seconds: how long before a layer client's access token
            expires a new one is fetched.
        page_size: rows asked of ``list_pages`` per call while listing. Not a limit on
            what a caller gets: listing continues until the caller's count is met or
            the layer has no more.
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
        *,
        admin_token: str = "",
        admin_token_file: str = "",
        base_url: str = "",
        signing_key: str = "",
        signing_key_file: str = "",
        protocol_version: int = 1,
        timeout_seconds: float = 30.0,
        token_refresh_margin_seconds: float = 30.0,
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
        admin_token = read_secret(admin_token, admin_token_file, what="gbrain admin token")
        if not admin_token:
            raise ValueError(
                "GBrainBackend requires gbrain's admin token (admin_token or admin_token_file): "
                "it is the owner bootstrap credential of the gbrain server "
                "(GBRAIN_ADMIN_BOOTSTRAP_TOKEN), which Mímir uses to give each layer a source "
                "and a client of its own"
            )
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
        if token_refresh_margin_seconds < 0:
            raise ValueError("token_refresh_margin_seconds must not be negative")
        self._layers = GBrainLayers(
            mcp_url=mcp_url,
            base_url=_server_root(mcp_url, base_url),
            admin_token=admin_token,
            timeout_seconds=timeout_seconds,
            token_refresh_margin_seconds=token_refresh_margin_seconds,
        )
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
        await self._layers.close()

    # ------------------------------------------------------------------
    # Layers and slugs
    # ------------------------------------------------------------------

    @staticmethod
    def _layer_address(namespace: str) -> Address:
        """The address of a layer namespace itself (not a proposals namespace)."""
        address = parse_namespace(namespace)
        if address.held:
            raise MemoryValidationError(
                f"{namespace!r} holds proposals, which have pages only: facts, sources, "
                "search and changes belong to the layer"
            )
        return address

    async def _reader(self, address: Address, what: str) -> GBrainMcpClient:
        """The client of a layer that must exist for *what* to be there."""
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            raise RecordNotFoundError(f"no {what}: layer {address.layer} has nothing recorded")
        return mcp

    async def _writer(self, address: Address) -> GBrainMcpClient:
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            raise MemoryBackendError(
                f"layer {address.layer} has no gbrain source yet; create_namespace it before "
                "writing to it"
            )
        return mcp

    def _full(self, address: Address, slug: str, *, internal: bool = False) -> str:
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
        full = address.held + "/".join(segments)
        if len(full) > self._max_slug:
            record_cap_hit(
                _SLUG_KNOB,
                f"page slug {full!r} is {len(full)} characters; gbrain keeps at most "
                f"{self._max_slug}",
            )
            raise MemoryValidationError(
                f"page reference {slug!r} is too long: gbrain slugs hold at most "
                f"{self._max_slug} characters (knob {_SLUG_KNOB})"
            )
        return full

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
    # Listing a layer's pages
    # ------------------------------------------------------------------

    async def _scan(
        self,
        mcp: GBrainMcpClient,
        address: Address,
        *,
        under: str,
        want: int | None,
        include_internal: bool,
    ) -> list[dict[str, Any]]:
        """Every page of the layer under *under* (or the first *want*), by paging ``list_pages``.

        Each row's ``slug`` is the slug in the layer's source.
        """
        found: list[dict[str, Any]] = []
        offset = 0
        while want is None or len(found) < want:
            rows = _rows(
                await call_tool(
                    mcp,
                    "list_pages",
                    {
                        "limit": self._page_size,
                        "offset": offset,
                        "sort": "slug",
                        "source_id": address.source_id,
                    },
                ),
                "pages",
                "results",
            )
            if not rows:
                break
            offset += len(rows)
            for row in rows:
                slug = str(row.get("slug") or "")
                if not slug.startswith(under):
                    continue
                if not include_internal and self._is_internal(slug[len(under) :]):
                    continue
                found.append(row)
                if want is not None and len(found) >= want:
                    break
        return found

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    async def _raw_page(self, mcp: GBrainMcpClient, address: Address, full: str) -> dict[str, Any]:
        payload = await call_tool(
            mcp,
            "get_page",
            {"slug": full, "include_content": True, "source_id": address.source_id},
        )
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
        address = parse_namespace(namespace)
        full = self._full(address, slug)
        mcp = await self._reader(address, f"page {slug}")
        page = self._page_from(namespace, slug, await self._raw_page(mcp, address, full))
        rows = _rows(
            await call_tool(mcp, "get_timeline", {"slug": full, "limit": self._timeline_limit}),
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
        address = parse_namespace(namespace)
        full = self._full(address, slug)
        mcp = await self._writer(address)
        tail = ""
        if expected_revision is not None:
            current = await self._raw_page(mcp, address, full)
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
            await call_tool(mcp, "put_page", arguments)
        except RevisionConflictError as exc:
            if expected_revision is None:
                raise RevisionConflictError(
                    f"page {slug} already exists; read it and pass its revision to rewrite it"
                ) from exc
            raise
        return self._page_from(namespace, slug, await self._raw_page(mcp, address, full))

    async def append_timeline(
        self, namespace: str, slug: str, row: NewTimelineRow, attribution: Attribution
    ) -> None:
        address = parse_namespace(namespace)
        full = self._full(address, slug)
        mcp = await self._reader(address, f"page {slug}")
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
        await call_tool(mcp, "add_timeline_entry", arguments)

    async def delete_page(self, namespace: str, slug: str) -> None:
        address = parse_namespace(namespace)
        full = self._full(address, slug)
        mcp = await self._reader(address, f"page {slug}")
        await call_tool(mcp, "delete_page", {"slug": full, "force": True})

    async def list_pages(self, namespace: str, *, limit: int | None) -> list[PageRef]:
        address = parse_namespace(namespace)
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            return []
        rows = await self._scan(
            mcp, address, under=address.held, want=limit, include_internal=False
        )
        return [
            PageRef(
                slug=str(row["slug"])[len(address.held) :],
                namespace=namespace,
                title=str(row.get("title") or row["slug"]),
                updated_at=_moment(row.get("updated_at")),
            )
            for row in rows
        ]

    async def backlinks(self, namespace: str, slug: str) -> list[PageRef]:
        address = parse_namespace(namespace)
        full = self._full(address, slug)
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            return []
        rows = _rows(await call_tool(mcp, "get_backlinks", {"slug": full}), "backlinks", "links")
        refs: dict[str, PageRef] = {}
        for row in rows:
            source = str(row.get("from_slug") or row.get("source_slug") or row.get("slug") or "")
            if not source.startswith(address.held):
                continue
            relative = source[len(address.held) :]
            if not relative or self._is_internal(relative):
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
        address = self._layer_address(namespace)
        source_id = compute_source_id(source.content)
        full = self._full(address, f"{_SOURCES}/{source_id}", internal=True)
        mcp = await self._writer(address)
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
            await call_tool(
                mcp, "put_page", {"slug": full, "content": self._markdown(front, fenced)}
            )
        except RevisionConflictError:
            return await self.get_source(namespace, source_id), False
        return await self.get_source(namespace, source_id), True

    async def get_source(self, namespace: str, source_id: str) -> SourceRecord:
        address = self._layer_address(namespace)
        full = self._full(address, f"{_SOURCES}/{source_id}", internal=True)
        mcp = await self._reader(address, f"source {source_id}")
        raw = await self._raw_page(mcp, address, full)
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

    async def list_sources(self, namespace: str, *, limit: int | None) -> list[SourceRef]:
        address = self._layer_address(namespace)
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            return []
        under = f"{_SOURCES}/"
        rows = await self._scan(mcp, address, under=under, want=limit, include_internal=True)
        return [
            SourceRef(
                id=str(row["slug"])[len(under) :],
                namespace=namespace,
                title=str(row.get("title") or row["slug"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------

    def _entity(self, address: Address, subject: str) -> str:
        return self._full(address, subject)

    @staticmethod
    def _entity_id(layer: str, entity: str) -> str:
        """The entity part of a fact id: the layer to ask, then the subject."""
        return f"{layer}/{entity}"

    def _locate(self, fact_id_: str) -> tuple[str, str, str]:
        """``(raw gbrain id, layer, subject)`` of a fact id this backend made."""
        raw, entity_id = split_fact_id(fact_id_)
        layer, _, subject = entity_id.partition("/")
        try:
            self._layer_address(layer)
        except MemoryValidationError as exc:
            raise RecordNotFoundError(f"no fact {fact_id_}: {exc}") from exc
        if not subject:
            raise RecordNotFoundError(f"no fact {fact_id_}")
        return raw, layer, subject

    def _decode(self, row: dict[str, Any], layer: str) -> LayeredFact | None:
        """The fact if it is ours: sealed into the layer whose source it was read from."""
        entity = str(row.get("entity_slug") or "")
        if not entity:
            return None
        fact, sealed = self._codec.decode(
            row, subject=entity, id_entity=self._entity_id(layer, entity)
        )
        if sealed != layer:
            return None
        return LayeredFact(fact=fact, namespace=layer)

    async def _verb(
        self, mcp: GBrainMcpClient, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        payload = await call_tool(mcp, name, arguments)
        if not isinstance(payload, dict):
            raise MemoryBackendError(f"gbrain {name} returned an unusable result: {payload}")
        return payload

    async def _fact_rows(
        self,
        mcp: GBrainMcpClient,
        address: Address,
        entity: str | None,
        *,
        include_expired: bool,
        contains: str | None,
    ) -> tuple[list[dict[str, Any]], list[Omission]]:
        arguments: dict[str, Any] = {
            "limit": self._recall_limit,
            "include_expired": include_expired,
            "source_id": address.source_id,
        }
        if entity:
            arguments["entity"] = entity
        if contains:
            arguments["grep"] = contains
        rows = _rows(await self._verb(mcp, "recall", arguments), "facts")
        omitted: list[Omission] = []
        if len(rows) >= self._recall_limit:
            message = (
                f"gbrain returned {len(rows)} facts for {entity or address.layer}, its per-call "
                "cap; there may be more. Narrow the request or retire stale facts"
            )
            record_cap_hit(_RECALL_KNOB, message)
            omitted.append(Omission(OmissionReason.BACKEND_LIMIT, 0, message))
        return rows, omitted

    async def remember(self, namespace: str, new: NewFact) -> RememberResult:
        address = self._layer_address(namespace)
        entity = self._entity(address, new.draft.subject)
        mcp = await self._writer(address)
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
        payload = await self._verb(mcp, "remember", arguments)
        raw_id, slug, status = payload.get("id"), payload.get("entity_slug"), payload.get("status")
        if (
            raw_id in (None, "")
            or not slug
            or status not in ("inserted", "duplicate", "superseded")
        ):
            raise MemoryBackendError(f"gbrain remember returned an unusable result: {payload}")
        if slug != entity:
            await call_tool(
                mcp,
                "forget",
                {
                    "id": str(raw_id),
                    "reason": self._codec.retirement_note(
                        {"by": _SYSTEM_AUTHOR, "why": f"filed under {slug}, not {entity}"}
                    ),
                },
            )
            raise MemoryBackendError(
                f"gbrain filed the fact under entity {slug!r} instead of {entity!r}; it was "
                "withdrawn. Remember it with a subject that is not an alias of another page"
            )
        stored = await self.get_fact(fact_id(raw_id, self._entity_id(address.layer, entity)))
        if status != "duplicate":
            await self._index_fact(mcp, address, str(raw_id), stored.fact.id)
        if status == "superseded":
            await self._refuse_trust_inversion(mcp, address, stored)
        return RememberResult(fact=stored, status=status)

    async def _index_fact(
        self, mcp: GBrainMcpClient, address: Address, raw_id: str, full_id: str
    ) -> None:
        full = self._full(address, f"{_FACTS}/{raw_id}", internal=True)
        front = {"title": full_id, "type": "niuu-fact-ref", "fact_id": full_id}
        try:
            await call_tool(
                mcp, "put_page", {"slug": full, "content": self._markdown(front, full_id)}
            )
        except RevisionConflictError:
            return
        except MemoryBackendError as exc:
            raise MemoryBackendError(
                f"fact {full_id} is stored, but its index page in {address.layer} could not be "
                f"written ({exc}); removing the layer would not withdraw it. Retire the "
                "fact and remember it again"
            ) from exc

    async def _refuse_trust_inversion(
        self, mcp: GBrainMcpClient, address: Address, new: LayeredFact
    ) -> None:
        _, _, entity = self._locate(new.fact.id)
        payload = await self._verb(
            mcp,
            "recall",
            {
                "entity": entity,
                "supersessions": True,
                "limit": self._recall_limit,
                "source_id": address.source_id,
            },
        )
        entity_id = self._entity_id(address.layer, entity)
        for row in _rows(payload, "facts"):
            if row.get("superseded_by") in (None, ""):
                continue
            if fact_id(row["superseded_by"], entity_id) != new.fact.id:
                continue
            retired = self._decode(row, address.layer)
            if retired is not None and retired.fact.trust > new.fact.trust:
                raise TrustError(
                    f"gbrain retired {retired.fact.trust.label}-level fact {retired.fact.id} "
                    f"because the {new.fact.trust.label}-level fact {new.fact.id} is similar to "
                    "it. Re-remember the old claim at its level through a caller of that level"
                )

    async def get_fact(self, fact_id_: str) -> LayeredFact:
        raw, layer, subject = self._locate(fact_id_)
        address = self._layer_address(layer)
        mcp = await self._reader(address, f"fact {fact_id_}")
        rows, omitted = await self._fact_rows(
            mcp, address, subject, include_expired=True, contains=None
        )
        for row in rows:
            if str(row.get("fact_id") or row.get("id")) != raw:
                continue
            found = self._decode(row, layer)
            if found is None:
                raise RecordNotFoundError(f"no fact {fact_id_} written through the gateway")
            return found
        if omitted:
            raise MemoryBackendError(
                f"fact {fact_id_} was not among the {len(rows)} facts gbrain returned for "
                f"{subject}, which is its per-call cap; it may exist beyond it"
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
        found: list[LayeredFact] = []
        omitted: list[Omission] = []
        for namespace in namespaces:
            address = self._layer_address(namespace)
            entity = self._entity(address, subject) if subject else None
            mcp = await self._layers.client(address.layer, create=False)
            if mcp is None:
                continue
            rows, cut = await self._fact_rows(
                mcp, address, entity, include_expired=moment < now, contains=contains
            )
            omitted.extend(cut)
            for row in rows:
                fact = self._decode(row, address.layer)
                if fact is not None:
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
        raw, _, _ = self._locate(fact_id_)
        note: dict[str, Any] = {"by": by, "why": why}
        if superseded_by:
            note["sb"] = superseded_by
        mcp = await self._writer(self._layer_address(namespace))
        payload = await self._verb(
            mcp, "forget", {"id": raw, "reason": self._codec.retirement_note(note)}
        )
        if "expired" not in payload:
            raise MemoryBackendError(f"gbrain forget returned an unusable result: {payload}")
        return await self.get_fact(fact_id_)

    async def entity(self, namespace: str, subject: str) -> EntityLayer:
        address = self._layer_address(namespace)
        full = self._entity(address, subject)
        try:
            page: PageRecord | None = await self.get_page(namespace, subject)
        except RecordNotFoundError:
            page = None
        mcp = await self._layers.client(address.layer, create=False)
        aliases: tuple[str, ...] = ()
        links: list[str] = []
        if mcp is not None:
            payload = await self._verb(mcp, "entity", {"name": full})
            card = payload.get("card") if payload.get("found") else None
            if isinstance(card, dict) and (card.get("entity") or {}).get("slug") == full:
                aliases = tuple(str(a) for a in card.get("aka") or [] if a)
                for edge in card.get("edges") or []:
                    if not isinstance(edge, dict):
                        continue
                    target = str(edge.get("to_slug") or edge.get("slug") or "")
                    if target and not self._is_internal(target):
                        links.append(target)
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
        limit: int | None,
    ) -> list[SearchHit]:
        """Ranked hits of these layers: one gbrain ``query`` per layer source.

        The query carries no ``limit`` unless the caller names one, so gbrain applies the
        search mode and ``search.searchLimit`` its operator configured; the backend does
        not page, cap or truncate. Hits of several layers are interleaved by rank (each
        source ranks its own; their scores do not compare).
        """
        if not query.strip():
            raise MemoryValidationError("a search needs a query")
        ranked: list[list[SearchHit]] = []
        for namespace in namespaces:
            address = self._layer_address(namespace)
            mcp = await self._layers.client(address.layer, create=False)
            if mcp is None:
                continue
            arguments: dict[str, Any] = {
                "query": query,
                "source_id": address.source_id,
                "expand": False,
                "autocut": False,
            }
            if limit is not None:
                arguments["limit"] = limit
            rows = _rows(await call_tool(mcp, "query", arguments), "results")
            hits: dict[str, SearchHit] = {}
            for row in rows:
                hit = self._hit(row, address, kinds)
                if hit is not None:
                    hits.setdefault(f"{hit.kind}/{hit.ref}", hit)
            ranked.append(list(hits.values()))
        merged = [
            group[rank]
            for rank in range(max((len(g) for g in ranked), default=0))
            for group in ranked
            if rank < len(group)
        ]
        return merged if limit is None else merged[:limit]

    def _hit(
        self, row: dict[str, Any], address: Address, kinds: frozenset[HitKind]
    ) -> SearchHit | None:
        returned = row.get("source_id")
        if returned not in (None, address.source_id):
            raise MemoryBackendError(
                f"gbrain returned a hit from source {returned} for layer {address.layer} "
                f"(source {address.source_id}); the layer's client is bound to the wrong source"
            )
        relative = str(row.get("slug") or "")
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
            namespace=address.layer,
            title=str(row.get("title") or relative),
            snippet=readable(str(snippet), source=kind is HitKind.SOURCE),
        )

    # ------------------------------------------------------------------
    # Changes
    # ------------------------------------------------------------------

    async def delta(self, namespaces: Sequence[str], *, since: str, limit: int) -> ChangeSet:
        """Pages and facts changed in one layer after *since*, oldest first.

        A change cursor follows one source's clock, so exactly one layer is named.
        """
        if len(namespaces) != 1:
            raise MemoryValidationError(
                f"changes are read one layer at a time (a cursor follows one gbrain source); "
                f"got {len(namespaces)} layers"
            )
        address = self._layer_address(namespaces[0])
        mcp = await self._layers.client(address.layer, create=False)
        if mcp is None:
            return ChangeSet(pages=(), facts=(), cursor=since, has_more=False)
        moment, _, after_slug = since.partition("|")
        pages: list[PageRef] = []
        facts: dict[str, LayeredFact] = {}
        has_more = True
        while has_more and len(pages) < limit:
            arguments: dict[str, Any] = {"since": moment}
            if after_slug:
                arguments["since_slug"] = after_slug
            payload = await self._verb(mcp, "delta", arguments)
            for row in _rows(payload, "facts"):
                fact = self._decode(row, address.layer)
                if fact is not None:
                    facts.setdefault(fact.fact.id, fact)
            batch = _rows(payload, "pages")
            stopped_inside = False
            for index, row in enumerate(batch):
                slug = str(row.get("slug") or "")
                # The cursor moves past every page read: delivery resumes after it.
                moment, after_slug = str(row.get("updated_at") or moment), slug
                if not self._is_internal(slug):
                    pages.append(
                        PageRef(
                            slug=slug,
                            namespace=address.layer,
                            title=str(row.get("title") or slug),
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
        """Make a layer ready: its gbrain source and client exist.

        A proposals namespace is part of its layer's source, so it makes the layer ready
        too. False when the source already existed.
        """
        return await self._layers.create(parse_namespace(namespace).layer)

    async def list_namespaces(self) -> list[str]:
        """The layers that have a source (or a client left over from a removed one).

        Proposals namespaces are not listed: they are part of their layer's source and go
        with it.
        """
        return await self._layers.layers()

    async def delete_namespace(self, namespace: str) -> NamespaceRemoval:
        """Remove a layer: withdraw its facts, remove its source, revoke its client.

        A proposals namespace loses only its own pages.
        """
        address = parse_namespace(namespace)
        mcp = await self._layers.client(address.layer, create=False)
        if address.held:
            if mcp is None:
                return NamespaceRemoval(pages_removed=0, facts_withdrawn=0)
            rows = await self._scan(
                mcp, address, under=address.held, want=None, include_internal=True
            )
            for row in rows:
                await call_tool(mcp, "delete_page", {"slug": str(row["slug"]), "force": True})
            return NamespaceRemoval(pages_removed=len(rows), facts_withdrawn=0)
        withdrawn = 0 if mcp is None else await self._withdraw_facts(mcp, address)
        pages = await self._layers.remove(address.layer)
        return NamespaceRemoval(pages_removed=pages, facts_withdrawn=withdrawn)

    async def _withdraw_facts(self, mcp: GBrainMcpClient, address: Address) -> int:
        under = f"{_FACTS}/"
        note = self._codec.retirement_note(
            {"by": _SYSTEM_AUTHOR, "why": f"layer namespace {address.layer} was deleted"}
        )
        withdrawn = 0
        for row in await self._scan(mcp, address, under=under, want=None, include_internal=True):
            raw_id = str(row["slug"])[len(under) :]
            try:
                payload = await self._verb(mcp, "forget", {"id": raw_id, "reason": note})
            except RecordNotFoundError:
                continue
            withdrawn += 1 if payload.get("expired") else 0
        return withdrawn
