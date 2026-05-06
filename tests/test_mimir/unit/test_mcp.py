"""Unit tests for MimirMcpServer — JSON-RPC protocol, all six tools, and transports.

Tests use a real MarkdownMimirAdapter backed by a tmp_path — no network, no mocking.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.mcp import MimirMcpServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(tmp_path: Path) -> MimirMcpServer:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    return MimirMcpServer(adapter=adapter, name="test")


def _make_client(tmp_path: Path) -> TestClient:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    server = MimirMcpServer(adapter=adapter, name="test")
    app = FastAPI()
    app.include_router(server.router(), prefix="/mcp")
    return TestClient(app)


def _jsonrpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    msg: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def _seed_page(tmp_path: Path) -> None:
    """Write a wiki page directly so tools have something to find."""
    wiki = tmp_path / "mimir" / "wiki" / "technical"
    wiki.mkdir(parents=True, exist_ok=True)
    (wiki / "ravn.md").write_text(
        "# Ravn Architecture\n"
        "Ravn is the autonomous agent framework.\n\n"
        "## Overview\n"
        "Six regions collaborate via nng synapses.\n"
        "<!-- sources: src_abc123 -->",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Protocol: initialize, ping, tools/list
# ---------------------------------------------------------------------------


class TestProtocol:
    def test_initialize(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post("/mcp", json=_jsonrpc("initialize"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        result = data["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert result["serverInfo"]["name"] == "test"
        assert "tools" in result["capabilities"]

    def test_ping(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post("/mcp", json=_jsonrpc("ping"))
        assert resp.status_code == 200
        assert resp.json()["result"] == {}

    def test_tools_list(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post("/mcp", json=_jsonrpc("tools/list"))
        assert resp.status_code == 200
        tools = resp.json()["result"]["tools"]
        names = {t["name"] for t in tools}
        assert names == {
            "mimir_search",
            "mimir_read",
            "mimir_write",
            "mimir_ingest",
            "mimir_lint",
            "mimir_stats",
        }

    def test_method_not_found(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post("/mcp", json=_jsonrpc("nonexistent/method"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["error"]["code"] == -32601

    def test_notification_returns_204(self, tmp_path: Path) -> None:
        """Notifications (no id) should return 204 No Content."""
        client = _make_client(tmp_path)
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "method": "ping"})
        assert resp.status_code == 204

    def test_parse_error(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp", content=b"not json", headers={"content-type": "application/json"}
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32700

    def test_batch_request(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        batch = [
            _jsonrpc("ping", req_id=1),
            _jsonrpc("initialize", req_id=2),
        ]
        resp = client.post("/mcp", json=batch)
        assert resp.status_code == 200
        results = resp.json()
        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert ids == {1, 2}

    def test_batch_notifications_returns_204(self, tmp_path: Path) -> None:
        """A batch of only notifications should return 204."""
        client = _make_client(tmp_path)
        batch = [
            {"jsonrpc": "2.0", "method": "ping"},
            {"jsonrpc": "2.0", "method": "ping"},
        ]
        resp = client.post("/mcp", json=batch)
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Tool: mimir_search
# ---------------------------------------------------------------------------


class TestSearchTool:
    def test_search_empty(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_search", "arguments": {"query": "ravn"}},
            ),
        )
        assert resp.status_code == 200
        content = resp.json()["result"]["content"]
        results = json.loads(content[0]["text"])
        assert results == []

    def test_search_with_results(self, tmp_path: Path) -> None:
        _seed_page(tmp_path)
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_search", "arguments": {"query": "ravn agent"}},
            ),
        )
        content = resp.json()["result"]["content"]
        results = json.loads(content[0]["text"])
        assert len(results) >= 1
        assert results[0]["path"] == "technical/ravn.md"
        assert results[0]["title"] == "Ravn Architecture"

    def test_search_respects_limit(self, tmp_path: Path) -> None:
        _seed_page(tmp_path)
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_search", "arguments": {"query": "ravn", "limit": 1}},
            ),
        )
        content = resp.json()["result"]["content"]
        results = json.loads(content[0]["text"])
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Tool: mimir_read
# ---------------------------------------------------------------------------


class TestReadTool:
    def test_read_existing_page(self, tmp_path: Path) -> None:
        _seed_page(tmp_path)
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_read", "arguments": {"path": "technical/ravn.md"}},
            ),
        )
        content = resp.json()["result"]["content"]
        result = json.loads(content[0]["text"])
        assert result["path"] == "technical/ravn.md"
        assert result["title"] == "Ravn Architecture"
        assert "Ravn is the autonomous agent framework" in result["content"]
        assert "updated_at" in result
        assert result["source_ids"] == ["src_abc123"]

    def test_read_missing_page(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_read", "arguments": {"path": "technical/missing.md"}},
            ),
        )
        content = resp.json()["result"]["content"]
        assert "not found" in content[0]["text"].lower()


# ---------------------------------------------------------------------------
# Tool: mimir_write
# ---------------------------------------------------------------------------


class TestWriteTool:
    def test_write_new_page(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_write",
                    "arguments": {
                        "path": "technical/new.md",
                        "content": "# New Page\nSome content about testing.",
                    },
                },
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["path"] == "technical/new.md"
        assert result["title"] == "New Page"

    def test_write_with_frontmatter(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_write",
                    "arguments": {
                        "path": "decisions/adr-001.md",
                        "content": "# ADR-001\nWe chose hexagonal architecture.",
                        "frontmatter": {
                            "type": "decision",
                            "confidence": "high",
                        },
                    },
                },
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["path"] == "decisions/adr-001.md"

        # Verify the frontmatter was written to disk
        page_path = tmp_path / "mimir" / "wiki" / "decisions" / "adr-001.md"
        raw = page_path.read_text()
        assert raw.startswith("---\n")
        assert "type: decision" in raw

    def test_write_without_frontmatter(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_write",
                    "arguments": {
                        "path": "technical/plain.md",
                        "content": "# Plain Page\nNo frontmatter here.",
                    },
                },
            ),
        )
        page_path = tmp_path / "mimir" / "wiki" / "technical" / "plain.md"
        raw = page_path.read_text()
        assert not raw.startswith("---")


# ---------------------------------------------------------------------------
# Tool: mimir_ingest
# ---------------------------------------------------------------------------


class TestIngestTool:
    def test_ingest_document(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_ingest",
                    "arguments": {
                        "content": "# Meeting Notes\nWe discussed the new auth flow.",
                        "source_type": "document",
                    },
                },
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["source_id"].startswith("src_")
        assert isinstance(result["pages_updated"], list)

    def test_ingest_with_title_and_url(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_ingest",
                    "arguments": {
                        "content": "Some scraped content about k8s.",
                        "title": "K8s Patterns",
                        "source_type": "url",
                        "origin_url": "https://example.com/k8s",
                    },
                },
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["source_id"].startswith("src_")

    def test_ingest_derives_title_from_content(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {
                    "name": "mimir_ingest",
                    "arguments": {"content": "# Auto Title\nBody text here."},
                },
            ),
        )
        # Should succeed without explicit title
        assert resp.json()["result"]["content"][0]["type"] == "text"


# ---------------------------------------------------------------------------
# Tool: mimir_lint
# ---------------------------------------------------------------------------


class TestLintTool:
    def test_lint_empty_wiki(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_lint", "arguments": {}},
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["pages_checked"] == 0
        assert result["issues_found"] is False
        assert "summary" in result
        assert isinstance(result["issues"], list)

    def test_lint_detects_issues(self, tmp_path: Path) -> None:
        _seed_page(tmp_path)
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_lint", "arguments": {}},
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["pages_checked"] >= 1
        assert "issues" in result


# ---------------------------------------------------------------------------
# Tool: mimir_stats
# ---------------------------------------------------------------------------


class TestStatsTool:
    def test_stats_empty(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_stats", "arguments": {}},
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["page_count"] == 0
        assert result["categories"] == []
        assert result["healthy"] is True

    def test_stats_with_pages(self, tmp_path: Path) -> None:
        _seed_page(tmp_path)
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "mimir_stats", "arguments": {}},
            ),
        )
        result_content = resp.json()["result"]["content"]
        result = json.loads(result_content[0]["text"])
        assert result["page_count"] >= 1
        assert "technical" in result["categories"]


# ---------------------------------------------------------------------------
# Tool: unknown tool
# ---------------------------------------------------------------------------


class TestUnknownTool:
    def test_unknown_tool_returns_error(self, tmp_path: Path) -> None:
        client = _make_client(tmp_path)
        resp = client.post(
            "/mcp",
            json=_jsonrpc(
                "tools/call",
                {"name": "nonexistent_tool", "arguments": {}},
            ),
        )
        data = resp.json()
        assert data["error"]["code"] == -32603


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


class TestStdioTransport:
    async def test_stdio_initialize_and_ping(self, tmp_path: Path) -> None:
        server = _make_server(tmp_path)

        init_msg = json.dumps(_jsonrpc("initialize")) + "\n"
        ping_msg = json.dumps(_jsonrpc("ping", req_id=2)) + "\n"
        stdin = io.StringIO(init_msg + ping_msg)
        stdout = io.StringIO()

        await server.run_stdio(stdin=stdin, stdout=stdout)

        stdout.seek(0)
        lines = stdout.readlines()
        assert len(lines) == 2

        init_resp = json.loads(lines[0])
        assert init_resp["result"]["protocolVersion"] == "2024-11-05"

        ping_resp = json.loads(lines[1])
        assert ping_resp["result"] == {}

    async def test_stdio_tool_call(self, tmp_path: Path) -> None:
        server = _make_server(tmp_path)

        msg = (
            json.dumps(
                _jsonrpc(
                    "tools/call",
                    {"name": "mimir_stats", "arguments": {}},
                )
            )
            + "\n"
        )
        stdin = io.StringIO(msg)
        stdout = io.StringIO()

        await server.run_stdio(stdin=stdin, stdout=stdout)

        stdout.seek(0)
        resp = json.loads(stdout.readline())
        result = json.loads(resp["result"]["content"][0]["text"])
        assert result["page_count"] == 0

    async def test_stdio_parse_error(self, tmp_path: Path) -> None:
        server = _make_server(tmp_path)

        stdin = io.StringIO("not valid json\n")
        stdout = io.StringIO()

        await server.run_stdio(stdin=stdin, stdout=stdout)

        stdout.seek(0)
        resp = json.loads(stdout.readline())
        assert resp["error"]["code"] == -32700

    async def test_stdio_skips_blank_lines(self, tmp_path: Path) -> None:
        server = _make_server(tmp_path)

        msg = "\n\n" + json.dumps(_jsonrpc("ping")) + "\n\n"
        stdin = io.StringIO(msg)
        stdout = io.StringIO()

        await server.run_stdio(stdin=stdin, stdout=stdout)

        stdout.seek(0)
        lines = [ln for ln in stdout.readlines() if ln.strip()]
        assert len(lines) == 1

    async def test_stdio_notification_no_output(self, tmp_path: Path) -> None:
        server = _make_server(tmp_path)

        notification = json.dumps({"jsonrpc": "2.0", "method": "ping"}) + "\n"
        stdin = io.StringIO(notification)
        stdout = io.StringIO()

        await server.run_stdio(stdin=stdin, stdout=stdout)

        stdout.seek(0)
        assert stdout.read() == ""
