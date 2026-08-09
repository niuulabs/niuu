"""GBrainMimirAdapter — MimirPort backed by a gbrain brain over MCP.

gbrain (https://github.com/garrytan/gbrain) is a knowledge brain whose MCP
operations line up almost exactly with ``MimirPort``: ``search``, ``get_page``,
``put_page``, ``delete_page``, ``list_pages``. It adds one thing Mímir has
never had — ``think``, a composed answer with citations, which is precisely
the shape of ``MimirQueryResult.answer``. Both existing MimirPort
implementations return ``answer=""``; this one fills it.

This adapter exists to be measured, not assumed. ``src/mimir/eval.py`` scores
a golden set with P@5 / recall@10 / MRR; run it against this adapter and
against the markdown adapter to find out whether gbrain's reranker beats the
current baseline. See NIU-1133.

Transport
---------
JSON-RPC ``tools/call`` over HTTP to gbrain's ``/mcp`` endpoint with a bearer
token (``gbrain auth create <name>``). gbrain may answer as plain JSON or as
an SSE ``data:`` frame, so responses are parsed for both. Markdown ingest goes
to ``/ingest`` when configured, which is the path gbrain documents for bulk
content.

What this adapter deliberately does not do
------------------------------------------
gbrain has no equivalent of Mímir's lint pass, raw-source registry, or mount
summary. Those methods raise instead of returning empty results: an operator
who points a workflow at a brain that cannot lint should be told so, not
handed a clean report over an unlinted corpus. See
``.claude/rules/no-fallbacks.md``.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from niuu.domain.mimir import (
    MimirLintReport,
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
)
from niuu.ports.mimir import MimirPort

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_SEARCH_LIMIT = 10
_UNSUPPORTED = (
    "gbrain has no equivalent for {op}. Mímir-specific operations are not "
    "emulated — point this workflow at a Mímir mount, or drop the call."
)


class GBrainMimirAdapter(MimirPort):
    """Knowledge store backed by gbrain's MCP server.

    Args:
        mcp_url: gbrain MCP endpoint, e.g. ``https://brain.example/mcp``.
        api_token: bearer token from ``gbrain auth create <name>``.
        ingest_url: optional ``/ingest`` endpoint for markdown bulk writes.
        timeout_seconds: HTTP timeout for every call.
        search_limit: results requested per search.
    """

    def __init__(
        self,
        mcp_url: str,
        api_token: str,
        *,
        ingest_url: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> None:
        if not mcp_url:
            raise ValueError("GBrainMimirAdapter requires an MCP URL")
        if not api_token:
            raise ValueError(
                "GBrainMimirAdapter requires an API token; create one with "
                "`gbrain auth create <name>`"
            )
        self._mcp_url = mcp_url.rstrip("/")
        self._ingest_url = ingest_url.rstrip("/") if ingest_url else None
        self._api_token = api_token
        self._timeout_seconds = float(timeout_seconds)
        self._search_limit = search_limit
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke one gbrain MCP tool, raising on any error it reports."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        response = await self._get_client().post(
            self._mcp_url,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()
        message = _parse_mcp_response(response.text)
        if "error" in message:
            raise RuntimeError(f"gbrain MCP error from {name}: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"gbrain MCP returned no result object from {name}: {message}")
        if result.get("isError"):
            raise RuntimeError(f"gbrain MCP tool {name} failed: {_text_result(result)}")
        return result

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(self, query: str) -> list[MimirPage]:
        result = await self._call_tool("search", {"query": query, "limit": self._search_limit})
        return [_page_from_record(r) for r in _records(result)]

    async def query(self, question: str) -> MimirQueryResult:
        """Ask gbrain to compose an answer, not just return pages.

        This is the reason the adapter is interesting: ``MimirQueryResult``
        has always carried an ``answer`` field documented as an LLM-synthesised
        answer, and every existing implementation leaves it empty.
        """
        result = await self._call_tool("think", {"query": question})
        text = _text_result(result)
        records = _records(result)
        return MimirQueryResult(
            question=question,
            answer=text,
            sources=[_page_from_record(r) for r in records],
        )

    async def read_page(self, path: str) -> str:
        result = await self._call_tool("get_page", {"slug": _slug(path)})
        records = _records(result)
        if not records:
            text = _text_result(result)
            if not text:
                raise FileNotFoundError(path)
            return text
        return str(records[0].get("content") or records[0].get("body") or "")

    async def get_page(self, path: str) -> MimirPage:
        result = await self._call_tool("get_page", {"slug": _slug(path)})
        records = _records(result)
        if not records:
            raise FileNotFoundError(path)
        return _page_from_record(records[0], default_path=path)

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        arguments: dict[str, Any] = {}
        if prefix:
            arguments["prefix"] = _slug(prefix)
        if category:
            arguments["category"] = category
        result = await self._call_tool("list_pages", arguments)
        return [_page_from_record(r).meta for r in _records(result)]

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def upsert_page(
        self,
        path: str,
        content: str,
        mimir: str | None = None,
        meta: MimirPageMeta | None = None,
    ) -> None:
        await self._call_tool(
            "put_page",
            {"slug": _slug(path), "content": _with_front_matter(path, content, meta)},
        )

    async def delete_page(self, path: str, mimir: str | None = None) -> bool:
        result = await self._call_tool("delete_page", {"slug": _slug(path)})
        return not result.get("isError", False)

    async def ingest(self, source: MimirSource) -> list[str]:
        """Write a raw source into the brain.

        Uses the documented ``/ingest`` webhook when configured — that is the
        path gbrain intends for bulk markdown — and falls back to ``put_page``
        over MCP otherwise. This is a transport choice between two ways of
        doing the same write, not a degraded mode.
        """
        slug = _slug(f"sources/{source.source_id}")
        markdown = f"# {source.title}\n\n{source.content}"
        if self._ingest_url:
            response = await self._get_client().post(
                self._ingest_url,
                headers={
                    "Authorization": f"Bearer {self._api_token}",
                    "Content-Type": "text/markdown; charset=utf-8",
                    "X-Gbrain-Content-Type": "text/markdown",
                    "X-Gbrain-Source-Id": source.source_id,
                    "X-Gbrain-Source-Uri": source.origin_url
                    or f"mimir://source/{source.source_id}",
                    "X-Gbrain-Slug": slug,
                },
                content=markdown,
            )
            response.raise_for_status()
            return [slug]
        await self._call_tool("put_page", {"slug": slug, "content": markdown})
        return [slug]

    # ------------------------------------------------------------------
    # Mímir-specific surface gbrain does not have
    # ------------------------------------------------------------------

    async def lint(self, fix: bool = False) -> MimirLintReport:
        raise NotImplementedError(_UNSUPPORTED.format(op="lint"))

    async def read_source(self, source_id: str) -> MimirSource | None:
        raise NotImplementedError(_UNSUPPORTED.format(op="read_source"))

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        raise NotImplementedError(_UNSUPPORTED.format(op="list_sources"))

    async def summarize(self) -> MimirMountSummary:
        raise NotImplementedError(_UNSUPPORTED.format(op="summarize"))


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def _parse_mcp_response(raw: str) -> dict[str, Any]:
    """Parse a gbrain MCP reply, which may be JSON or an SSE ``data:`` frame."""
    text = raw.strip()
    if not text:
        raise RuntimeError("gbrain MCP returned an empty response")
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            body = line[len("data:") :].strip()
            if body:
                return json.loads(body)
    raise RuntimeError(f"gbrain MCP returned an unparseable response: {text[:200]}")


def _text_result(result: dict[str, Any]) -> str:
    """Join the text blocks of an MCP tool result."""
    blocks = result.get("content")
    if not isinstance(blocks, list):
        return ""
    return "\n".join(
        str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text"
    ).strip()


def _records(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract page records from a tool result.

    gbrain returns structured content when it can and JSON-in-a-text-block
    otherwise; both shapes are read here so callers never have to care.
    """
    structured = result.get("structuredContent")
    nested = structured.get("results") if isinstance(structured, dict) else None
    for candidate in (structured, nested):
        if isinstance(candidate, list):
            return [r for r in candidate if isinstance(r, dict)]
    text = _text_result(result)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return [r for r in parsed if isinstance(r, dict)]
    if isinstance(parsed, dict):
        results = parsed.get("results") or parsed.get("pages")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    return []


def _page_from_record(record: dict[str, Any], *, default_path: str = "") -> MimirPage:
    path = str(record.get("slug") or record.get("path") or default_path)
    content = str(record.get("content") or record.get("body") or "")
    updated_raw = record.get("updated_at") or record.get("updatedAt")
    try:
        updated = datetime.fromisoformat(str(updated_raw)) if updated_raw else datetime.now(UTC)
    except ValueError:
        updated = datetime.now(UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    meta = MimirPageMeta(
        path=path,
        title=str(record.get("title") or path),
        summary=str(record.get("summary") or record.get("snippet") or ""),
        category=str(record.get("category") or "gbrain"),
        updated_at=updated,
        source_ids=[],
    )
    return MimirPage(meta=meta, content=content)


# ---------------------------------------------------------------------------
# Path / content helpers
# ---------------------------------------------------------------------------

_SLUG_UNSAFE = re.compile(r"[^a-z0-9/_-]+")


def _slug(path: str) -> str:
    """Convert a Mímir page path into a gbrain slug.

    Mímir paths are file-like (``wiki/entities/person-x.md``); gbrain slugs are
    path-like without an extension. The mapping is reversible enough that a
    page written here is findable by the same Mímir path.
    """
    cleaned = path.strip().strip("/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[: -len(".md")]
    cleaned = _SLUG_UNSAFE.sub("-", cleaned.lower())
    return cleaned.strip("-/") or "untitled"


def _with_front_matter(path: str, content: str, meta: MimirPageMeta | None) -> str:
    """Preserve Mímir page metadata as YAML front matter inside the body.

    gbrain stores markdown without Mímir's typed frontmatter fields, so they
    ride along in the document. A page round-tripped through gbrain keeps its
    type and confidence rather than silently losing them.
    """
    if meta is None or content.lstrip().startswith("---"):
        return content
    lines = ["---", f"mimir_path: {path}"]
    if meta.page_type is not None:
        lines.append(f"type: {meta.page_type.value}")
    if meta.confidence is not None:
        lines.append(f"confidence: {meta.confidence.value}")
    if meta.source_ids:
        lines.append(f"source_ids: [{', '.join(meta.source_ids)}]")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + content
