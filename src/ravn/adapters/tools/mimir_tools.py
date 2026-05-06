"""Mímir agent tools — six tools for the persistent knowledge base (NIU-540).

Tools:
  mimir_ingest  — ingest a URL or raw text into the wiki
  mimir_query   — search the wiki and synthesise an answer
  mimir_read    — read a specific wiki page
  mimir_write   — create or update a wiki page
  mimir_search  — full-text search, returns list of matching pages
  mimir_lint    — health-check: orphans, contradictions, staleness, gaps
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from mimir.compiled_truth import parse_page as parse_compiled_truth_page
from niuu.domain.mimir import MimirSource, compute_content_hash
from ravn.adapters.tools.entity_extractor import EntityExtractor
from ravn.domain.models import ToolResult
from ravn.ports.mimir import MimirPort
from ravn.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION = "mimir:write"
_PERMISSION_READ = "mimir:read"


def _source_id_from_content(title: str, content: str) -> str:
    return "src_" + compute_content_hash(f"{title}:{content}")[:16]


# ---------------------------------------------------------------------------
# mimir_ingest
# ---------------------------------------------------------------------------


class MimirIngestTool(ToolPort):
    """Ingest a URL or raw text as a Mímir source document."""

    def __init__(
        self,
        adapter: MimirPort,
        entity_extractor: EntityExtractor | None = None,
    ) -> None:
        self._adapter = adapter
        self._entity_extractor = entity_extractor

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
                    "enum": ["web", "document", "conversation", "tool_output", "research"],
                    "description": "Category of source.",
                },
                "origin_url": {
                    "type": "string",
                    "description": "Original URL, if fetched from the web.",
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

        if not content:
            return ToolResult(tool_call_id="", content="content is required", is_error=True)
        if not title:
            return ToolResult(tool_call_id="", content="title is required", is_error=True)

        source = MimirSource(
            source_id=_source_id_from_content(title, content),
            title=title,
            content=content,
            source_type=source_type,
            origin_url=origin_url,
            content_hash=compute_content_hash(content),
            ingested_at=datetime.now(UTC),
        )

        page_paths = await self._adapter.ingest(source)

        if self._entity_extractor is not None:
            entity_paths = await self._entity_extractor.run(source)
            page_paths = page_paths + entity_paths

        result = f"Ingested source '{title}' (id={source.source_id})"
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

    async def _validate_research_page_provenance(
        self,
        *,
        path: str,
        content: str,
    ) -> str | None:
        if not path.startswith("research/"):
            return None

        page = parse_compiled_truth_page(content)
        source_ids = [
            stripped_id
            for source_id in page.source_ids
            if (stripped_id := str(source_id).strip())
        ]
        if not source_ids:
            return (
                "research pages must include non-empty frontmatter 'source_ids' that point to "
                "ingested raw sources; call mimir_ingest on the sources you actually used first"
            )

        resolved_sources: list[MimirSource] = []
        missing: list[str] = []
        for source_id in source_ids:
            source = await self._adapter.read_source(source_id)
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

        provenance_error = await self._validate_research_page_provenance(
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
    entity_extractor: EntityExtractor | None = None,
) -> list[ToolPort]:
    """Return all six Mímir tools wired to *adapter*.

    When *entity_extractor* is provided it is wired into :class:`MimirIngestTool`
    so that LLM-based entity detection runs automatically on every ingest.
    """
    return [
        MimirIngestTool(adapter, entity_extractor=entity_extractor),
        MimirQueryTool(adapter),
        MimirReadTool(adapter),
        MimirWriteTool(adapter),
        MimirSearchTool(adapter),
        MimirLintTool(adapter),
    ]
