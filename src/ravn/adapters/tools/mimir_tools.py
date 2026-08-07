"""Mímir agent tools for the persistent knowledge base (NIU-540).

Tools:
  mimir_ingest  — ingest a URL or raw text into the wiki
  mimir_read_source — read a specific raw source by source_id
  mimir_query   — search the wiki and synthesise an answer
  mimir_read    — read a specific wiki page
  mimir_write   — create or update a wiki page
  mimir_publish_files — publish workspace markdown files into the wiki
  mimir_search  — full-text search, returns list of matching pages
  mimir_lint    — health-check: orphans, contradictions, staleness, gaps
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mimir.compiled_truth import parse_page as parse_compiled_truth_page
from niuu.domain.mimir import MimirSource, compute_content_hash, compute_source_id
from ravn.adapters.tools.entity_extractor import EntityExtractor
from ravn.adapters.tools.file_security import PathSecurityError, resolve_safe
from ravn.domain.exceptions import MimirUnavailableError
from ravn.domain.models import ToolResult
from ravn.ports.mimir import MimirPort
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION = "mimir:write"
_PERMISSION_READ = "mimir:read"
_MIMIR_SOURCE_INGESTED_EVENT = "mimir.source.ingested"


def _is_provenance_optional_research_path(path: str) -> bool:
    normalized = path.strip().lstrip("/")
    optional_suffixes = (
        "/brief.md",
        "/plan.md",
        "/manifest.md",
        "/sources.md",
    )
    return normalized.startswith("research/") and normalized.endswith(optional_suffixes)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _resolve_ingest_mount_names(adapter: MimirPort, explicit: str | None = None) -> list[str]:
    resolver = getattr(type(adapter), "ingest_targets", None)
    if resolver is not None:
        try:
            return _string_list(resolver(adapter, explicit))
        except Exception:
            logger.debug("mimir_ingest: failed to resolve ingest targets", exc_info=True)
    if explicit:
        return [explicit]
    return []


# Marks an artifact failure the agent cannot fix — Mímir could not be reached, so
# provenance is unverified rather than invalid. Callers key off this to skip a
# model repair turn that would be spent re-ingesting sources that already exist.
PROVENANCE_UNVERIFIABLE = "provenance could not be verified"


async def _validate_research_page_provenance(
    adapter: MimirPort,
    *,
    path: str,
    content: str,
) -> str | None:
    if not path.startswith("research/"):
        return None
    if _is_provenance_optional_research_path(path):
        return None

    page = parse_compiled_truth_page(content)
    source_ids = [
        stripped_id for source_id in page.source_ids if (stripped_id := str(source_id).strip())
    ]
    if not source_ids:
        return (
            "research pages must include non-empty frontmatter 'source_ids' that point to "
            "ingested raw sources; call mimir_ingest on the sources you actually used first"
        )

    resolved_sources: list[MimirSource] = []
    missing: list[str] = []
    for source_id in source_ids:
        try:
            source = await adapter.read_source(source_id)
        except MimirUnavailableError as exc:
            # Mímir could not answer, so we cannot conclude the source is absent.
            # Saying "missing" here sent the agent off to re-ingest sources that
            # were already there, and burned its one repair attempt on an outage.
            return f"{PROVENANCE_UNVERIFIABLE}: {exc}"
        if source is None:
            missing.append(source_id)
            continue
        resolved_sources.append(source)

    if missing:
        return (
            "research page references missing source_ids: "
            + ", ".join(missing)
            + "; ingest those sources before writing the page"
        )

    stripped_content = content.strip()
    if resolved_sources and all(
        source.content.strip() == stripped_content for source in resolved_sources
    ):
        return (
            "research page provenance only points to the page content itself; ingest the "
            "actual source material you relied on, not the final synthesized page"
        )

    return None


# ---------------------------------------------------------------------------
# mimir_ingest
# ---------------------------------------------------------------------------


class MimirIngestTool(ToolPort):
    """Ingest a URL or raw text as a Mímir source document."""

    def __init__(
        self,
        adapter: MimirPort,
        entity_extractor: EntityExtractor | None = None,
        event_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._adapter = adapter
        self._entity_extractor = entity_extractor
        self._event_emitter = event_emitter

    @property
    def name(self) -> str:
        return "mimir_ingest"

    @property
    def description(self) -> str:
        return (
            "Ingest a raw text or URL content into Mímir as an immutable source. "
            "Records the source for staleness detection. "
            "Use before writing wiki pages derived from this content."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Raw text content to ingest.",
                },
                "title": {
                    "type": "string",
                    "description": "Human-readable title for the source.",
                },
                "source_type": {
                    "type": "string",
                    "enum": ["web", "document", "conversation", "research"],
                    "description": (
                        "Category of source. Mímir holds knowledge about the world, not "
                        "records of your own activity — do not ingest tool transcripts, "
                        "probe output or run logs, which no page will ever synthesise."
                    ),
                },
                "origin_url": {
                    "type": "string",
                    "description": "Original URL, if fetched from the web.",
                },
                "mimir": {
                    "type": "string",
                    "description": (
                        "Optional named Mimir mount to ingest into deterministically. "
                        "When omitted, the adapter's default routing is used."
                    ),
                },
            },
            "required": ["content", "title"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        content = input.get("content", "").strip()
        title = input.get("title", "").strip()
        source_type = input.get("source_type", "document")
        origin_url = input.get("origin_url")
        target_mimir = str(input.get("mimir", "") or "").strip() or None

        if not content:
            return ToolResult(tool_call_id="", content="content is required", is_error=True)
        if not title:
            return ToolResult(tool_call_id="", content="title is required", is_error=True)

        source = MimirSource(
            source_id=compute_source_id(content),
            title=title,
            content=content,
            source_type=source_type,
            origin_url=origin_url,
            content_hash=compute_content_hash(content),
            ingested_at=datetime.now(UTC),
        )

        ingest_to = getattr(type(self._adapter), "ingest_to", None)
        if target_mimir and ingest_to is not None:
            page_paths = await ingest_to(self._adapter, source, target_mimir)
        else:
            page_paths = await self._adapter.ingest(source)

        if self._entity_extractor is not None:
            entity_paths = await self._entity_extractor.run(source)
            page_paths = page_paths + entity_paths

        mount_names = _resolve_ingest_mount_names(self._adapter, target_mimir)
        if self._event_emitter is not None:
            event_fields: dict[str, Any] = {
                "source_id": source.source_id,
                "source_title": title,
                "source_type": source_type,
                "page_paths": list(dict.fromkeys(page_paths)),
            }
            if target_mimir:
                event_fields["mount_name"] = target_mimir
                event_fields["mount_names"] = [target_mimir]
            elif mount_names:
                event_fields["mount_names"] = mount_names
                if len(mount_names) == 1:
                    event_fields["mount_name"] = mount_names[0]
            try:
                logger.info(
                    "mimir_ingest: emitting source-ingested event source_id=%s",
                    source.source_id,
                )
                await self._event_emitter(_MIMIR_SOURCE_INGESTED_EVENT, event_fields)
            except Exception:
                logger.warning(
                    "mimir_ingest: failed to emit source-ingested event",
                    exc_info=True,
                )
        else:
            logger.warning(
                "mimir_ingest: no event emitter wired for source_id=%s",
                source.source_id,
            )

        result = (
            f"Ingested source '{title}' (id={source.source_id})\n"
            f"source_id: {source.source_id}\n"
            f"source_type: {source_type}"
        )
        if target_mimir:
            result += f"\nmount_name: {target_mimir}"
        elif mount_names:
            result += f"\nmount_names: {', '.join(mount_names)}"
        if page_paths:
            result += f"\nPages updated: {', '.join(page_paths)}"
        return ToolResult(tool_call_id="", content=result)


# ---------------------------------------------------------------------------
# mimir_query
# ---------------------------------------------------------------------------


class MimirQueryTool(ToolPort):
    """Search the Mímir wiki and return relevant pages for synthesis."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_query"

    @property
    def description(self) -> str:
        return (
            "Query the Mímir knowledge base. "
            "Reads the wiki index to find relevant pages, then returns their content. "
            "Call this before going to the web — check what you already know."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question or topic to search the wiki for.",
                },
            },
            "required": ["question"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        question = input.get("question", "").strip()
        if not question:
            return ToolResult(tool_call_id="", content="question is required", is_error=True)

        result = await self._adapter.query(question)

        if not result.sources:
            return ToolResult(
                tool_call_id="",
                content=f"No wiki pages found for: {question}\n\nGo to the web to research.",
            )

        lines = [f"## Mímir: results for '{question}'\n"]
        for page in result.sources:
            lines.append(f"### {page.meta.title} ({page.meta.path})")
            lines.append(page.content[:2000])
            lines.append("")

        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# mimir_read
# ---------------------------------------------------------------------------


class MimirReadTool(ToolPort):
    """Read a specific Mímir wiki page by path."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_read"

    @property
    def description(self) -> str:
        return (
            "Read the full content of a specific Mímir wiki page. "
            "Use the path from mimir_search or mimir_query results."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path to the wiki page relative to the wiki root, "
                        "e.g. 'technical/ravn/tools.md'."
                    ),
                },
            },
            "required": ["path"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        path = input.get("path", "").strip()
        if not path:
            return ToolResult(tool_call_id="", content="path is required", is_error=True)

        try:
            content = await self._adapter.read_page(path)
        except FileNotFoundError:
            return ToolResult(
                tool_call_id="",
                content=f"Page not found: {path}",
                is_error=True,
            )

        return ToolResult(tool_call_id="", content=content)


# ---------------------------------------------------------------------------
# mimir_read_source
# ---------------------------------------------------------------------------


class MimirReadSourceTool(ToolPort):
    """Read an ingested raw Mimir source by ID."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_read_source"

    @property
    def description(self) -> str:
        return (
            "Read an immutable raw Mimir source by source_id. "
            "Use this after a mimir.source.ingested event to synthesize compiled wiki pages."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Immutable Mimir source id to read.",
                },
                "mimir": {
                    "type": "string",
                    "description": (
                        "Optional named Mimir mount to read from deterministically "
                        "when multiple mounts are available."
                    ),
                },
            },
            "required": ["source_id"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        source_id = input.get("source_id", "").strip()
        target_mimir = str(input.get("mimir", "") or "").strip() or None
        if not source_id:
            return ToolResult(tool_call_id="", content="source_id is required", is_error=True)

        read_source_from_mount = getattr(type(self._adapter), "read_source_from_mount", None)
        if target_mimir and read_source_from_mount is not None:
            source = await read_source_from_mount(self._adapter, source_id, target_mimir)
        else:
            source = await self._adapter.read_source(source_id)
        if source is None:
            suffix = f" in mount '{target_mimir}'" if target_mimir else ""
            return ToolResult(
                tool_call_id="",
                content=f"Source not found: {source_id}{suffix}",
                is_error=True,
            )

        lines = [
            f"Source ID: {source.source_id}",
            f"Title: {source.title}",
            f"Type: {source.source_type}",
            f"Ingested: {source.ingested_at.isoformat()}",
        ]
        if target_mimir:
            lines.append(f"Mount: {target_mimir}")
        if source.origin_url:
            lines.append(f"Origin URL: {source.origin_url}")
        lines.append("")
        lines.append(source.content)
        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# mimir_write
# ---------------------------------------------------------------------------


class MimirWriteTool(ToolPort):
    """Create or update a Mímir wiki page."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_write"

    @property
    def description(self) -> str:
        return (
            "Create or update a Mímir wiki page. "
            "Write synthesised, concise markdown — the wiki is for retrieval, not transcription. "
            "Always start with a # Title heading. "
            "Use the optional 'mimir' parameter to route the write to a specific instance "
            "(e.g. 'shared' to promote to the shared Mímir), bypassing category routing."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Path relative to the wiki root, e.g. 'technical/ravn/tools.md'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Full Markdown content for the page.",
                },
                "mimir": {
                    "type": "string",
                    "description": (
                        "Optional: name of the Mímir instance to write to "
                        "(e.g. 'local', 'shared', 'kanuck'). "
                        "Overrides category-based write routing. "
                        "Omit to use the default routing rules."
                    ),
                },
            },
            "required": ["path", "content"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    async def execute(self, input: dict) -> ToolResult:
        path = input.get("path", "").strip()
        content = input.get("content", "").strip()
        mimir = input.get("mimir")

        if not path:
            return ToolResult(tool_call_id="", content="path is required", is_error=True)
        if not content:
            return ToolResult(tool_call_id="", content="content is required", is_error=True)
        if not path.endswith(".md"):
            return ToolResult(
                tool_call_id="",
                content="path must end with .md",
                is_error=True,
            )

        provenance_error = await _validate_research_page_provenance(
            self._adapter,
            path=path,
            content=content,
        )
        if provenance_error is not None:
            return ToolResult(
                tool_call_id="",
                content=provenance_error,
                is_error=True,
            )

        await self._adapter.upsert_page(path, content, mimir=mimir)
        suffix = f" (routed to: {mimir})" if mimir else ""
        return ToolResult(tool_call_id="", content=f"Page written: {path}{suffix}")


# ---------------------------------------------------------------------------
# mimir_publish_files
# ---------------------------------------------------------------------------


class MimirPublishFilesTool(ToolPort):
    """Publish one or more workspace markdown files into Mimir."""

    def __init__(self, adapter: MimirPort, workspace: Path) -> None:
        self._adapter = adapter
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "mimir_publish_files"

    @property
    def description(self) -> str:
        return (
            "Publish markdown files from the current workspace into Mimir using the same "
            "relative paths. Useful when a workflow keeps working artifacts locally and "
            "needs a deterministic durable publication step."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Workspace-relative markdown paths to publish into Mimir. "
                        "Each file is published to the same relative path."
                    ),
                },
                "mimir": {
                    "type": "string",
                    "description": (
                        "Optional named Mimir mount to publish into deterministically."
                    ),
                },
            },
            "required": ["paths"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    def resolve_workspace_file(self, path: str) -> Path | None:
        """Resolve a markdown artifact path from the session root or checked-out repo root."""
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                safe_path = resolve_safe(candidate, self._workspace)
            except PathSecurityError:
                return None
            return safe_path if safe_path.exists() and safe_path.is_file() else None

        roots = [self._workspace]
        repo_root = self._workspace / "repo"
        if repo_root.exists() and repo_root.is_dir():
            roots.append(repo_root)

        for root in roots:
            try:
                safe_path = resolve_safe(root / candidate, self._workspace)
            except PathSecurityError:
                continue
            if safe_path.exists() and safe_path.is_file():
                return safe_path
        return None

    async def execute(self, input: dict) -> ToolResult:
        raw_paths = input.get("paths")
        mimir = input.get("mimir")
        if not isinstance(raw_paths, list) or not raw_paths:
            return ToolResult(
                tool_call_id="",
                content="paths must be a non-empty array of workspace-relative markdown files",
                is_error=True,
            )

        published: list[str] = []
        for raw_path in raw_paths:
            path = str(raw_path or "").strip()
            if not path:
                continue
            if not path.endswith(".md"):
                return ToolResult(
                    tool_call_id="",
                    content=f"path must end with .md: {path}",
                    is_error=True,
                )
            safe_path = self.resolve_workspace_file(path)
            if safe_path is None:
                return ToolResult(
                    tool_call_id="",
                    content=f"Workspace file not found: {path}",
                    is_error=True,
                )

            content = safe_path.read_text(encoding="utf-8", errors="replace")
            provenance_error = await _validate_research_page_provenance(
                self._adapter,
                path=path,
                content=content,
            )
            if provenance_error is not None:
                return ToolResult(tool_call_id="", content=provenance_error, is_error=True)

            try:
                await self._adapter.upsert_page(path, content, mimir=mimir)
                await self._adapter.get_page(path)
            except Exception as exc:
                return ToolResult(
                    tool_call_id="",
                    content=f"Failed to publish {path}: {exc}",
                    is_error=True,
                )
            published.append(path)

        if not published:
            return ToolResult(
                tool_call_id="",
                content="no publishable markdown files found",
                is_error=True,
            )

        suffix = f" (routed to: {mimir})" if mimir else ""
        return ToolResult(
            tool_call_id="",
            content="Published pages:\n- " + "\n- ".join(published) + suffix,
        )


# ---------------------------------------------------------------------------
# mimir_search
# ---------------------------------------------------------------------------


class MimirSearchTool(ToolPort):
    """Full-text search over Mímir wiki pages."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_search"

    @property
    def description(self) -> str:
        return (
            "Full-text search over Mímir wiki pages. "
            "Returns a list of matching pages with titles, paths, and summaries. "
            "Use mimir_read to fetch the full content of a specific page."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms to match against wiki page content.",
                },
            },
            "required": ["query"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        query = input.get("query", "").strip()
        if not query:
            return ToolResult(tool_call_id="", content="query is required", is_error=True)

        pages = await self._adapter.search(query)

        if not pages:
            return ToolResult(
                tool_call_id="",
                content=f"No pages found for query: {query}",
            )

        lines = [f"## Mímir search results for '{query}'\n"]
        for page in pages:
            lines.append(f"- **{page.meta.title}** (`{page.meta.path}`)")
            if page.meta.summary:
                lines.append(f"  {page.meta.summary}")

        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# mimir_lint
# ---------------------------------------------------------------------------


class MimirLintTool(ToolPort):
    """Run a Mímir wiki health-check: orphans, contradictions, staleness, gaps."""

    def __init__(self, adapter: MimirPort) -> None:
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "mimir_lint"

    @property
    def description(self) -> str:
        return (
            "Health-check the Mímir wiki. "
            "Finds orphan pages, flagged contradictions, stale sources, and concept gaps. "
            "Run during idle time to keep the wiki healthy."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {},
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        report = await self._adapter.lint()

        lines = [
            f"## Mímir lint — {report.pages_checked} pages checked\n",
            f"Issues found: {len(report.issues)}",
            "",
        ]

        check_labels: dict[str, str] = {
            "L01": "Orphans",
            "L02": "Contradictions",
            "L04": "Concept Gaps",
            "L05": "Broken Wikilinks",
            "L06": "Missing Source Attribution",
            "L07": "Thin Pages",
            "L08": "Stale Content",
            "L09": "Timeline Edits",
            "L10": "Empty Compiled Truth",
            "L11": "Stale Index",
            "L12": "Invalid Frontmatter",
        }

        by_check: dict[str, list] = {}
        for issue in report.issues:
            by_check.setdefault(issue.id, []).append(issue)

        for check_id in sorted(by_check):
            group = by_check[check_id]
            label = check_labels.get(check_id, check_id)
            lines.append(f"### {label} ({len(group)})")
            for issue in group:
                fix_tag = " [auto-fixable]" if issue.auto_fixable else ""
                lines.append(f"  - [{issue.severity}] {issue.page_path}: {issue.message}{fix_tag}")
            lines.append("")

        if not report.issues_found:
            lines.append("All clear — no issues found.")

        return ToolResult(tool_call_id="", content="\n".join(lines))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_mimir_tools(
    adapter: MimirPort,
    workspace: Path | None = None,
    entity_extractor: EntityExtractor | None = None,
    event_emitter: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
) -> list[ToolPort]:
    """Return all Mímir tools wired to *adapter*.

    When *entity_extractor* is provided it is wired into :class:`MimirIngestTool`
    so that LLM-based entity detection runs automatically on every ingest.
    """
    tools: list[ToolPort] = [
        MimirIngestTool(adapter, entity_extractor=entity_extractor, event_emitter=event_emitter),
        MimirQueryTool(adapter),
        MimirReadTool(adapter),
        MimirReadSourceTool(adapter),
        MimirWriteTool(adapter),
        MimirSearchTool(adapter),
        MimirLintTool(adapter),
    ]
    if workspace is not None:
        tools.append(MimirPublishFilesTool(adapter, workspace))
    return tools
