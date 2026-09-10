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
GET  /mimir/doctor         — health-check report (8 checks, D01–D08)
GET  /mimir/graph          — nodes + edges for MimirExplorer visualiser
PUT  /mimir/page           — upsert a page (requires write auth)
POST /mimir/ingest         — ingest URL or text (requires write auth)
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from posixpath import normpath
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlparse

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field

from mimir.compiled_truth import CompiledTruthPage
from mimir.compiled_truth import parse_page as parse_compiled_truth_page
from mimir.ports.deployment import DeploymentRequest, KnowledgeDeploymentPort
from mimir.registry import MimirRegistryEntry, MimirRegistryStore
from niuu.domain.knowledge_graph import cross_mount_source_edges
from niuu.domain.mimir import (
    OPERATIONAL_SOURCE_TYPES,
    MimirLintReport,
    MimirPage,
    MimirPageMeta,
    MimirSource,
    compute_content_hash,
    compute_source_id,
)
from niuu.ports.mimir import MimirPort
from ravn.adapters.tools._url_security import check_ssrf
from ravn.domain.exceptions import MimirUnavailableError

logger = logging.getLogger(__name__)
_ALLOWED_INGEST_URL_SCHEMES = {"http", "https"}
_SAFE_INGEST_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")
_SAFE_INGEST_QUERY_RE = re.compile(r"^[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")
# Recent-query rows returned by GET /eval/queries for the Analytics view.
_RECENT_QUERY_LIMIT = 20

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StatsResponse(BaseModel):
    page_count: int
    categories: list[str]
    healthy: bool


class SummaryResponse(BaseModel):
    """Cheap scale/health summary of one mount — see ``MimirMountSummary``."""

    page_count: int
    source_count: int
    categories: list[str]
    last_write: str
    lint_issues: int
    # Empty when lint has never run; ``lint_issues`` is then unknown, not zero.
    lint_checked_at: str


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
    zones: (
        list[
            KeyFactsZoneResponse
            | RelationshipsZoneResponse
            | AssessmentZoneResponse
            | TimelineZoneResponse
        ]
        | None
    ) = None


class KeyFactsZoneResponse(BaseModel):
    kind: Literal["key-facts"] = "key-facts"
    items: list[str]


class RelationshipZoneItemResponse(BaseModel):
    slug: str
    note: str


class RelationshipsZoneResponse(BaseModel):
    kind: Literal["relationships"] = "relationships"
    items: list[RelationshipZoneItemResponse]


class AssessmentZoneResponse(BaseModel):
    kind: Literal["assessment"] = "assessment"
    text: str


class TimelineZoneItemResponse(BaseModel):
    date: str
    note: str
    source: str


class TimelineZoneResponse(BaseModel):
    kind: Literal["timeline"] = "timeline"
    items: list[TimelineZoneItemResponse]


class SearchResult(BaseModel):
    path: str
    title: str
    summary: str
    category: str
    # Populated only when the search is called with ?debug=true (NIU-1057).
    score: float | None = None
    score_breakdown: dict[str, float] | None = None


class EntityResponse(BaseModel):
    slug: str
    title: str
    type: str | None = None
    confidence: str | None = None
    updated_at: str
    path: str


class RelatedPageResponse(BaseModel):
    path: str
    hop: int
    rel: str | None = None
    direction: str


class FactEvidenceResponse(BaseModel):
    fact: str
    proof_count: int
    trend: str
    latest_support: str | None = None
    supporting_dates: list[str] = []
    source_proof_count: int = 0


class ReviseBeliefRequest(BaseModel):
    path: str
    old_fact: str
    new_fact: str
    attribution: str


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
    path: str = ""
    kind: str = "page"
    summary: str = ""
    mount: str = ""
    source_ids: list[str] = Field(default_factory=list)


class GraphEdge(BaseModel):
    source: str
    target: str
    type: str = "shared_source"


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
    consolidated: list[str] = []


class UrlIngestRequest(BaseModel):
    url: str
    mount: str | None = None


class LintFixRequest(BaseModel):
    issue_ids: list[str] = []


class LintReassignRequest(BaseModel):
    issue_ids: list[str]
    assignee: str


class MountResponse(BaseModel):
    access_scope: str = "unknown"
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
    # When empty, lint has never run on this mount and ``lint_issues`` is
    # unknown rather than clean — summarising deliberately does not lint.
    lint_checked_at: str = ""
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
    adapter: str = ""
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)
    auth_ref: str | None = None
    default_read_priority: int = 10
    enabled: bool = True
    health_status: str = "unknown"
    health_message: str = ""
    desc: str = ""


class RegistryMountResponse(BaseModel):
    access_scope: str = "unknown"
    id: str
    name: str
    kind: str
    lifecycle: str
    role: str
    url: str
    path: str
    categories: list[str] | None
    adapter: str = ""
    kwargs: dict[str, Any] = Field(default_factory=dict)
    secret_kwargs_env: dict[str, str] = Field(default_factory=dict)
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


def _require_deploy_auth(
    user: Annotated[str | None, Header(alias="x-auth-user-id")] = None,
    roles: Annotated[str, Header(alias="x-auth-roles")] = "",
    tenant: Annotated[str, Header(alias="x-auth-tenant")] = "",
) -> None:
    """Identity is supplied by the authenticated Niuu/Envoy gateway."""
    from niuu.adapters.identity_headers import parse_roles_header

    # Accept the configured gateway claim and its platform-normalized form.
    if (
        not user
        or not tenant
        or not {"admin", "volundr:admin"}.intersection(parse_roles_header(roles))
    ):
        raise HTTPException(
            403, "Instance deployment requires an authenticated tenant administrator"
        )


def _require_write_auth(authorization: Annotated[str | None, Header()] = None) -> None:
    """Minimal write-auth guard.  Override in production with mTLS or JWT validation."""


_LOG_HEADER_RE = re.compile(r"^## \[(?P<date>[^\]]+)\] (?P<prefix>[^|]+)\| (?P<subject>.+)$")
_KV_TOKEN_RE = re.compile(r"(?P<key>[a-z_]+)=(?P<value>[^\s]+)")
_RELATIONSHIP_LINE_RE = re.compile(r"^- \[\[(?P<slug>[^\]]+)\]\]\s*(?:—|-)\s*(?P<note>.+)$")


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
    mounts: list[dict[str, Any]] | None = None,
    category: str | None = None,
    prefix: str | None = None,
) -> dict[str, list[str]]:
    """Map page path → the mounts holding it, scoped the same way the caller is.

    The scope matters: this decorates a page listing, so it only needs the pages
    in that listing. Asking each mount for its whole corpus regardless of filter
    silently undid the caller's ``prefix`` — ``GET /mimir/pages?prefix=...``
    scoped its own read and then walked all 578 pages here anyway, which is the
    cost Ting pays once per campaign and 18 times per research page view.
    """
    mount_map: dict[str, list[str]] = {}
    mounts = mounts or _extract_mount_definitions(
        adapter,
        default_name=default_name,
        default_role=default_role,
    )
    for mount in mounts:
        try:
            pages = await mount["port"].list_pages(category=category, prefix=prefix)
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
    """Load every raw source with its content.

    This holds the entire corpus in memory, which is enough to OOM the service
    on a large mount — listings and activity feeds must use ``list_sources()``
    instead. Only evidence computation legitimately needs the bodies, because it
    matches facts against source text, including sources a page does not yet
    cite.
    """
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
    summary = await port.summarize()

    url = getattr(port, "_base_url", "")
    host = "embedded"
    if url:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path or "remote"

    return MountResponse(
        name=mount["name"],
        role=mount["role"],
        access_scope=mount.get("access_scope", "unknown"),
        host=host,
        url=url,
        priority=mount["priority"],
        categories=mount["categories"] or summary.categories,
        status="healthy",
        pages=summary.page_count,
        sources=summary.source_count,
        lint_issues=summary.lint_issues,
        lint_checked_at=(
            summary.lint_checked_at.isoformat() if summary.lint_checked_at is not None else ""
        ),
        last_write=summary.last_write.isoformat() if summary.last_write is not None else "",
        embedding="fts",
        size_kb=0,
        desc=f"{mount.get('access_scope', 'unknown')} knowledge instance",
    )


def _registry_to_response(entry: MimirRegistryEntry) -> RegistryMountResponse:
    return RegistryMountResponse(
        **entry.model_dump(mode="json"),
        access_scope="tenant" if entry.tenant_id else "global",
    )


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
        access_scope="tenant" if entry.tenant_id else "global",
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
        desc=entry.desc or "Registered knowledge connection",
    )


async def _merged_mount_responses(
    adapter: MimirPort,
    *,
    default_name: str,
    default_role: str,
    registry_store: MimirRegistryStore | None,
    mounts: list[dict[str, Any]] | None = None,
    tenant_id: str = "",
) -> list[MountResponse]:
    live_summaries = [
        await _summarize_mount(mount)
        for mount in (
            mounts
            or _extract_mount_definitions(
                adapter,
                default_name=default_name,
                default_role=default_role,
            )
        )
    ]
    live_by_name = {mount.name: mount for mount in live_summaries}
    entries = registry_store.list_entries(tenant_id=tenant_id) if registry_store is not None else []
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


def _extract_compiled_subsection(compiled_truth: str, heading: str) -> str:
    pattern = re.compile(
        rf"^### {re.escape(heading)}\s*\n(.*?)(?=^### |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(compiled_truth)
    if not match:
        return ""
    return match.group(1).strip()


def _extract_assessment(compiled_truth: str) -> str:
    assessment = _extract_compiled_subsection(compiled_truth, "Assessment")
    if assessment:
        return assessment
    return ""


def _extract_key_facts(compiled_truth: str) -> list[str]:
    key_facts = _extract_compiled_subsection(compiled_truth, "Key Facts")
    if not key_facts:
        return []
    return [
        line[2:].strip()
        for line in key_facts.splitlines()
        if line.strip().startswith("- ") and line[2:].strip()
    ]


def _extract_relationships(compiled_truth: str) -> list[RelationshipZoneItemResponse]:
    relationships = _extract_compiled_subsection(compiled_truth, "Relationships")
    if not relationships:
        return []

    items: list[RelationshipZoneItemResponse] = []
    for line in relationships.splitlines():
        match = _RELATIONSHIP_LINE_RE.match(line.strip())
        if match is None:
            continue
        items.append(
            RelationshipZoneItemResponse(
                slug=match.group("slug").strip(),
                note=match.group("note").strip(),
            )
        )
    return items


def _decorate_page_zones(
    parsed: CompiledTruthPage,
) -> (
    list[
        KeyFactsZoneResponse
        | RelationshipsZoneResponse
        | AssessmentZoneResponse
        | TimelineZoneResponse
    ]
    | None
):
    zones: list[
        KeyFactsZoneResponse
        | RelationshipsZoneResponse
        | AssessmentZoneResponse
        | TimelineZoneResponse
    ] = []

    key_facts = _extract_key_facts(parsed.compiled_truth)
    if key_facts:
        zones.append(KeyFactsZoneResponse(items=key_facts))

    relationships = _extract_relationships(parsed.compiled_truth)
    if relationships:
        zones.append(RelationshipsZoneResponse(items=relationships))

    assessment = _extract_assessment(parsed.compiled_truth)
    if assessment:
        zones.append(AssessmentZoneResponse(text=assessment))
    elif parsed.compiled_truth.strip() and not key_facts and not relationships:
        zones.append(AssessmentZoneResponse(text=parsed.compiled_truth.strip()))

    if parsed.timeline_entries:
        zones.append(
            TimelineZoneResponse(
                items=[
                    TimelineZoneItemResponse(
                        date=entry.date,
                        note=entry.description,
                        source=entry.source,
                    )
                    for entry in parsed.timeline_entries
                ]
            )
        )

    return zones or None


def _decorate_page(page: MimirPage, mounts: list[str] | None = None) -> PageResponse:
    meta = _decorate_page_meta(page.meta, mounts=mounts)
    parsed = parse_compiled_truth_page(page.content)
    return PageResponse(
        **meta.model_dump(),
        content=page.content,
        related=parsed.related_entities,
        zones=_decorate_page_zones(parsed),
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
        eval_capture_dir: Path | None = None,
        deployment: KnowledgeDeploymentPort | None = None,
        public_url: str = "",
        tenant_id: str = "",
    ) -> None:
        self._owner_tenant = tenant_id
        self._public_url = public_url
        self._tenant: ContextVar[str] = ContextVar("mimir_tenant", default="")
        self._deployed_mounts: ContextVar[list] = ContextVar("mimir_deployed_mounts", default=[])
        self._deployment = deployment
        self._adapter = adapter
        self._name = name
        self._role = role
        self._registry_store = registry_store
        self._registry_local_ports: dict[str, tuple[str, MimirPort]] = {}
        self._eval_capture_dir = eval_capture_dir
        self.router = APIRouter(dependencies=[Depends(self._request_scope)])
        self._register_routes()

    async def _request_scope(self, request: Request):
        tenant = (
            request.headers.get("x-auth-tenant", "")
            if request.headers.get("x-auth-user-id")
            else ""
        )
        if (
            request.headers.get("x-auth-user-id")
            and not tenant
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        ):
            raise HTTPException(403, "An authenticated tenant is required for knowledge changes")
        if self._owner_tenant and tenant != self._owner_tenant:
            raise HTTPException(403, "Knowledge instance belongs to a different tenant")
        tenant_token = self._tenant.set(tenant)
        mounts_token = self._deployed_mounts.set([])
        try:
            if self._deployment is not None and "/deployments" not in request.url.path:
                self._deployed_mounts.set(
                    await self._deployment.discover_mounts(
                        tenant, request.headers.get("authorization", "")
                    )
                )
            yield
        finally:
            for mount in self._deployed_mounts.get():
                if mount.get("close_after_request"):
                    await mount["port"].aclose()
            self._deployed_mounts.reset(mounts_token)
            self._tenant.reset(tenant_token)

    def _validate_registry_write(self, request: RegistryMountRequest) -> None:
        if not self._tenant.get():
            return
        if request.kind != "remote" or request.path or request.secret_kwargs_env:
            raise HTTPException(
                422, "Tenant connections cannot access host files or environment secrets"
            )
        if request.adapter not in {"", "ravn.adapters.mimir.http.HttpMimirAdapter"}:
            raise HTTPException(422, "Native adapters must be provisioned by the deployment target")
        if set(request.kwargs) - {"base_url", "mount", "timeout"}:
            raise HTTPException(422, "Unsupported tenant connection settings")

    def _registry_entry_by_name(self, mount_name: str) -> MimirRegistryEntry | None:
        if self._registry_store is None:
            return None
        for entry in self._registry_store.list_entries(tenant_id=self._tenant.get()):
            if entry.name == mount_name:
                return entry
        return None

    def _registry_port(self, mount_name: str) -> MimirPort | None:
        entry = self._registry_entry_by_name(mount_name)
        if entry is None or not entry.enabled:
            return None
        endpoint = entry.path if entry.kind == "local" else entry.url
        if not endpoint and not entry.adapter:
            return None
        if entry.auth_ref and not entry.adapter:
            raise ValueError(
                "Authenticated connections must use a configured mount adapter; "
                "auth_ref resolution is not configured"
            )
        cache_key = entry.model_dump_json()
        cached = self._registry_local_ports.get(mount_name)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        if entry.adapter:
            from niuu.utils import import_class, resolve_secret_kwargs

            cls = import_class(entry.adapter)
            if not isinstance(cls, type) or not issubclass(cls, MimirPort):
                raise TypeError(f"Registry adapter {entry.adapter} must implement MimirPort")
            port = cls(**resolve_secret_kwargs(entry.kwargs, entry.secret_kwargs_env))
        elif entry.kind == "remote":
            from ravn.adapters.mimir.http import HttpMimirAdapter

            port = HttpMimirAdapter(base_url=entry.url)
        else:
            from mimir.adapters.markdown import MarkdownMimirAdapter

            port = MarkdownMimirAdapter(root=entry.path)
        self._registry_local_ports[mount_name] = (cache_key, port)
        return port

    def _mount_definitions(self) -> list[dict[str, Any]]:
        mounts = _extract_mount_definitions(
            self._adapter,
            default_name=self._name,
            default_role=self._role,
        )
        for mount in mounts:
            mount["access_scope"] = "tenant" if self._owner_tenant else "global"
        if self._deployment is not None:
            mounts.extend(self._deployed_mounts.get())
        seen = {mount["name"] for mount in mounts}
        if self._registry_store is None:
            return mounts

        for entry in self._registry_store.list_entries(tenant_id=self._tenant.get()):
            if entry.name in seen:
                continue
            port = self._registry_port(entry.name)
            if port is None:
                continue
            mounts.append(
                {
                    "name": entry.name,
                    "access_scope": "tenant" if entry.tenant_id else "global",
                    "role": entry.role,
                    "categories": entry.categories,
                    "priority": entry.default_read_priority,
                    "port": port,
                }
            )
            seen.add(entry.name)
        return mounts

    def _read_adapter(self) -> MimirPort:
        mounts = self._mount_definitions()
        single_base_mount = (
            len(mounts) == 1
            and mounts[0]["port"] is self._adapter
            and mounts[0]["name"] == self._name
        )
        if single_base_mount:
            return self._adapter

        from ravn.adapters.mimir.composite import CompositeMimirAdapter
        from ravn.domain.mimir import MimirMount

        return CompositeMimirAdapter(
            mounts=[
                MimirMount(
                    name=mount["name"],
                    port=mount["port"],
                    role=mount["role"],
                    read_priority=mount["priority"],
                    categories=mount["categories"],
                )
                for mount in mounts
            ],
            write_routing=getattr(self._adapter, "_write_routing", None),
        )

    def _resolve_port(self, mount_name: str | None) -> tuple[MimirPort, str]:
        if mount_name is None:
            return self._read_adapter(), self._name

        for mount in self._mount_definitions():
            if mount["name"] == mount_name:
                return mount["port"], mount_name
        raise HTTPException(404, f"Unknown mount: {mount_name}")

    def _register_routes(self) -> None:
        router = self.router
        adapter = self._adapter

        @router.get("/stats", response_model=StatsResponse)
        async def stats(mount: str | None = Query(default=None)) -> StatsResponse:
            port, _ = self._resolve_port(mount)
            summary = await port.summarize()
            return StatsResponse(
                page_count=summary.page_count,
                categories=summary.categories,
                healthy=True,
            )

        @router.get("/summary", response_model=SummaryResponse)
        async def summary(mount: str | None = Query(default=None)) -> SummaryResponse:
            """Counts and last-write time without reading page or source bodies.

            Exists so a remote mount can be summarised in one cheap call rather
            than by transferring its whole corpus over ``/pages`` + ``/sources``.
            """
            port, _ = self._resolve_port(mount)
            result = await port.summarize()
            return SummaryResponse(
                page_count=result.page_count,
                source_count=result.source_count,
                categories=result.categories,
                last_write=(result.last_write.isoformat() if result.last_write is not None else ""),
                lint_issues=result.lint_issues,
                lint_checked_at=(
                    result.lint_checked_at.isoformat() if result.lint_checked_at is not None else ""
                ),
            )

        @router.get("/mounts", response_model=list[MountResponse])
        async def list_mounts() -> list[MountResponse]:
            mounts = self._mount_definitions()
            return await _merged_mount_responses(
                adapter,
                default_name=self._name,
                default_role=self._role,
                registry_store=self._registry_store,
                tenant_id=self._tenant.get(),
                mounts=mounts,
            )

        @router.get("/registry/mounts", response_model=list[RegistryMountResponse])
        async def list_registry_mounts() -> list[RegistryMountResponse]:
            if self._registry_store is None:
                mounts = _extract_mount_definitions(
                    adapter,
                    default_name=self._name,
                    default_role=self._role,
                )
                entries = [
                    MimirRegistryEntry(
                        name=mount["name"],
                        kind="remote" if getattr(mount["port"], "_base_url", "") else "local",
                        role=mount["role"],
                        categories=mount["categories"],
                        url=getattr(mount["port"], "_base_url", ""),
                        default_read_priority=mount["priority"],
                        desc=f"{mount.get('access_scope', 'unknown')} knowledge instance",
                    )
                    for mount in mounts
                ]
            else:
                entries = self._registry_store.list_entries(tenant_id=self._tenant.get())
            names = {entry.name for entry in entries}
            if self._deployment is not None:
                for mount in self._deployed_mounts.get():
                    connection = mount.get("connection")
                    if mount.get("kind") == "remote" and self._public_url:
                        connection = {
                            "adapter": "ravn.adapters.mimir.http.HttpMimirAdapter",
                            "kwargs": {"base_url": self._public_url, "mount": mount["name"]},
                            "auth_ref": "workload:mimir",
                        }
                    if mount["name"] in names or not connection:
                        continue
                    entries.append(
                        MimirRegistryEntry(
                            id=f"deployment:{mount['name']}",
                            name=mount["name"],
                            kind=mount.get("kind", "local"),
                            role=mount["role"],
                            **connection,
                            categories=mount["categories"],
                            default_read_priority=mount["priority"],
                            desc=mount.get(
                                "desc", "Managed local instance; session must run on this host."
                            ),
                        )
                    )
            scopes = {
                m["name"]: m.get("access_scope", "unknown") for m in self._mount_definitions()
            }
            return [
                _registry_to_response(entry).model_copy(
                    update={
                        "access_scope": scopes.get(
                            entry.name, "tenant" if entry.tenant_id else "global"
                        )
                    }
                )
                for entry in entries
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

            self._validate_registry_write(request)
            if request.auth_ref and not request.adapter:
                raise HTTPException(
                    422, "Use a configured mount adapter for authenticated connections"
                )
            entry = MimirRegistryEntry(**request.model_dump(), tenant_id=self._tenant.get())
            self._registry_store.save_entry(entry)
            self._registry_local_ports.clear()
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

            existing = self._registry_store.get_entry(entry_id, tenant_id=self._tenant.get())
            if existing is None:
                raise HTTPException(status_code=404, detail=f"Unknown registry mount: {entry_id}")

            self._validate_registry_write(request)
            if request.auth_ref and not request.adapter:
                raise HTTPException(
                    422, "Use a configured mount adapter for authenticated connections"
                )
            entry = existing.model_copy(update=request.model_dump())
            self._registry_store.save_entry(entry)
            self._registry_local_ports.clear()
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

            if self._registry_store.get_entry(entry_id, tenant_id=self._tenant.get()) is None:
                raise HTTPException(404, "Registry connection not found in this tenant")
            self._registry_store.delete_entry(entry_id, tenant_id=self._tenant.get())
            self._registry_local_ports.clear()

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
            for mount in self._mount_definitions():
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
                # Metadata only — an activity feed needs titles and timestamps,
                # not the bodies behind them.
                for source in await port.list_sources():
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
            prefix: str | None = Query(default=None),
            mount: str | None = Query(default=None),
        ) -> list[PageMetaResponse]:
            mounts = self._mount_definitions()
            port, resolved_mount = self._resolve_port(mount)
            pages = await port.list_pages(category=category, prefix=prefix)
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
                mounts=mounts,
                category=category,
                prefix=prefix,
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
            mounts = self._mount_definitions()
            port, resolved_mount = self._resolve_port(mount)
            try:
                page = await port.get_page(path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Page not found: {path}")
            if mount is not None:
                return _decorate_page(page, mounts=[resolved_mount])
            # Only this page's mounts are needed. Unscoped, this walked all 578
            # pages to answer for one — and Ting reads ~10 pages per campaign,
            # 18 campaigns per research page view, so a single view cost ~180
            # corpus walks. A path is its own prefix.
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
                mounts=mounts,
                prefix=page.meta.path,
            )
            return _decorate_page(page, mounts=mount_map.get(page.meta.path, [self._name]))

        @router.get("/search", response_model=list[SearchResult])
        async def search(
            q: str = Query(),
            mount: str | None = Query(default=None),
            debug: bool = Query(default=False),
        ) -> list[SearchResult]:
            port, _ = self._resolve_port(mount)
            pages = await port.search(q)
            if self._eval_capture_dir is not None:
                from mimir.eval import append_capture

                await asyncio.to_thread(
                    append_capture,
                    self._eval_capture_dir,
                    q,
                    [p.meta.path for p in pages],
                )
            return [
                SearchResult(
                    path=p.meta.path,
                    title=p.meta.title,
                    summary=p.meta.summary,
                    category=p.meta.category,
                    score=p.meta.search_score if debug else None,
                    score_breakdown=p.meta.score_breakdown if debug else None,
                )
                for p in pages
            ]

        @router.get("/entities/index", response_model=list[EntityResponse])
        async def entities() -> list[EntityResponse]:
            """Entity index for the Retrieval Reflex (NIU-1059) and graph tools.

            Distinct from ``GET /entities`` (the UI catalog): this is the
            compact, typed index derived from the link graph."""
            index = getattr(adapter, "entity_index", None)
            if index is None:
                raise HTTPException(status_code=501, detail="Adapter has no entity index")
            return [
                EntityResponse(
                    slug=meta.path.rsplit("/", 1)[-1].removesuffix(".md"),
                    title=meta.title,
                    type=meta.entity_type.value if meta.entity_type else None,
                    confidence=meta.confidence.value if meta.confidence else None,
                    updated_at=meta.updated_at.isoformat(),
                    path=meta.path,
                )
                for meta in index()
            ]

        @router.get("/related", response_model=list[RelatedPageResponse])
        async def related(
            path: str = Query(),
            depth: int = Query(default=1, ge=1, le=3),
            rel: str | None = Query(default=None),
        ) -> list[RelatedPageResponse]:
            """Link-graph traversal from a page (NIU-1058)."""
            related_fn = getattr(adapter, "related_pages", None)
            if related_fn is None:
                raise HTTPException(status_code=501, detail="Adapter has no link graph")
            return [RelatedPageResponse(**item) for item in related_fn(path, depth=depth, rel=rel)]

        @router.get("/evidence", response_model=list[FactEvidenceResponse])
        async def evidence(path: str = Query()) -> list[FactEvidenceResponse]:
            """Evidence-counted beliefs for a page (NIU-1062).

            Proofs come from the page's timeline entries AND from ingested raw
            sources that bear on a fact (write-time scoped consolidation)."""
            from mimir.learning import compute_page_evidence

            try:
                content = await adapter.read_page(path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Page not found: {path}")
            sources = [
                (source.source_id, source.content) for source in await _read_full_sources(adapter)
            ]
            return [
                FactEvidenceResponse(**item.to_dict())
                for item in compute_page_evidence(content, sources=sources)
            ]

        @router.post(
            "/page/revise",
            response_model=PageResponse,
            dependencies=[Depends(_require_write_auth)],
        )
        async def revise_belief_endpoint(request: ReviseBeliefRequest) -> PageResponse:
            """Belief revision with journey (NIU-1062): rewrite a compiled-truth
            fact while appending the old → new transition to the Timeline."""
            from mimir.learning import revise_belief

            try:
                content = await adapter.read_page(request.path)
            except FileNotFoundError:
                raise HTTPException(status_code=404, detail=f"Page not found: {request.path}")
            try:
                revised = revise_belief(
                    content, request.old_fact, request.new_fact, request.attribution
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            await adapter.upsert_page(request.path, revised)
            page = await adapter.get_page(request.path)
            return _decorate_page(page, mounts=[self._name])

        @router.get("/log")
        async def log_entries(
            n: int = Query(default=50),
            mount: str | None = Query(default=None),
        ) -> dict:
            """Return last *n* log entries as raw text (delegated to filesystem adapter)."""
            port, _ = self._resolve_port(mount)
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
            mounts = self._mount_definitions()
            port, resolved_mount = self._resolve_port(mount)
            try:
                report = await port.lint()
            except NotImplementedError as exc:
                raise HTTPException(501, str(exc)) from exc
            except MimirUnavailableError as exc:
                status = 501 if isinstance(exc.__cause__, NotImplementedError) else 503
                raise HTTPException(status, str(exc)) from exc
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
                mounts=mounts,
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
            mounts = self._mount_definitions()
            port, resolved_mount = self._resolve_port(mount)
            report = await port.lint(fix=True)
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
                mounts=mounts,
            )
            assignments = _get_lint_assignment_store(adapter)
            return _lint_to_response(
                report,
                assignments=assignments,
                mount_lookup=lambda path: (
                    resolved_mount if mount is not None else mount_map.get(path, [self._name])[0]
                ),
            )

        @router.get("/doctor")
        async def doctor_report(mount: str | None = Query(default=None)) -> dict[str, Any]:
            from mimir.doctor import run_doctor

            port, _ = self._resolve_port(mount)
            root = port.filesystem_root()
            if root is None:
                raise HTTPException(
                    status_code=501,
                    detail="doctor requires a filesystem-backed Mimir adapter",
                )
            report = await run_doctor(
                port,
                Path(root),
                registry_store=self._registry_store,
                tenant_id=self._tenant.get(),
            )
            return report.to_dict()

        @router.get("/eval/latest")
        async def eval_latest() -> dict[str, Any]:
            """Latest retrieval eval report (written by `mimir eval --out
            <root>/evals/eval-latest.json`). 404 when none has been recorded."""
            root = adapter.filesystem_root()
            if self._eval_capture_dir is None and root is None:
                raise HTTPException(status_code=501, detail="No evaluation storage configured")
            evals_dir = self._eval_capture_dir or Path(root) / "evals"
            report_path = evals_dir / "eval-latest.json"
            if not report_path.exists():
                raise HTTPException(status_code=404, detail="No eval report recorded")
            import json as _json

            return _json.loads(report_path.read_text(encoding="utf-8"))

        @router.get("/eval/queries")
        async def eval_queries() -> dict[str, Any]:
            """Aggregated live query stats from the eval-capture JSONL files."""
            from mimir.eval import load_capture

            root = adapter.filesystem_root()
            if self._eval_capture_dir is None and root is None:
                raise HTTPException(status_code=501, detail="No query capture storage configured")
            evals_dir = self._eval_capture_dir or Path(root) / "evals"
            captures = []
            for capture_file in sorted(evals_dir.glob("queries-*.jsonl")):
                captures.extend(load_capture(capture_file))
            if not captures:
                raise HTTPException(status_code=404, detail="No captured queries")
            recent = [
                {
                    "ts": entry.ts,
                    "query": entry.query,
                    "result_count": len(entry.result_paths),
                }
                for entry in reversed(captures[-_RECENT_QUERY_LIMIT:])
            ]
            return {
                "total": len(captures),
                "zero_result_count": sum(1 for c in captures if not c.result_paths),
                "recent": recent,
            }

        @router.post("/doctor/fix", dependencies=[Depends(_require_write_auth)])
        async def doctor_fix(mount: str | None = Query(default=None)) -> dict[str, Any]:
            """Run the safe auto-remediations, then return a fresh report."""
            from mimir.doctor import run_doctor, run_fixes

            port, _ = self._resolve_port(mount)
            root = port.filesystem_root()
            if root is None:
                raise HTTPException(
                    status_code=501,
                    detail="doctor requires a filesystem-backed Mimir adapter",
                )
            await run_fixes(port)
            report = await run_doctor(
                port,
                Path(root),
                registry_store=self._registry_store,
                tenant_id=self._tenant.get(),
            )
            return report.to_dict()

        @router.post("/lint/reassign", response_model=LintResponse)
        async def lint_reassign(request: LintReassignRequest) -> LintResponse:
            assignments = _get_lint_assignment_store(adapter)
            mounts = self._mount_definitions()
            report = await self._read_adapter().lint()
            for issue in report.issues:
                if issue.id in request.issue_ids:
                    assignments[_lint_issue_key(issue.id, issue.page_path)] = request.assignee
            mount_map = await _page_mount_map(
                adapter,
                default_name=self._name,
                default_role=self._role,
                mounts=mounts,
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
            for mount in self._mount_definitions():
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
            for mount in self._mount_definitions():
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

        @router.get("/instances/inspect")
        async def inspect_instances(mount: str | None = Query(default=None)) -> list[dict]:
            mounts = self._mount_definitions()
            if mount is not None:
                port, name = self._resolve_port(mount)
                mounts = [{"name": name, "port": port}]
            return [
                {"mount": item["name"], **await item["port"].inspect_instance()} for item in mounts
            ]

        @router.get("/deployments")
        async def deployments(_auth: None = Depends(_require_deploy_auth)) -> dict:
            if self._deployment is None:
                raise HTTPException(501, "No knowledge deployment target is configured")
            return await self._deployment.list_deployments(tenant_id=self._tenant.get())

        @router.get("/deployments/{name}")
        async def inspect_deployment(
            name: str, target: str = "", _auth: None = Depends(_require_deploy_auth)
        ) -> dict:
            if self._deployment is None:
                raise HTTPException(501, "No knowledge deployment target is configured")
            try:
                return await self._deployment.inspect_deployment(
                    f"{target}/{name}" if target else name, tenant_id=self._tenant.get()
                )
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
            except NotImplementedError as exc:
                raise HTTPException(501, str(exc)) from exc

        @router.post("/deployments/{name}/{action}")
        async def control_deployment(
            name: str, action: str, target: str = "", _auth: None = Depends(_require_deploy_auth)
        ) -> dict:
            if self._deployment is None:
                raise HTTPException(501, "No knowledge deployment target is configured")
            try:
                return await self._deployment.control(
                    f"{target}/{name}" if target else name, action, tenant_id=self._tenant.get()
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            except NotImplementedError as exc:
                raise HTTPException(501, str(exc)) from exc

        @router.post("/deployments", status_code=202)
        async def deploy_instance(
            request: DeploymentRequest,
            _auth: None = Depends(_require_deploy_auth),
        ) -> dict:
            if self._deployment is None:
                raise HTTPException(501, "No knowledge deployment target is configured")
            try:
                return await self._deployment.deploy(
                    request.model_copy(update={"tenant_id": self._tenant.get()})
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

        @router.get("/graph", response_model=GraphResponse)
        async def graph(mount: str | None = Query(default=None)) -> GraphResponse:
            from dataclasses import asdict
            from urllib.parse import quote

            mounts = self._mount_definitions()
            if mount is not None:
                port, name = self._resolve_port(mount)
                mounts = [{"name": name, "port": port}]
            nodes: list[GraphNode] = []
            edges: list[GraphEdge] = []
            for item in mounts:
                result = await item["port"].get_graph()
                # Keep identical paths in different mounts independently navigable.
                prefix = quote(item["name"], safe="") + ":"
                ids = {node.id: prefix + quote(node.id, safe="") for node in result.nodes}
                for node in result.nodes:
                    data = asdict(node)
                    data.update(id=ids[node.id], mount=item["name"])
                    nodes.append(GraphNode(**data))
                edges.extend(
                    GraphEdge(source=ids[edge.source], target=ids[edge.target], type=edge.type)
                    for edge in result.edges
                )
            edges.extend(
                GraphEdge(**asdict(edge))
                for edge in cross_mount_source_edges(
                    {node.id: (node.mount, node.source_ids) for node in nodes}
                )
            )
            return GraphResponse(nodes=nodes, edges=edges)

        @router.get("/entities", response_model=list[EntityMetaResponse])
        async def list_entities(kind: str | None = Query(default=None)) -> list[EntityMetaResponse]:
            pages = await self._read_adapter().list_pages()
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
            port, resolved_mount = self._resolve_port(mount)
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
            max_chars: int | None = Query(
                default=None,
                gt=0,
                description=(
                    "Bound the returned content to this many characters. Raw sources "
                    "reach several megabytes; callers that only read a prefix should "
                    "say so rather than have the whole blob serialised and shipped."
                ),
            ),
        ) -> SourceResponse:
            port, resolved_mount = self._resolve_port(mount)
            source = (
                await port.read_source(source_id)
                if max_chars is None
                else await port.read_source_excerpt(source_id, max_chars)
            )
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
            port, resolved_mount = self._resolve_port(mount)
            source_pages = await _source_page_map(port)
            results: list[SourceMetaResponse] = []
            # Metadata only. Reading every source body to build a listing loaded
            # the whole corpus into memory at once and OOM-killed the service on
            # the shared mount; the bodies were then discarded, since a listing
            # has no use for them. Callers that want content fetch /mimir/source.
            for source in await port.list_sources():
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
                # Same definition as the adapter's unprocessed_only: pending
                # synthesis means uncited AND not operational exhaust —
                # otherwise the API and the warden report different backlogs.
                results = [
                    source
                    for source in results
                    if not source.compiled_into
                    and source.source_type not in OPERATIONAL_SOURCE_TYPES
                ]
            return results

        @router.get("/page/sources", response_model=list[SourceMetaResponse])
        async def page_sources(
            path: str = Query(),
            mount: str | None = Query(default=None),
        ) -> list[SourceMetaResponse]:
            port, resolved_mount = self._resolve_port(mount)
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
            port, _ = self._resolve_port(request.mount)
            await port.upsert_page(request.path, request.content)

        @router.delete("/page", status_code=204)
        async def delete_page(
            path: str = Query(),
            mount: str | None = Query(default=None),
            _auth: None = Depends(_require_write_auth),
        ) -> None:
            port, _ = self._resolve_port(mount)
            if not await port.delete_page(path):
                raise HTTPException(status_code=404, detail=f"Page not found: {path}")

        @router.post("/ingest", response_model=IngestResponse)
        async def ingest_source(
            request: IngestRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> IngestResponse:
            port, _ = self._resolve_port(request.mount)
            if request.source_type in OPERATIONAL_SOURCE_TYPES:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Refusing to ingest source_type {request.source_type!r}: it records "
                        "system activity, not knowledge, so nothing will ever synthesise it "
                        "into a page and it would sit unprocessed forever. Write run output "
                        "to logs or a thread instead."
                    ),
                )
            content_hash = compute_content_hash(request.content)
            source_id = compute_source_id(request.content)
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
            consolidated = list(getattr(port, "_last_consolidated", []))
            return IngestResponse(
                source_id=source_id,
                pages_updated=page_paths,
                consolidated=consolidated,
            )

        @router.post("/sources/ingest/url", response_model=SourceResponse)
        async def ingest_url(
            request: UrlIngestRequest,
            _auth: None = Depends(_require_write_auth),
        ) -> SourceResponse:
            port, resolved_mount = self._resolve_port(request.mount)
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
            port, resolved_mount = self._resolve_port(mount)
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
