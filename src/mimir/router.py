"""MimirRouter — FastAPI router exposing all Mímir HTTP endpoints.

Mountable on any FastAPI application::

    from mimir.router import MimirRouter
    from mimir.adapters.markdown import MarkdownMimirAdapter

    adapter = MarkdownMimirAdapter(root="~/.ravn/mimir")
    router = MimirRouter(adapter)

    app.include_router(router.router, prefix="/mimir")

Endpoints
---------
GET  /mimir/stats          — page count, categories, last activity
GET  /mimir/pages          — list all pages with metadata
GET  /mimir/page           — read a specific page (?path=...)
GET  /mimir/search         — full-text search (?q=...)
GET  /mimir/log            — last N log entries (?n=50)
GET  /mimir/lint           — current lint report (12 check types, L01–L12)
POST /mimir/lint/fix       — run lint and apply auto-fixes (L05, L11, L12)
GET  /mimir/graph          — nodes + edges for MimirExplorer visualiser
PUT  /mimir/page           — upsert a page (requires write auth)
POST /mimir/ingest         — ingest URL or text (requires write auth)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from posixpath import normpath
from typing import Annotated, Any
from urllib.parse import unquote, urlparse

import httpx
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from niuu.domain.mimir import (
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirSource,
    compute_content_hash,
)
from niuu.ports.mimir import MimirPort
from ravn.adapters.tools._url_security import check_ssrf

logger = logging.getLogger(__name__)
_ALLOWED_INGEST_URL_SCHEMES = {"http", "https"}
_SAFE_INGEST_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
_SAFE_INGEST_QUERY_RE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    page_count: int
    categories: list[str]
    healthy: bool


class PageMetaResponse(BaseModel):
    path: str
    title: str
    summary: str
    category: str
    updated_at: str
    source_ids: list[str]
    type: str = "topic"
    confidence: str = "medium"
    entity_type: str | None = None
    mounts: list[str] | None = None
    updated_by: str = "mimir"
    size: int = 0


class PageResponse(BaseModel):
    path: str
    title: str
    summary: str
    category: str
    updated_at: str
    source_ids: list[str]
    content: str
    type: str = "topic"
    confidence: str = "medium"
    entity_type: str | None = None
    mounts: list[str] | None = None
    updated_by: str = "mimir"
    size: int = 0
    related: list[str] = []


class SearchResult(BaseModel):
    path: str
    title: str
    summary: str
    category: str


class LintIssueResponse(BaseModel):
    id: str
    severity: str
    message: str
    page_path: str
    auto_fixable: bool
    rule: str
    page: str
    mount: str
    assignee: str | None = None
    auto_fix: bool


class LintResponse(BaseModel):
    issues: list[LintIssueResponse]
    pages_checked: int
    issues_found: bool
    summary: dict[str, int]


class GraphNode(BaseModel):
    id: str
    title: str
    category: str


class GraphEdge(BaseModel):
    source: str
    target: str


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class UpsertPageRequest(BaseModel):
    path: str
    content: str
    mount: str | None = None


class IngestRequest(BaseModel):
    title: str
    content: str
    source_type: str = "document"
    origin_url: str | None = None
    mount: str | None = None


class IngestResponse(BaseModel):
    source_id: str
    pages_updated: list[str]


class UrlIngestRequest(BaseModel):
    url: str
    mount: str | None = None


class LintFixRequest(BaseModel):
    issue_ids: list[str] = []


class LintReassignRequest(BaseModel):
    issue_ids: list[str]
    assignee: str


class MountResponse(BaseModel):
    name: str
    role: str
    host: str
    url: str
    priority: int
    categories: list[str] | None
    status: str
    pages: int
    sources: int
    lint_issues: int
    last_write: str
    embedding: str
    size_kb: int
    desc: str


class RegistryMountRequest(BaseModel):
    name: str
    kind: str = "remote"
    lifecycle: str = "registered"
    role: str = "shared"
    url: str = ""
    path: str = ""
    categories: list[str] | None = None
    auth_ref: str | None = None
    default_read_priority: int = 10
    enabled: bool = True
    health_status: str = "unknown"
    health_message: str = ""
    desc: str = ""


class RegistryMountResponse(BaseModel):
    id: str
    name: str
    kind: str
    lifecycle: str
    role: str
    url: str
    path: str
    categories: list[str] | None
    auth_ref: str | None = None
    default_read_priority: int
    enabled: bool
    health_status: str
    health_message: str
    desc: str


class RoutingRuleResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str
    prefix: str
    mount_name: str = Field(alias="mountName")
    priority: int
    active: bool = True
    desc: str | None = None


class RecentWriteResponse(BaseModel):
    id: str
    timestamp: str
    mount: str
    page: str
    ravn: str
    kind: str
    message: str


class EntityMetaResponse(BaseModel):
    path: str
    title: str
    entity_kind: str
    summary: str
    relationship_count: int


class EmbeddingSearchResponse(BaseModel):
    path: str
    title: str
    summary: str
    score: float
    mount_name: str


class DreamCycleResponse(BaseModel):
    id: str
    timestamp: str
    ravn: str
    mounts: list[str]
    pages_updated: int
    entities_created: int
    lint_fixes: int
    duration_ms: int


def _validated_ingest_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if parsed.scheme not in _ALLOWED_INGEST_URL_SCHEMES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported URL scheme: {parsed.scheme or 'unknown'}",
        )
    if not parsed.hostname:
        raise HTTPException(status_code=400, detail="Invalid URL: no hostname")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="Invalid URL: embedded credentials")
    if parsed.fragment:
        raise HTTPException(status_code=400, detail="Invalid URL: fragments are not supported")

    block_reason = check_ssrf(parsed.hostname)
    if block_reason:
        raise HTTPException(status_code=400, detail=block_reason)

    raw_path = parsed.path or "/"
    decoded_path = unquote(raw_path)
    if any(ord(ch) < 32 for ch in decoded_path):
        raise HTTPException(status_code=400, detail="Invalid URL path")
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise HTTPException(status_code=400, detail="Invalid URL path")
    if not _SAFE_INGEST_PATH_RE.fullmatch(decoded_path):
        raise HTTPException(status_code=400, detail="Invalid URL path")

    normalized_path = normpath(decoded_path)
    if not normalized_path.startswith("/"):
        normalized_path = f"/{normalized_path}"
    safe_netloc = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    safe_url = f"{parsed.scheme}://{safe_netloc}{normalized_path}"
    if parsed.query:
        if any(ord(ch) < 32 for ch in parsed.query):
            raise HTTPException(status_code=400, detail="Invalid URL query")
        if not _SAFE_INGEST_QUERY_RE.fullmatch(parsed.query):
            raise HTTPException(status_code=400, detail="Invalid URL query")
        safe_url = f"{safe_url}?{parsed.query}"
    return safe_url


class ActivityEventResponse(BaseModel):
    id: str
    timestamp: str
    kind: str
    mount: str
    ravn: str
    message: str
    page: str | None = None


class RavnBindingResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    ravn_id: str = Field(alias="ravnId")
    ravn_rune: str = Field(alias="ravnRune")
    role: str
    state: str
    mount_names: list[str] = Field(alias="mountNames")
    write_mount: str = Field(alias="writeMount")
    last_dream: DreamCycleResponse | None = Field(default=None, alias="lastDream")
    bio: str
    pages_touched: int = Field(alias="pagesTouched")
    expertise: list[str]
    tools: list[str]


class SourceMetaResponse(BaseModel):
    source_id: str
    title: str
    ingested_at: str
    source_type: str
    id: str | None = None
    origin_type: str | None = None
    origin_url: str | None = None
    origin_path: str | None = None
    ingest_agent: str | None = None
    compiled_into: list[str] = []
    mount_name: str | None = None
    content: str | None = None


class SourceResponse(BaseModel):
    source_id: str
    title: str
    content: str
    source_type: str
    ingested_at: str
    content_hash: str
    origin_url: str | None
    id: str | None = None
    origin_type: str | None = None
    origin_path: str | None = None
    ingest_agent: str | None = None
    compiled_into: list[str] = []
    mount_name: str | None = None


# ---------------------------------------------------------------------------
# Auth dependency (bearer token or SPIFFE — pass-through for now)
# ---------------------------------------------------------------------------


def _require_write_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """Minimal write-auth guard.  Override in production with mTLS or JWT validation."""


_LOG_HEADER_RE = re.compile(r"^## \[(?P<date>[^\]]+)\] (?P<prefix>[^|]+)\| (?P<subject>.+)$")
_KV_TOKEN_RE = re.compile(r"(?P<key>[a-z_]+)=(?P<value>[^\s]+)")


def _stable_id(*parts: str) -> str:
    return compute_content_hash("|".join(parts))[:16]


def _infer_page_type(path: str, category: str) -> str:
    if path.startswith("/entities/") or path.startswith("entities/") or category == "entity":
        return "entity"
    if "/decisions/" in path or category == "decision":
        return "decision"
    if "/preferences/" in path or category == "preference":
        return "preference"
    if "/directives/" in path or category == "directive":
        return "directive"
    return "topic"


def _infer_entity_kind(path: str, title: str, summary: str) -> str:
    haystack = f"{path} {title} {summary}".lower()
    if "/people/" in haystack or " person " in haystack:
        return "person"
    if "/project" in haystack or " project " in haystack:
        return "project"
    if "/component" in haystack or " component " in haystack:
        return "component"
    if "/tech" in haystack or " technology " in haystack:
        return "technology"
    if "/org" in haystack or " organization " in haystack or " organisation " in haystack:
        return "org"
    return "concept"


def _extract_mount_definitions(
    adapter: MimirPort,
    *,
    default_name: str,
    default_role: str,
) -> list[dict[str, Any]]:
    composite_mounts = getattr(adapter, "_mounts", None)
    if composite_mounts:
        return [
            {
                "name": getattr(mount, "name", "local"),
                "role": getattr(mount, "role", "local"),
                "categories": getattr(mount, "categories", None),
                "priority": getattr(mount, "read_priority", 0),
                "port": getattr(mount, "port"),
            }
            for mount in composite_mounts
        ]

    return [
        {
            "name": default_name,
            "role": default_role,
            "categories": None,
            "priority": 0,
            "port": adapter,
        }
    ]


def _resolve_mount_port(
    adapter: MimirPort,
    mount_name: str | None,
    *,
    default_name: str,
) -> tuple[MimirPort, str]:
    if mount_name is None:
        return adapter, default_name

    mount_map = getattr(adapter, "_mount_map", None)
    if mount_map:
        mount = mount_map.get(mount_name)
        if mount is None:
            raise HTTPException(status_code=404, detail=f"Unknown mount: {mount_name}")
        return getattr(mount, "port"), mount_name

    if mount_name == default_name:
        return adapter, mount_name

    raise HTTPException(status_code=404, detail=f"Unknown mount: {mount_name}")


def _get_routing_rule_store(adapter: MimirPort) -> list[dict[str, Any]]:
    store = getattr(adapter, "_http_routing_rules", None)
    if store is None:
        derived: list[dict[str, Any]] = []
        write_routing = getattr(adapter, "_write_routing", None)
        raw_rules = list(getattr(write_routing, "rules", [])) if write_routing is not None else []
        for index, (prefix, mounts) in enumerate(raw_rules):
            if not mounts:
                continue
            derived.append(
                {
                    "id": f"rule-{index + 1}",
                    "prefix": prefix,
                    "mount_name": mounts[0],
                    "priority": index,
                    "active": True,
                    "desc": None,
                }
            )
        setattr(adapter, "_http_routing_rules", derived)
        return derived
    return store


def _sync_routing_rules(adapter: MimirPort) -> None:
    write_routing = getattr(adapter, "_write_routing", None)
    if write_routing is None:
        return
    rules = sorted(
        (rule for rule in _get_routing_rule_store(adapter) if rule.get("active", True)),
        key=lambda item: item["priority"],
    )
    write_routing.rules = [(rule["prefix"], [rule["mount_name"]]) for rule in rules]


def _get_lint_assignment_store(adapter: MimirPort) -> dict[str, str]:
    store = getattr(adapter, "_http_lint_assignments", None)
    if store is None:
        store = {}
        setattr(adapter, "_http_lint_assignments", store)
    return store


def _lint_issue_key(issue_id: str, page_path: str) -> str:
    return f"{issue_id}:{page_path}"


async def _page_mount_map(
    adapter: MimirPort,
    *,
    default_name: str,
    default_role: str,
) -> dict[str, list[str]]:
    mount_map: dict[str, list[str]] = {}
    mounts = _extract_mount_definitions(
        adapter,
        default_name=default_name,
        default_role=default_role,
    )
    for mount in mounts:
        try:
            pages = await mount["port"].list_pages()
        except Exception:
            continue
        for page in pages:
            mount_map.setdefault(page.path, []).append(mount["name"])
    return mount_map


async def _source_page_map(
    port: MimirPort,
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for page in await port.list_pages():
        for source_id in page.source_ids:
            mapping.setdefault(source_id, []).append(page.path)
    return mapping


async def _read_full_sources(port: MimirPort) -> list[MimirSource]:
    sources: list[MimirSource] = []
    for source_meta in await port.list_sources():
        source = await port.read_source(source_meta.source_id)
        if source is not None:
            sources.append(source)
    return sources


async def _parse_log_entries(port: MimirPort) -> list[dict[str, Any]]:
    try:
        raw = await port.read_page("log.md")
    except FileNotFoundError:
        return []

    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw.splitlines():
        match = _LOG_HEADER_RE.match(line)
        if match:
            if current is not None:
                entries.append(current)
            current = {
                "date": match.group("date"),
                "prefix": match.group("prefix").strip(),
                "subject": match.group("subject").strip(),
                "detail": [],
            }
            continue
        if current is not None and line.strip():
            current["detail"].append(line.strip())
    if current is not None:
        entries.append(current)
    return entries


def _parse_log_timestamp(raw_date: str) -> str:
    try:
        return datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return datetime.now(UTC).isoformat()


def _extract_key_values(detail_lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in detail_lines:
        for match in _KV_TOKEN_RE.finditer(line):
            values[match.group("key")] = match.group("value")
    return values


def _parse_count(detail: str, label: str) -> int:
    match = re.search(rf"{label}[=_ ](?P<count>\d+)", detail)
    if match:
        return int(match.group("count"))
    return 0


async def _summarize_mount(
    mount: dict[str, Any],
) -> MountResponse:
    port: MimirPort = mount["port"]
    pages = await port.list_pages()
    sources = await port.list_sources()
    lint_report = await port.lint()
    last_write = ""
    page_timestamps = [page.updated_at for page in pages]
    source_timestamps = [source.ingested_at for source in sources]
    if page_timestamps or source_timestamps:
        last_write = max([*page_timestamps, *source_timestamps]).isoformat()

    url = getattr(port, "_base_url", "")
    host = "embedded"
    if url:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path or "remote"

    return MountResponse(
        name=mount["name"],
        role=mount["role"],
        host=host,
        url=url,
        priority=mount["priority"],
        categories=mount["categories"] or sorted({page.category for page in pages}),
        status="healthy",
        pages=len(pages),
        sources=len(sources),
        lint_issues=len(lint_report.issues),
        last_write=last_write,
        embedding="fts",
        size_kb=0,
        desc=f"{mount['role']} mount",
    )


def _registry_to_response(entry: MimirRegistryEntry) -> RegistryMountResponse:
    return RegistryMountResponse(**entry.model_dump(mode="json"))


def _registry_mount_host(entry: MimirRegistryEntry) -> str:
    if entry.url:
        parsed = urlparse(entry.url)
        return parsed.netloc or parsed.path or "remote"

    if entry.path:
        return entry.path

    return "registered"


def _mount_from_registry(entry: MimirRegistryEntry) -> MountResponse:
    return MountResponse(
        name=entry.name,
        role=entry.role,
        host=_registry_mount_host(entry),
        url=entry.url,
        priority=entry.default_read_priority,
        categories=entry.categories,
        status="down" if entry.enabled else "down",
        pages=0,
        sources=0,
        lint_issues=0,
        last_write="",
        embedding="fts",
        size_kb=0,
        desc=entry.desc or f"registered {entry.role} mount",
    )


async def _merged_mount_responses(
    adapter: MimirPort,
    *,
    default_name: str,
    default_role: str,
    registry_store: MimirRegistryStore | None,
) -> list[MountResponse]:
    live_summaries = [
        await _summarize_mount(mount)
        for mount in _extract_mount_definitions(
            adapter,
            default_name=default_name,
            default_role=default_role,
        )
    ]
    live_by_name = {mount.name: mount for mount in live_summaries}
    entries = registry_store.list_entries() if registry_store is not None else []
    if not entries:
        return sorted(live_summaries, key=lambda mount: mount.priority)

    merged: list[MountResponse] = []
    seen_names: set[str] = set()

    for entry in entries:
        live_mount = live_by_name.get(entry.name)
        if live_mount is None:
            merged.append(_mount_from_registry(entry))
            seen_names.add(entry.name)
            continue

        merged.append(
            live_mount.model_copy(
                update={
                    "priority": entry.default_read_priority,
                    "categories": entry.categories or live_mount.categories,
                    "desc": entry.desc or live_mount.desc,
                }
            )
        )
        seen_names.add(entry.name)

    for live_mount in live_summaries:
        if live_mount.name in seen_names:
            continue
        merged.append(live_mount)

    return sorted(merged, key=lambda mount: mount.priority)


def _decorate_page_meta(meta: MimirPageMeta, mounts: list[str] | None = None) -> PageMetaResponse:
    return PageMetaResponse(
        path=meta.path,
        title=meta.title,
        summary=meta.summary,
        category=meta.category,
        updated_at=meta.updated_at.isoformat(),
        source_ids=meta.source_ids,
        type=_infer_page_type(meta.path, meta.category),
        confidence="medium",
        entity_type=_infer_entity_kind(meta.path, meta.title, meta.summary)
        if _infer_page_type(meta.path, meta.category) == "entity"
        else None,
        mounts=mounts,
        updated_by="mimir",
        size=0,
    )


def _decorate_page(page: MimirPage, mounts: list[str] | None = None) -> PageResponse:
    meta = _decorate_page_meta(page.meta, mounts=mounts)
    return PageResponse(
        **meta.model_dump(),
        content=page.content,
        related=[],
    )


def _decorate_source(
    source: MimirSource,
    *,
    compiled_into: list[str],
    mount_name: str | None = None,
) -> SourceResponse:
    return SourceResponse(
        source_id=source.source_id,
        title=source.title,
        content=source.content,
        source_type=source.source_type,
        ingested_at=source.ingested_at.isoformat(),
        content_hash=source.content_hash,
        origin_url=source.origin_url,
        id=source.source_id,
        origin_type="web" if source.origin_url else "file",
        origin_path=None,
        ingest_agent="mimir",
        compiled_into=compiled_into,
        mount_name=mount_name,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class MimirRouter:
    """Wraps a ``MimirPort`` and exposes it as a FastAPI ``APIRouter``.

    Args:
        adapter: The MimirPort implementation to delegate to.
        name:    Instance name (used in announce events).
        role:    Instance role: ``shared``, ``local``, or ``domain``.
    """

    def __init__(
        self,
        adapter: MimirPort,
        name: str = "local",
        role: str = "local",
        registry_store: MimirRegistryStore | None = None,
    ) -> None:
        self._adapter = adapter
        self._name = name
        self._role = role
        self._registry_store = registry_store
        self.router = APIRouter()
        self._register_routes()

    def _register_routes(self) -> None:
        router = self.router
        adapter = self._adapter

        @router.get("/stats", response_model=StatsResponse)
        async def stats(mount: str | None = Query(default=None)) -> StatsResponse:
            port, _ = _resolve_mount_port(adapter, mount, default_name=self._name)
            pages = await port.list_pages()
            categories = sorted({p.category for p in pages})
            return StatsResponse(
                page_count=len(pages),
                categories=categories,
                healthy=True,
            )

        @router.get("/mounts", response_model=list[MountResponse])
        async def list_mounts() -> list[MountResponse]:
            return await _merged_mount_responses(
                adapter,
                default_name=self._name,
                default_role=self._role,
                registry_store=self._registry_store,
            )

        @router.get("/registry/mounts", response_model=list[RegistryMountResponse])
        async def list_registry_mounts() -> list[RegistryMountResponse]:
            if self._registry_store is None:
                mounts = _extract_mount_definitions(
                    adapter,
                    default_name=self._name,
                    default_role=self._role,
                )
                return [
                    _registry_to_response(
                        MimirRegistryEntry(
                            name=mount["name"],
                            kind="remote" if getattr(mount["port"], "_base_url", "") else "local",
                            role=mount["role"],
                            categories=mount["categories"],
                            url=getattr(mount["port"], "_base_url", ""),
                            default_read_priority=mount["priority"],
                            desc=f"{mount['role']} mount",
                        )
                    )
                    for mount in mounts
                ]

            return [
                _registry_to_response(entry)
                for entry in self._registry_store.list_entries()
            ]

        @router.post("/registry/mounts", response_model=RegistryMountResponse)
        async def create_registry_mount(
            request: RegistryMountRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> RegistryMountResponse:
            if self._registry_store is None:
                raise HTTPException(
                    status_code=501,
                    detail="Registry persistence is not configured",
                )

            entry = MimirRegistryEntry(**request.model_dump())
            self._registry_store.save_entry(entry)
            return _registry_to_response(entry)

        @router.put("/registry/mounts/{entry_id}", response_model=RegistryMountResponse)
        async def update_registry_mount(
            entry_id: str,
            request: RegistryMountRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> RegistryMountResponse:
            if self._registry_store is None:
                raise HTTPException(
                    status_code=501,
                    detail="Registry persistence is not configured",
                )

            existing = self._registry_store.get_entry(entry_id)
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Unknown registry mount: {entry_id}")

            entry = existing.model_copy(update=request.model_dump())
            self._registry_store.save_entry(entry)
            return _registry_to_response(entry)

        @router.delete("/registry/mounts/{entry_id}", status_code=204)
        async def delete_registry_mount(
            entry_id: str,
            _auth: None = Depends(_require_write_auth),
        ) -> None:
            if self._registry_store is None:
                raise HTTPException(
                    status_code=501,
                    detail="Registry persistence is not configured",
                )

            self._registry_store.delete_entry(entry_id)

        @router.get("/routing/rules", response_model=list[RoutingRuleResponse])
        async def list_routing_rules() -> list[RoutingRuleResponse]:
            return [RoutingRuleResponse(**rule) for rule in _get_routing_rule_store(adapter)]

        @router.put("/routing/rules/{rule_id}", response_model=RoutingRuleResponse)
        async def upsert_routing_rule(
            rule_id: str,
            rule: RoutingRuleResponse,
        ) -> RoutingRuleResponse:
            rules = _get_routing_rule_store(adapter)
            payload = rule.model_dump()
            payload["id"] = rule_id
            for index, existing in enumerate(rules):
                if existing["id"] == rule_id:
                    rules[index] = payload
                    break
            else:
                rules.append(payload)
            _sync_routing_rules(adapter)
            return RoutingRuleResponse(**payload)

        @router.delete("/routing/rules/{rule_id}", status_code=204)
        async def delete_routing_rule(rule_id: str) -> None:
            rules = _get_routing_rule_store(adapter)
            rules[:] = [rule for rule in rules if rule["id"] != rule_id]
            _sync_routing_rules(adapter)

        @router.get("/ravns/bindings", response_model=list[RavnBindingResponse])
        async def list_ravn_bindings() -> list[RavnBindingResponse]:
            bindings = getattr(adapter, "_http_ravn_bindings", None)
            if isinstance(bindings, list):
                return [RavnBindingResponse(**binding) for binding in bindings]
            return []

        @router.get("/mounts/recent-writes", response_model=list[RecentWriteResponse])
        async def recent_writes(
            limit: int = Query(default=20, ge=1, le=200),
        ) -> list[RecentWriteResponse]:
            events: list[RecentWriteResponse] = []
            for mount in _extract_mount_definitions(
                adapter,
                default_name=self._name,
                default_role=self._role,
            ):
                port: MimirPort = mount["port"]
                for page in await port.list_pages():
                    events.append(
                        RecentWriteResponse(
                            id=_stable_id(mount["name"], page.path, page.updated_at.isoformat()),
                            timestamp=page.updated_at.isoformat(),
                            mount=mount["name"],
                            page=page.path,
                            ravn="mimir",
                            kind="write",
                            message=page.title,
                        )
                    )
                source_pages = await _source_page_map(port)
                for source in await _read_full_sources(port):
                    events.append(
                        RecentWriteResponse(
                            id=_stable_id(
                                mount["name"],
                                source.source_id,
                                source.ingested_at.isoformat(),
                            ),
                            timestamp=source.ingested_at.isoformat(),
                            mount=mount["name"],
                            page=(source_pages.get(source.source_id) or [""])[0],
                            ravn="mimir",
                            kind="compile",
                            message=source.title,
                        )
                    )
                for entry in await _parse_log_entries(port):
                    detail_text = " ".join(entry["detail"])
                    lower_text = f"{entry['prefix']} {entry['subject']} {detail_text}".lower()
                    if "dream cycle" not in lower_text:
                        continue
                    events.append(
                        RecentWriteResponse(
                            id=_stable_id(mount["name"], entry["subject"], entry["date"]),
                            timestamp=_parse_log_timestamp(entry["date"]),
                            mount=mount["name"],
                            page="",
                            ravn=_extract_key_values(entry["detail"]).get("ravn", "mimir"),
                            kind="dream",
                            message=entry["subject"],
                        )
                    )

            events.sort(key=lambda event: event.timestamp, reverse=True)
            return events[:limit]

        @router.get("/pages", response_model=list[PageMetaResponse])
        async def list_pages(
            category: str | None = Query(default=None),
            mount: str | None = Query(default=None),
        ) -> list[PageMetaResponse]:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            pages = await port.list_pages(category=category)
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
            )
            if mount is not None:
                return [_decorate_page_meta(page, mounts=[resolved_mount]) for page in pages]
            return [
                _decorate_page_meta(page, mounts=mount_map.get(page.path, [self._name]))
                for page in pages
            ]

        @router.get("/page", response_model=PageResponse)
        async def read_page(
            path: str = Query(),
            mount: str | None = Query(default=None),
        ) -> PageResponse:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            try:
                page = await port.get_page(path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Page not found: {path}")
            if mount is not None:
                return _decorate_page(page, mounts=[resolved_mount])
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
            )
            return _decorate_page(page, mounts=mount_map.get(page.meta.path, [self._name]))

        @router.get("/search", response_model=list[SearchResult])
        async def search(
            q: str = Query(),
            mount: str | None = Query(default=None),
        ) -> list[SearchResult]:
            port, _ = _resolve_mount_port(adapter, mount, default_name=self._name)
            pages = await port.search(q)
            return [
                SearchResult(
                    path=p.meta.path,
                    title=p.meta.title,
                    summary=p.meta.summary,
                    category=p.meta.category,
                )
                for p in pages
            ]

        @router.get("/log")
        async def log_entries(
            n: int = Query(default=50),
            mount: str | None = Query(default=None),
        ) -> dict:
            """Return last *n* log entries as raw text (delegated to filesystem adapter)."""
            port, _ = _resolve_mount_port(adapter, mount, default_name=self._name)
            try:
                content = await port.read_page("log.md")
            except FileNotFoundError:
                return {"entries": [], "raw": ""}
            lines = content.splitlines()
            # Each entry starts with "## "
            entries = [ln for ln in lines if ln.startswith("## ")]
            return {"entries": entries[-n:], "raw": content}

        @router.get("/lint", response_model=LintResponse)
        async def lint(mount: str | None = Query(default=None)) -> LintResponse:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            report = await port.lint()
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
            )
            assignments = _get_lint_assignment_store(adapter)
            return _lint_to_response(
                report,
                assignments=assignments,
                mount_lookup=lambda path: (
                    resolved_mount if mount is not None else mount_map.get(path, [self._name])[0]
                ),
            )

        @router.post("/lint/fix", response_model=LintResponse)
        async def lint_fix(
            request: LintFixRequest | None = None,
            mount: str | None = Query(default=None),
        ) -> LintResponse:
            del request
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            report = await port.lint(fix=True)
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
            )
            assignments = _get_lint_assignment_store(adapter)
            return _lint_to_response(
                report,
                assignments=assignments,
                mount_lookup=lambda path: (
                    resolved_mount if mount is not None else mount_map.get(path, [self._name])[0]
                ),
            )

        @router.post("/lint/reassign", response_model=LintResponse)
        async def lint_reassign(request: LintReassignRequest) -> LintResponse:
            assignments = _get_lint_assignment_store(adapter)
            report = await adapter.lint()
            for issue in report.issues:
                if issue.id in request.issue_ids:
                    assignments[_lint_issue_key(issue.id, issue.page_path)] = request.assignee
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
            )
            return _lint_to_response(
                report,
                assignments=assignments,
                mount_lookup=lambda path: mount_map.get(path, [self._name])[0],
            )

        @router.get("/dreams", response_model=list[DreamCycleResponse])
        async def list_dream_cycles(
            limit: int = Query(default=20, ge=1, le=200),
        ) -> list[DreamCycleResponse]:
            cycles: list[DreamCycleResponse] = []
            for mount in _extract_mount_definitions(
                adapter,
                default_name=self._name,
                default_role=self._role,
            ):
                for entry in await _parse_log_entries(mount["port"]):
                    detail_text = " ".join(entry["detail"])
                    lower_text = f"{entry['prefix']} {entry['subject']} {detail_text}".lower()
                    if "dream cycle" not in lower_text:
                        continue
                    values = _extract_key_values(entry["detail"])
                    cycles.append(
                        DreamCycleResponse(
                            id=_stable_id(mount["name"], entry["subject"], entry["date"]),
                            timestamp=_parse_log_timestamp(entry["date"]),
                            ravn=values.get("ravn", "mimir"),
                            mounts=[mount["name"]],
                            pages_updated=int(
                                values.get(
                                    "pages_updated",
                                    _parse_count(detail_text, "pages_updated"),
                                )
                            ),
                            entities_created=int(
                                values.get(
                                    "entities_created",
                                    _parse_count(detail_text, "entities_created"),
                                )
                            ),
                            lint_fixes=int(
                                values.get(
                                    "lint_fixes",
                                    _parse_count(detail_text, "lint_fixes"),
                                )
                            ),
                            duration_ms=int(values.get("duration_ms", 0)),
                        )
                    )
            cycles.sort(key=lambda cycle: cycle.timestamp, reverse=True)
            return cycles[:limit]

        @router.get("/activity", response_model=list[ActivityEventResponse])
        async def activity_log(
            limit: int = Query(default=50, ge=1, le=200),
        ) -> list[ActivityEventResponse]:
            events: list[ActivityEventResponse] = []
            for mount in _extract_mount_definitions(
                adapter,
                default_name=self._name,
                default_role=self._role,
            ):
                port: MimirPort = mount["port"]
                for page in await port.list_pages():
                    events.append(
                        ActivityEventResponse(
                            id=_stable_id(
                                mount["name"],
                                "write",
                                page.path,
                                page.updated_at.isoformat(),
                            ),
                            timestamp=page.updated_at.isoformat(),
                            kind="write",
                            mount=mount["name"],
                            ravn="mimir",
                            message=f"updated {page.title}",
                            page=page.path,
                        )
                    )
                for entry in await _parse_log_entries(port):
                    detail_values = _extract_key_values(entry["detail"])
                    kind = entry["prefix"]
                    if "dream cycle" in f"{entry['subject']} {' '.join(entry['detail'])}".lower():
                        kind = "dream"
                    if kind not in {"ingest", "query", "lint", "dream"}:
                        continue
                    events.append(
                        ActivityEventResponse(
                            id=_stable_id(mount["name"], kind, entry["subject"], entry["date"]),
                            timestamp=_parse_log_timestamp(entry["date"]),
                            kind=kind,
                            mount=mount["name"],
                            ravn=detail_values.get("ravn", "mimir"),
                            message=entry["subject"],
                            page=detail_values.get("page"),
                        )
                    )
            events.sort(key=lambda event: event.timestamp, reverse=True)
            return events[:limit]

        @router.get("/graph", response_model=GraphResponse)
        async def graph(mount: str | None = Query(default=None)) -> GraphResponse:
            port, _ = _resolve_mount_port(adapter, mount, default_name=self._name)
            pages = await port.list_pages()
            nodes = [GraphNode(id=p.path, title=p.title, category=p.category) for p in pages]
            # Build edges from source_ids overlap (pages sharing a source are related)
            source_to_pages: dict[str, list[str]] = {}
            for p in pages:
                for sid in p.source_ids:
                    source_to_pages.setdefault(sid, []).append(p.path)

            edges: list[GraphEdge] = []
            seen: set[tuple[str, str]] = set()
            for page_paths in source_to_pages.values():
                for i, src in enumerate(page_paths):
                    for tgt in page_paths[i + 1 :]:
                        key = (min(src, tgt), max(src, tgt))
                        if key not in seen:
                            seen.add(key)
                            edges.append(GraphEdge(source=src, target=tgt))

            return GraphResponse(nodes=nodes, edges=edges)

        @router.get("/entities", response_model=list[EntityMetaResponse])
        async def list_entities(kind: str | None = Query(default=None)) -> list[EntityMetaResponse]:
            pages = await adapter.list_pages()
            entities = [
                EntityMetaResponse(
                    path=page.path,
                    title=page.title,
                    entity_kind=_infer_entity_kind(page.path, page.title, page.summary),
                    summary=page.summary,
                    relationship_count=0,
                )
                for page in pages
                if (
                    page.path.startswith("entities/")
                    or page.path.startswith("/entities/")
                    or page.category == "entity"
                )
            ]
            if kind is not None:
                entities = [entity for entity in entities if entity.entity_kind == kind]
            return entities

        @router.get("/embeddings/search", response_model=list[EmbeddingSearchResponse])
        async def embedding_search(
            q: str = Query(),
            top_k: int = Query(default=10, ge=1, le=100),
            mount: str | None = Query(default=None),
        ) -> list[EmbeddingSearchResponse]:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            results = await port.search(q)
            return [
                EmbeddingSearchResponse(
                    path=page.meta.path,
                    title=page.meta.title,
                    summary=page.meta.summary,
                    score=max(0.0, 1.0 - index * 0.1),
                    mount_name=resolved_mount,
                )
                for index, page in enumerate(results[:top_k])
            ]

        @router.get("/source", response_model=SourceResponse)
        async def read_source(
            source_id: str = Query(),
            mount: str | None = Query(default=None),
        ) -> SourceResponse:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            source = await port.read_source(source_id)
            if source is None:
                raise HTTPException(status_code=404, detail=f"Source not found: {source_id}")
            compiled_into = (await _source_page_map(port)).get(source.source_id, [])
            return _decorate_source(source, compiled_into=compiled_into, mount_name=resolved_mount)

        @router.get("/sources", response_model=list[SourceMetaResponse])
        async def list_sources(
            unprocessed: bool = Query(default=False),
            origin_type: str | None = Query(default=None),
            mount: str | None = Query(default=None),
        ) -> list[SourceMetaResponse]:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            source_pages = await _source_page_map(port)
            full_sources = await _read_full_sources(port)
            results: list[SourceMetaResponse] = []
            for source in full_sources:
                if origin_type == "web" and not source.origin_url:
                    continue
                if origin_type is not None and origin_type != "web" and source.origin_url:
                    continue
                results.append(
                    SourceMetaResponse(
                        source_id=source.source_id,
                        title=source.title,
                        ingested_at=source.ingested_at.isoformat(),
                        source_type=source.source_type,
                        id=source.source_id,
                        origin_type="web" if source.origin_url else "file",
                        origin_url=source.origin_url,
                        origin_path=None,
                        ingest_agent="mimir",
                        compiled_into=source_pages.get(source.source_id, []),
                        mount_name=resolved_mount,
                    )
                )
            if unprocessed:
                results = [source for source in results if not source.compiled_into]
            return results

        @router.get("/page/sources", response_model=list[SourceMetaResponse])
        async def page_sources(
            path: str = Query(),
            mount: str | None = Query(default=None),
        ) -> list[SourceMetaResponse]:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            try:
                page = await port.get_page(path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Page not found: {path}")
            results: list[SourceMetaResponse] = []
            source_pages = await _source_page_map(port)
            for source_id in page.meta.source_ids:
                source = await port.read_source(source_id)
                if source is None:
                    continue
                results.append(
                    SourceMetaResponse(
                        source_id=source.source_id,
                        title=source.title,
                        ingested_at=source.ingested_at.isoformat(),
                        source_type=source.source_type,
                        id=source.source_id,
                        origin_type="web" if source.origin_url else "file",
                        origin_url=source.origin_url,
                        origin_path=None,
                        ingest_agent="mimir",
                        compiled_into=source_pages.get(source.source_id, []),
                        mount_name=resolved_mount,
                        content=source.content,
                    )
                )
            return results

        @router.put("/page", status_code=204)
        async def upsert_page(
            request: UpsertPageRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> None:
            await adapter.upsert_page(request.path, request.content, mimir=request.mount)

        @router.post("/ingest", response_model=IngestResponse)
        async def ingest_source(
            request: IngestRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> IngestResponse:
            port, _ = _resolve_mount_port(adapter, request.mount, default_name=self._name)
            content_hash = compute_content_hash(request.content)
            source_id = "src_" + content_hash[:16]
            source = MimirSource(
                source_id=source_id,
                title=request.title,
                content=request.content,
                source_type=request.source_type,  # type: ignore[arg-type]
                origin_url=request.origin_url,
                content_hash=content_hash,
                ingested_at=datetime.now(UTC),
            )
            page_paths = await port.ingest(source)
            return IngestResponse(source_id=source_id, pages_updated=page_paths)

        @router.post("/sources/ingest/url", response_model=SourceResponse)
        async def ingest_url(
            request: UrlIngestRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> SourceResponse:
            port, resolved_mount = _resolve_mount_port(
                adapter,
                request.mount,
                default_name=self._name,
            )
            safe_url = _validated_ingest_url(request.url)
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
                    response = await client.get(safe_url)
                    response.raise_for_status()
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to fetch source URL: {exc}",
                ) from exc

            title_match = re.search(
                r"<title>(.*?)</title>",
                response.text,
                re.IGNORECASE | re.DOTALL,
            )
            title = title_match.group(1).strip() if title_match else request.url
            source = MimirSource(
                source_id="src_" + compute_content_hash(response.text)[:16],
                title=title,
                content=response.text,
                source_type="web",
                origin_url=str(response.url),
                content_hash=compute_content_hash(response.text),
                ingested_at=datetime.now(UTC),
            )
            page_paths = await port.ingest(source)
            return _decorate_source(
                source,
                compiled_into=page_paths,
                mount_name=resolved_mount,
            )

        @router.post("/sources/ingest/file", response_model=SourceResponse)
        async def ingest_file(
            file: UploadFile = File(...),
            mount: str | None = Form(default=None),
            _auth: None = Depends(_require_write_auth),
        ) -> SourceResponse:
            port, resolved_mount = _resolve_mount_port(adapter, mount, default_name=self._name)
            raw_bytes = await file.read()
            content = raw_bytes.decode("utf-8", errors="replace")
            source = MimirSource(
                source_id="src_" + compute_content_hash(content)[:16],
                title=file.filename or "uploaded-file",
                content=content,
                source_type="document",
                origin_url=None,
                content_hash=compute_content_hash(content),
                ingested_at=datetime.now(UTC),
            )
            page_paths = await port.ingest(source)
            return _decorate_source(
                source,
                compiled_into=page_paths,
                mount_name=resolved_mount,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _meta_to_response(meta: MimirPageMeta) -> PageMetaResponse:
    return _decorate_page_meta(meta)


def _lint_to_response(
    report: MimirLintReport,
    *,
    assignments: dict[str, str] | None = None,
    mount_lookup: Callable[[str], str] | None = None,
) -> LintResponse:
    assignment_map = assignments or {}
    mount_name_for = mount_lookup or (lambda _path: "local")
    return LintResponse(
        issues=[
            LintIssueResponse(
                id=issue.id,
                severity=issue.severity,
                message=issue.message,
                page_path=issue.page_path,
                auto_fixable=issue.auto_fixable,
                rule=issue.id,
                page=issue.page_path,
                mount=mount_name_for(issue.page_path),
                assignee=assignment_map.get(_lint_issue_key(issue.id, issue.page_path)),
                auto_fix=issue.auto_fixable,
            )
            for issue in report.issues
        ],
        pages_checked=report.pages_checked,
        issues_found=report.issues_found,
        summary=report.summary,
    )
