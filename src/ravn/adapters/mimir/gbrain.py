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
Raw sources are stored as source pages with a lossless JSON payload so
GBrain markdown normalization cannot alter the evidence. The lint method raises: an operator
who points a workflow at a brain that cannot lint should be told so, not
handed a clean report over an unlinted corpus. See
``.claude/rules/no-fallbacks.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import httpx
import yaml

from niuu.domain.mimir import (
    EntityType,
    MimirLintReport,
    MimirMountSummary,
    MimirPage,
    MimirPageMeta,
    MimirQueryResult,
    MimirSource,
    MimirSourceMeta,
    PageType,
    compute_content_hash,
    compute_source_id,
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
        think_model: model that ``query()`` asks gbrain to synthesise with,
            as ``<provider>:<model>``. Required in practice: ``think`` ignores
            the brain's configured ``chat_model`` and falls back to a
            hardcoded Anthropic default, so without this every synthesis
            reports ``NO_ANTHROPIC_API_KEY`` however the brain is configured.
        query_expansion: whether gbrain may spend an LLM call rewriting each
            query into variants before retrieving. It is gbrain's default and
            usually helps recall, but it needs a working chat model and makes
            every search cost a generation — so it is explicit here rather
            than left to the server's mood, and a bake-off can hold it
            constant on both sides.
    """

    def __init__(
        self,
        mcp_url: str,
        api_token: str = "",
        *,
        api_token_file: str = "",
        ingest_url: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT,
        search_limit: int = _DEFAULT_SEARCH_LIMIT,
        think_model: str = "",
        query_expansion: bool = True,
    ) -> None:
        if not mcp_url:
            raise ValueError("GBrainMimirAdapter requires an MCP URL")
        if api_token_file:
            from pathlib import Path

            api_token = Path(api_token_file).expanduser().read_text().strip()
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
        self._think_model = think_model
        self._query_expansion = query_expansion
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
            detail = _text_result(result)
            if name == "get_page":
                try:
                    error = json.loads(detail)
                except json.JSONDecodeError:
                    error = None
                if isinstance(error, dict) and error.get("error") == "page_not_found":
                    raise FileNotFoundError(arguments["slug"])
            raise RuntimeError(f"gbrain MCP tool {name} failed: {detail}")
        return result

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def search(self, query: str) -> list[MimirPage]:
        """Retrieve pages via gbrain's hybrid path.

        gbrain exposes two retrieval tools and the names are misleading:
        ``search`` is keyword-only (Postgres tsvector), while ``query`` is the
        hybrid one — vector + keyword + RRF + multi-query expansion. Mímir's
        ``search()`` is hybrid, so ``query`` is the like-for-like mapping.
        Wiring this to ``search`` would score gbrain's weakest retrieval path
        against Mímir's strongest and call the result a comparison.
        """
        result = await self._call_tool(
            "query",
            {"query": query, "limit": self._search_limit, "expand": self._query_expansion},
        )
        return [_page_from_record(r) for r in _records(result)]

    async def query(self, question: str) -> MimirQueryResult:
        """Ask gbrain to compose an answer, not just return pages.

        This is the reason the adapter is interesting: ``MimirQueryResult``
        has always carried an ``answer`` field documented as an LLM-synthesised
        answer, and every existing implementation leaves it empty.

        ``think`` answers HTTP 200 even when it could not synthesise anything —
        with no chat model configured it returns the literal string
        ``"(no LLM available — set ANTHROPIC_API_KEY or pass `client`)"``
        alongside ``synthesisOk: false``. Handing that back as an answer would
        put a placeholder into a resident's reasoning and call it knowledge, so
        an unsuccessful synthesis raises. See ``.claude/rules/no-fallbacks.md``.
        """
        arguments: dict[str, Any] = {"question": question}
        if self._think_model:
            arguments["model"] = self._think_model
        result = await self._call_tool("think", arguments)
        payload = _think_payload(result)
        if payload.get("synthesisOk") is False:
            detail = "; ".join(
                str(item)
                for item in [*payload.get("warnings", []), *payload.get("gaps", [])]
                if item
            )
            raise RuntimeError(
                f"gbrain think could not synthesise an answer for {question!r}"
                f"{': ' + detail if detail else ''}"
            )
        return MimirQueryResult(
            question=question,
            answer=str(payload.get("answer") or _text_result(result)),
            sources=[_page_from_record(r) for r in _citation_records(payload, result)],
        )

    async def read_page(self, path: str) -> str:
        result = await self._call_tool("get_page", {"slug": _slug(path), "include_content": True})
        records = _records(result)
        if not records:
            text = _text_result(result)
            if not text:
                raise FileNotFoundError(path)
            return text
        return str(records[0].get("content") or records[0].get("body") or "")

    async def get_page(self, path: str) -> MimirPage:
        result = await self._call_tool("get_page", {"slug": _slug(path), "include_content": True})
        records = _records(result)
        if not records:
            raise FileNotFoundError(path)
        return _page_from_record(records[0], default_path=path)

    async def list_pages(
        self,
        category: str | None = None,
        prefix: str | None = None,
    ) -> list[MimirPageMeta]:
        pages: list[MimirPageMeta] = []
        offset = 0
        while True:
            result = await self._call_tool(
                "list_pages", {"limit": 100, "offset": offset, "sort": "slug"}
            )
            records = _records(result)
            pages.extend(_page_from_record(record).meta for record in records)
            if len(records) < 100:
                break
            offset += len(records)
        return [
            page
            for page in pages
            if (not prefix or page.path.startswith(_slug(prefix)))
            and (not category or page.category == category)
        ]

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
        # JSON preserves the exact raw bytes represented by the string, including
        # whitespace, frontmatter and timeline markers that GBrain otherwise parses.
        payload = {**asdict(source), "ingested_at": source.ingested_at.isoformat()}
        front = yaml.safe_dump(
            {"type": "source", "title": source.title, "mimir_source_format": "json-v1"},
            sort_keys=False,
        )
        markdown = f"---\n{front}---\n\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```"
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
        try:
            result = await self._call_tool(
                "get_page", {"slug": _slug(f"sources/{source_id}"), "include_content": True}
            )
        except FileNotFoundError:
            return None
        records = _records(result)
        if not records:
            raise RuntimeError(f"gbrain returned no source record for {source_id}")
        return _source_from_record(source_id, records[0])

    async def list_sources(self, *, unprocessed_only: bool = False) -> list[MimirSourceMeta]:
        pages = await self.list_pages()
        referenced = {source_id for page in pages for source_id in page.source_ids}
        sources = []
        for page in pages:
            if not page.path.startswith("sources/src_"):
                continue
            source_id = page.path.removeprefix("sources/")
            if unprocessed_only and source_id in referenced:
                continue
            source = await self.read_source(source_id)
            if source is None:
                continue
            if unprocessed_only and source.source_type == "diagnostic":
                continue
            sources.append(
                MimirSourceMeta(
                    source_id=source.source_id,
                    title=source.title,
                    ingested_at=source.ingested_at,
                    source_type=source.source_type,
                    origin_url=source.origin_url,
                )
            )
        return sources

    async def inspect_instance(self) -> dict:
        response = await self._get_client().get(self._mcp_url.removesuffix("/mcp") + "/health")
        response.raise_for_status()
        health = response.json()
        summary = await self.summarize()
        return {
            "backend": "gbrain",
            "metrics": {
                "Version": health.get("version", "Not reported"),
                "Storage engine": health.get("engine", "Not reported"),
                "Health": health.get("status", "Not reported"),
                "Pages": summary.page_count,
                "Categories": len(summary.categories),
            },
            "unavailable": [
                "Dream-cycle history is not exposed by this MCP adapter",
                "Embedding coverage",
                "Synthesis activity",
            ],
        }

    async def summarize(self) -> MimirMountSummary:
        pages = await self.list_pages()
        return MimirMountSummary(
            page_count=len(pages),
            source_count=sum(page.path.startswith("sources/src_") for page in pages),
            categories=sorted({page.category for page in pages}),
            last_write=max((page.updated_at for page in pages), default=None),
        )


def _source_from_record(source_id: str, record: dict[str, Any]) -> MimirSource:
    """Decode source evidence, including the original heading-plus-body format."""
    content = str(record.get("content") or "")
    front = record.get("frontmatter") or {}
    if content.startswith("---\n"):
        _, header, content = content.split("---", 2)
        front = {**(yaml.safe_load(header) or {}), **front}
    body = str(record.get("compiled_truth", content)).strip()
    if front.get("mimir_source_format") == "json-v1":
        if not body.startswith("```json\n") or not body.endswith("\n```"):
            raise ValueError(f"Malformed GBrain raw source: {source_id}")
        payload = json.loads(body.removeprefix("```json\n").removesuffix("\n```"))
        source = MimirSource(
            **{**payload, "ingested_at": datetime.fromisoformat(payload["ingested_at"])}
        )
        if (
            source.source_id != source_id
            or compute_content_hash(source.content) != source.content_hash
        ):
            raise ValueError(f"GBrain raw source integrity check failed: {source_id}")
        return source

    # The old ingest stored '# title\n\ncontent' without Mimir metadata.
    # GBrain trims markdown whitespace. Only accept a legacy body if its
    # canonical content-addressed ID proves it is the original evidence.
    heading = f"# {record.get('title') or front.get('title') or ''}\n\n"
    raw = body.removeprefix(heading)
    if compute_source_id(raw) != source_id:
        raise ValueError(
            f"GBrain legacy source integrity check failed: {source_id}; "
            "re-ingest the original source"
        )
    origin_url = record.get("source_uri")
    return MimirSource(
        source_id=source_id,
        title=str(record.get("title") or front.get("title") or source_id),
        content=raw,
        content_hash=compute_content_hash(raw),
        source_type="web"
        if origin_url and origin_url.startswith(("http://", "https://"))
        else "document",
        origin_url=origin_url,
        ingested_at=datetime.fromisoformat(str(record.get("ingested_at") or record["created_at"])),
    )


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
    for line in text.split("\n"):
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
    if isinstance(structured, dict) and (structured.get("slug") or structured.get("path")):
        return [structured]
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
        if parsed.get("slug") or parsed.get("path"):
            return [parsed]
        results = parsed.get("results") or parsed.get("pages")
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
    return []


def _think_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Read ``think``'s report object out of its text block.

    ``think`` returns a single JSON document — answer, citations, gaps,
    warnings, ``synthesisOk`` — rather than the page-record list the retrieval
    tools return, so it needs its own parse.
    """
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    text = _text_result(result)
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _citation_records(payload: dict[str, Any], result: dict[str, Any]) -> list[dict[str, Any]]:
    """Cited pages behind a ``think`` answer, however gbrain shaped them."""
    citations = payload.get("citations")
    if isinstance(citations, list):
        return [c if isinstance(c, dict) else {"slug": str(c)} for c in citations]
    return _records(result)


def _page_from_record(record: dict[str, Any], *, default_path: str = "") -> MimirPage:
    # `think` citations key the page as `page_slug`; the retrieval tools use
    # `slug`. Reading only one of them silently produced empty-path sources.
    path = str(record.get("slug") or record.get("page_slug") or record.get("path") or default_path)
    content = str(record.get("content") or record.get("body") or "")
    updated_raw = record.get("updated_at") or record.get("updatedAt")
    try:
        updated = datetime.fromisoformat(str(updated_raw)) if updated_raw else datetime.now(UTC)
    except ValueError:
        updated = datetime.now(UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    front: dict[str, Any] = {}
    if content.startswith("---\n"):
        sections = content.split("---", 2)
        if len(sections) == 3:
            parsed = yaml.safe_load(sections[1])
            if isinstance(parsed, dict):
                front = parsed
    metadata = {
        **front,
        **record.get("frontmatter", {}),
        **{key: value for key, value in record.items() if value is not None},
    }
    page_type = metadata.get("type")
    entity_type = metadata.get("entity_type")
    meta = MimirPageMeta(
        path=path,
        title=str(record.get("title") or path),
        summary=str(metadata.get("summary") or metadata.get("snippet") or ""),
        category=str(
            metadata.get("category") or (path.split("/", 1)[0] if "/" in path else "gbrain")
        ),
        updated_at=updated,
        source_ids=list(metadata.get("source_ids") or []),
        related_entities=list(metadata.get("related_entities") or []),
        page_type=PageType(page_type) if page_type in PageType._value2member_map_ else None,
        entity_type=EntityType(entity_type)
        if entity_type in EntityType._value2member_map_
        else None,
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
