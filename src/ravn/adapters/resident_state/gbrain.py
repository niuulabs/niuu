"""GBrain resident state adapter.

GBrain is strongest as an external brain/search/synthesis layer. Ravn still
has typed resident records, so this adapter keeps the typed resident state in
local resident pages and projects durable observations into GBrain through the
real GBrain MCP/HTTP server when configured, or the CLI otherwise.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import httpx

from ravn.adapters.process_runner import run_command
from ravn.adapters.resident_state.mimir import LocalResidentState
from ravn.domain.resident_continuation import ResidentMemoryEntry, ResidentTurnRecord
from ravn.resident_continuation import _compact_line, _render_turn_record


class GBrainResidentStateAdapter(LocalResidentState):
    """Resident state adapter that projects memory into a configured GBrain brain."""

    def __init__(
        self,
        root: Path | str,
        *,
        command: str = "gbrain",
        mcp_url: str | None = None,
        ingest_url: str | None = None,
        api_token: str | None = None,
        write_mode: str = "auto",
        capture_enabled: bool = True,
        search_enabled: bool = True,
        timeout_seconds: float = 30.0,
        continuation_prefix: str = "resident/continuation",
    ) -> None:
        super().__init__(Path(root), continuation_prefix=continuation_prefix)
        self._command = command
        self._mcp_url = mcp_url.rstrip("/") if mcp_url else None
        self._ingest_url = ingest_url.rstrip("/") if ingest_url else None
        self._api_token = api_token
        self._write_mode = _normalize_write_mode(write_mode)
        self._capture_enabled = capture_enabled
        self._search_enabled = search_enabled
        self._timeout_seconds = float(timeout_seconds)

    async def available(self) -> bool:
        # Available when a remote brain is configured (MCP/HTTP + token) or the
        # gbrain CLI is on PATH; otherwise a selector should fall back.
        has_remote = bool(self._api_token and (self._mcp_url or self._ingest_url))
        return has_remote or shutil.which(self._command) is not None

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        local = await super().recall(mandate, limit=limit)
        if not self._search_enabled:
            return local
        external = await self._search_gbrain(mandate, limit=limit)
        merged: list[ResidentMemoryEntry] = []
        seen: set[str] = set()
        for entry in (*external, *local):
            if entry.path in seen:
                continue
            seen.add(entry.path)
            merged.append(entry)
            if len(merged) >= limit:
                break
        return merged

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        ref = await super().write_turn(record)
        if self._capture_enabled:
            await self._capture_gbrain(ref, _render_turn_record(record))
        return ref

    async def write_policy_observation(self, observation) -> str:
        ref = await super().write_policy_observation(observation)
        if self._capture_enabled:
            await self._capture_local_ref(ref)
        return ref

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
        case_id: str = "",
        turn_ref: str = "",
    ) -> str:
        ref = await super().write_operator_needed(
            question=question,
            reason=reason,
            turn=turn,
            case_id=case_id,
            turn_ref=turn_ref,
        )
        if self._capture_enabled:
            await self._capture_local_ref(ref)
        return ref

    async def write_operator_answer(self, answer: str, *, case_id: str = "") -> str:
        ref = await super().write_operator_answer(answer, case_id=case_id)
        if self._capture_enabled:
            await self._capture_local_ref(ref)
        return ref

    async def _capture_gbrain(self, ref: str, content: str) -> None:
        title = f"Ravn resident memory: {ref}"
        if self._write_mode in {"auto", "put_page"} and self._mcp_url and self._api_token:
            await self._put_page_gbrain(ref, title, content)
            return
        if self._write_mode in {"auto", "ingest"} and self._ingest_url and self._api_token:
            await self._ingest_gbrain(_slug_from_ref(ref), _gbrain_markdown(ref, title, content))
            return
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as fh:
            fh.write(f"# {title}\n\n{content}")
            tmp_path = Path(fh.name)
        try:
            await run_command(
                [self._command, "capture", "--file", str(tmp_path)],
                timeout_seconds=self._timeout_seconds,
            )
        finally:
            tmp_path.unlink(missing_ok=True)

    async def _capture_local_ref(self, ref: str) -> None:
        path = self._root / ref
        if path.exists():
            await self._capture_gbrain(ref, path.read_text(encoding="utf-8"))

    async def _search_gbrain(self, query: str, *, limit: int) -> list[ResidentMemoryEntry]:
        if self._mcp_url and self._api_token:
            result = await self._call_mcp_tool(
                "search",
                {"query": _compact_line(query), "limit": limit},
            )
            text = _mcp_text_result(result)
            return _entries_from_gbrain_search(text, limit=limit)
        result = await run_command(
            [self._command, "search", _compact_line(query), "--limit", str(limit)],
            timeout_seconds=self._timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            return []
        return _entries_from_gbrain_search(result.stdout, limit=limit)

    async def _put_page_gbrain(self, ref: str, title: str, content: str) -> None:
        slug = _slug_from_ref(ref)
        markdown = _gbrain_markdown(ref, title, content)
        await self._call_mcp_tool("put_page", {"slug": slug, "content": markdown})

    async def _ingest_gbrain(self, slug: str, content: str) -> None:
        if not self._ingest_url or not self._api_token:
            raise RuntimeError("GBrain ingest URL and API token are required for ingest calls")
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "text/markdown; charset=utf-8",
            "X-Gbrain-Content-Type": "text/markdown",
            "X-Gbrain-Source-Id": "ravn-resident",
            "X-Gbrain-Source-Uri": f"ravn://resident/{slug}",
            "X-Gbrain-Slug": slug,
        }
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(self._ingest_url, headers=headers, content=content)
            response.raise_for_status()

    async def _call_mcp_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self._mcp_url or not self._api_token:
            raise RuntimeError("GBrain MCP URL and API token are required for MCP calls")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(self._mcp_url, headers=headers, json=payload)
            response.raise_for_status()
        message = _parse_mcp_http_response(response.text)
        if "error" in message:
            raise RuntimeError(f"GBrain MCP error from {name}: {message['error']}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError(f"GBrain MCP returned invalid result from {name}: {message}")
        if result.get("isError"):
            raise RuntimeError(f"GBrain MCP tool {name} failed: {_mcp_text_result(result)}")
        return result


def _entries_from_gbrain_search(raw: str, *, limit: int) -> list[ResidentMemoryEntry]:
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return _entries_from_gbrain_text_search(raw, limit=limit)
    items = parsed.get("results") if isinstance(parsed, dict) else parsed
    if not isinstance(items, list):
        return []
    entries: list[ResidentMemoryEntry] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or item.get("slug") or item.get("page_slug") or "")
        summary = str(item.get("title") or item.get("summary") or path or "gbrain result")
        content = str(item.get("content") or item.get("excerpt") or item.get("text") or "")
        entries.append(
            ResidentMemoryEntry(
                path=f"gbrain:{path}" if path else f"gbrain:result-{len(entries) + 1}",
                summary=summary,
                content=content,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _entries_from_gbrain_text_search(raw: str, *, limit: int) -> list[ResidentMemoryEntry]:
    entries: list[ResidentMemoryEntry] = []
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.lower().startswith(("usage:", "options:", "keyword search")):
            continue
        entries.append(
            ResidentMemoryEntry(
                path=f"gbrain:search-{len(entries) + 1}",
                summary=text,
                content=text,
            )
        )
        if len(entries) >= limit:
            break
    return entries


def _parse_mcp_http_response(raw: str) -> dict[str, Any]:
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line.removeprefix("data:").strip()
        if payload:
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("GBrain MCP response was not a JSON object")
    return parsed


def _mcp_text_result(result: dict[str, Any]) -> str:
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(parts)


def _normalize_write_mode(write_mode: str) -> str:
    mode = str(write_mode or "auto").strip().casefold().replace("-", "_")
    if mode not in {"auto", "put_page", "ingest", "capture"}:
        raise ValueError("GBrain write_mode must be one of: auto, put_page, ingest, capture")
    return mode


def _gbrain_markdown(ref: str, title: str, content: str) -> str:
    return f"---\ntype: note\n---\n# {title}\n\nSource ref: `{ref}`\n\n{content}"


def _slug_from_ref(ref: str) -> str:
    path = ref.removesuffix(".md").strip("/")
    safe = "".join(ch if ch.isalnum() or ch in {"/", "-", "_", "."} else "-" for ch in path)
    segments = tuple(_gbrain_slug_segment(segment) for segment in safe.split("/") if segment)
    projected = "/".join(segments)
    return f"resident/ravn/{projected}" if not projected.startswith("resident/") else projected


def _gbrain_slug_segment(segment: str) -> str:
    cleaned = segment.strip("-") or "item"
    cleaned = re.sub(
        r"^(\d{8})T(\d{6})Z(?=-|$)",
        r"\1-\2",
        cleaned,
    )
    if cleaned[0].isdigit():
        return f"t{cleaned}"
    return cleaned
