"""Mímir MCP (Model Context Protocol) server.

Exposes seven tools over the MCP JSON-RPC 2.0 protocol, allowing Claude Code,
Codex, Cursor, and other MCP-capable agents to query and update the Mímir
knowledge base.

Transports
----------
HTTP (served via FastAPI ``/mcp`` endpoint)::

    from mimir.mcp import MimirMcpServer
    from mimir.adapters.markdown import MarkdownMimirAdapter

    adapter = MarkdownMimirAdapter(root="~/.ravn/mimir")
    mcp_server = MimirMcpServer(adapter=adapter)
    app.include_router(mcp_server.router(), prefix="/mcp")

stdio (for local development — no running service required)::

    python -m mimir mcp --path ~/.ravn/mimir

Tools
-----
- ``mimir_search``  — full-text search, returns ranked page list
- ``mimir_read``    — read a page with its full content
- ``mimir_write``   — create or update a page
- ``mimir_ingest``  — ingest a raw document
- ``mimir_read_source`` — read an immutable raw source by source_id
- ``mimir_lint``    — run the knowledge-base linter
- ``mimir_stats``   — page count, categories, health status
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from typing import IO, Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from niuu.domain.mimir import MimirSource, compute_content_hash
from niuu.ports.mimir import MimirPort

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")

# MCP protocol version negotiated during initialize
_PROTOCOL_VERSION = "2024-11-05"

# ---------------------------------------------------------------------------
# Tool definitions (JSON Schema inputSchema)
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "name": "mimir_search",
        "description": (
            "Search the Mímir knowledge base for pages matching a query. "
            "Returns a ranked list of pages with path, title, summary, and category. "
            "Use this first to discover relevant pages before reading their full content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Full-text search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of results to return",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mimir_read",
        "description": (
            "Read the full content of a Mímir page, including its YAML frontmatter "
            "metadata and compiled-truth body. Returns path, title, summary, category, "
            "source IDs, update timestamp, and the raw markdown content."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Page path relative to the wiki root, "
                        "e.g. 'technical/ravn.md' or 'decisions/adr-001.md'"
                    ),
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "mimir_write",
        "description": (
            "Create or update a page in the Mímir knowledge base. "
            "The page body should follow the compiled-truth format: "
            "a '## Compiled Truth' section for synthesised facts and an optional "
            "'## Timeline' section for dated evidence. "
            "Pass frontmatter as a dict to set metadata such as type, confidence, "
            "and related_entities; omit it to preserve existing metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Destination path, e.g. 'technical/new-page.md'",
                },
                "content": {
                    "type": "string",
                    "description": "Page body markdown (without YAML frontmatter block)",
                },
                "frontmatter": {
                    "type": "object",
                    "description": (
                        "Optional YAML frontmatter fields: "
                        "type, confidence, entity_type, related_entities, source_ids. "
                        "Omit to skip the frontmatter block entirely."
                    ),
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "mimir_ingest",
        "description": (
            "Ingest a raw document into Mímir. "
            "The adapter parses the content and creates or updates knowledge pages. "
            "Returns a source ID and the list of page paths that were updated. "
            "Use this to add meeting notes, documents, or scraped content to the knowledge base."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "Raw document text to ingest",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Optional title for the source document; "
                        "defaults to the first line of content"
                    ),
                },
                "source_type": {
                    "type": "string",
                    "description": (
                        "Source kind: 'document', 'url', 'conversation', or 'code' "
                        "(default: 'document')"
                    ),
                    "default": "document",
                },
                "origin_url": {
                    "type": "string",
                    "description": "Optional URL this content was fetched from",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "mimir_read_source",
        "description": (
            "Read a raw ingested source by source_id. "
            "Returns the immutable source content, metadata, and provenance details. "
            "Use this when a workflow wants to transform freshly ingested material "
            "into compiled wiki pages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "string",
                    "description": "Immutable Mimir source identifier, e.g. 'src_abc123...'",
                },
            },
            "required": ["source_id"],
        },
    },
    {
        "name": "mimir_lint",
        "description": (
            "Run the Mímir knowledge-base linter and return a health report. "
            "Reports orphaned pages (no inbound links), contradictions, stale entries, "
            "and knowledge gaps. "
            "Use this to assess quality before or after making bulk changes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "fix": {
                    "type": "boolean",
                    "description": "Automatically fix fixable issues (default: false)",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "mimir_stats",
        "description": (
            "Return a summary of the Mímir knowledge base: "
            "total page count, available categories, and overall health status. "
            "Use this for a quick overview before searching or reading specific pages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------


class MimirMcpServer:
    """MCP JSON-RPC 2.0 server wrapping a ``MimirPort`` adapter.

    Args:
        adapter: The MimirPort implementation to delegate tool calls to.
        name:    Server name reported in the ``initialize`` response.
    """

    def __init__(self, adapter: MimirPort, name: str = "mimir") -> None:
        self._adapter = adapter
        self._name = name

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle(self, payload: dict[str, Any] | list[dict[str, Any]]) -> Any:
        """Handle a JSON-RPC request or batch.

        Returns a single response dict, a list of responses (for batches),
        or ``None`` when the payload contains only notifications.
        """
        if isinstance(payload, list):
            responses = [r for item in payload if (r := await self._handle_one(item)) is not None]
            return responses or None
        return await self._handle_one(payload)

    def router(self) -> APIRouter:
        """Return a FastAPI ``APIRouter`` with a ``POST /`` endpoint for MCP."""
        api_router = APIRouter()
        server = self

        @api_router.post("")
        async def mcp_endpoint(request: Request) -> JSONResponse:
            try:
                body = await request.json()
            except Exception:
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    },
                    status_code=400,
                )
            try:
                response = await server.handle(body)
                if response is None:
                    return JSONResponse(None, status_code=204)
                return JSONResponse(response)
            except Exception:
                logger.exception("MCP HTTP handler failed")
                return JSONResponse(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32603, "message": "Internal error"},
                    },
                    status_code=500,
                )

        return api_router

    async def run_stdio(
        self,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        """Run the MCP server using stdio transport.

        Reads newline-delimited JSON-RPC from *stdin* and writes responses to
        *stdout*.  Uses ``sys.stdin`` / ``sys.stdout`` when not provided.

        This is the entry point for ``python -m mimir mcp``.
        """
        _in = stdin or sys.stdin
        _out = stdout or sys.stdout
        loop = asyncio.get_event_loop()

        while True:
            line = await loop.run_in_executor(None, _in.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                _out.write(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "Parse error"},
                        }
                    )
                    + "\n"
                )
                _out.flush()
                continue

            response = await self.handle(payload)
            if response is not None:
                _out.write(json.dumps(response) + "\n")
                _out.flush()

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _handle_one(self, req: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC message.

        Returns ``None`` for notifications (messages without an ``id``).
        """
        req_id = req.get("id")
        method = req.get("method", "")

        if req_id is None:
            return None

        try:
            result = await self._dispatch(method, req.get("params") or {})
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except _MethodNotFoundError:
            logger.info("Unknown MCP method requested: %s", _sanitize_log(method))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        except Exception:
            logger.exception("MCP tool error for method %s", _sanitize_log(method))
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": "Internal error"},
            }

    async def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        match method:
            case "initialize":
                return {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self._name, "version": "1.0.0"},
                }
            case "tools/list":
                return {"tools": _TOOLS}
            case "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments") or {}
                content = await self._call_tool(name, arguments)
                return {"content": content}
            case "ping":
                return {}
            case _:
                raise _MethodNotFoundError(f"Method not found: {method}")
        raise AssertionError("Unreachable _dispatch fallthrough")

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        match name:
            case "mimir_search":
                return await self._tool_search(arguments)
            case "mimir_read":
                return await self._tool_read(arguments)
            case "mimir_write":
                return await self._tool_write(arguments)
            case "mimir_ingest":
                return await self._tool_ingest(arguments)
            case "mimir_read_source":
                return await self._tool_read_source(arguments)
            case "mimir_lint":
                return await self._tool_lint(arguments)
            case "mimir_stats":
                return await self._tool_stats(arguments)
            case _:
                raise ValueError(f"Unknown tool: {name}")
        raise AssertionError("Unreachable _call_tool fallthrough")

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _tool_search(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        query: str = args["query"]
        limit: int = int(args.get("limit") or 10)
        pages = await self._adapter.search(query)
        results = [
            {
                "path": p.meta.path,
                "title": p.meta.title,
                "summary": p.meta.summary,
                "category": p.meta.category,
            }
            for p in pages[:limit]
        ]
        return [{"type": "text", "text": json.dumps(results, indent=2)}]

    async def _tool_read(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        path: str = args["path"]
        try:
            page = await self._adapter.get_page(path)
        except FileNotFoundError:
            return [{"type": "text", "text": f"Page not found: {path}"}]
        result = {
            "path": page.meta.path,
            "title": page.meta.title,
            "summary": page.meta.summary,
            "category": page.meta.category,
            "updated_at": page.meta.updated_at.isoformat(),
            "source_ids": page.meta.source_ids,
            "content": page.content,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    async def _tool_write(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        path: str = args["path"]
        content: str = args["content"]
        frontmatter: dict[str, Any] | None = args.get("frontmatter")

        full_content = content
        if frontmatter:
            fm_text = yaml.dump(frontmatter, default_flow_style=False).strip()
            full_content = f"---\n{fm_text}\n---\n\n{content}"

        await self._adapter.upsert_page(path, full_content)

        try:
            page = await self._adapter.get_page(path)
            result: dict[str, Any] = {
                "path": page.meta.path,
                "title": page.meta.title,
                "summary": page.meta.summary,
                "category": page.meta.category,
                "updated_at": page.meta.updated_at.isoformat(),
            }
        except FileNotFoundError:
            result = {"path": path, "written": True}

        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    async def _tool_ingest(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        content: str = args["content"]
        title: str = (
            args.get("title") or content.splitlines()[0].lstrip("# ").strip()[:80] or "Untitled"
        )
        source_type: str = args.get("source_type") or "document"
        origin_url: str | None = args.get("origin_url")

        content_hash = compute_content_hash(content)
        source_id = "src_" + content_hash[:16]
        source = MimirSource(
            source_id=source_id,
            title=title,
            content=content,
            source_type=source_type,  # type: ignore[arg-type]
            origin_url=origin_url,
            content_hash=content_hash,
            ingested_at=datetime.now(UTC),
        )
        page_paths = await self._adapter.ingest(source)
        result = {
            "source_id": source_id,
            "title": title,
            "source_type": source_type,
            "origin_url": origin_url,
            "pages_updated": page_paths,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    async def _tool_read_source(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        source_id: str = args["source_id"]
        source = await self._adapter.read_source(source_id)
        if source is None:
            return [{"type": "text", "text": f"Source not found: {source_id}"}]
        result = {
            "source_id": source.source_id,
            "title": source.title,
            "source_type": source.source_type,
            "origin_url": source.origin_url,
            "content_hash": source.content_hash,
            "ingested_at": source.ingested_at.isoformat(),
            "content": source.content,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    async def _tool_lint(self, args: dict[str, Any]) -> list[dict[str, Any]]:
        fix: bool = bool(args.get("fix", False))
        report = await self._adapter.lint(fix=fix)
        result = {
            "issues": [
                {
                    "id": issue.id,
                    "severity": issue.severity,
                    "message": issue.message,
                    "page_path": issue.page_path,
                    "auto_fixable": issue.auto_fixable,
                }
                for issue in report.issues
            ],
            "pages_checked": report.pages_checked,
            "issues_found": report.issues_found,
            "summary": report.summary,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]

    async def _tool_stats(self, _args: dict[str, Any]) -> list[dict[str, Any]]:
        pages = await self._adapter.list_pages()
        categories = sorted({p.category for p in pages})
        result = {
            "page_count": len(pages),
            "categories": categories,
            "healthy": True,
        }
        return [{"type": "text", "text": json.dumps(result, indent=2)}]


# ---------------------------------------------------------------------------
# Internal exceptions
# ---------------------------------------------------------------------------


class _MethodNotFoundError(Exception):
    """Raised when the requested JSON-RPC method is not implemented."""
