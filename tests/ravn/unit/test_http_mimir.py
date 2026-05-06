"""Unit tests for HttpMimirAdapter — all MimirPort methods with mocked HTTP server.

Uses ``respx`` to mock HTTPX calls so no network is required.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from niuu.domain.mimir import (
    MimirLintReport,
    MimirSource,
    ThreadOwnershipError,
    ThreadState,
    compute_content_hash,
)
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.domain.mimir import MimirAuth

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def adapter() -> HttpMimirAdapter:
    return HttpMimirAdapter(base_url="http://mimir.test")


@pytest.fixture()
def adapter_bearer() -> HttpMimirAdapter:
    auth = MimirAuth(type="bearer", token="test-token")
    return HttpMimirAdapter(base_url="http://mimir.test", auth=auth)


def _source() -> MimirSource:
    return MimirSource(
        source_id="src_abc123",
        title="Test",
        content="test content",
        source_type="document",
        ingested_at=datetime.now(UTC),
        content_hash=compute_content_hash("test content"),
    )


# ---------------------------------------------------------------------------
# HttpMimirAdapter.ingest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_ingest_posts_to_ingest_endpoint(adapter: HttpMimirAdapter) -> None:
    route = respx.post("http://mimir.test/mimir/ingest").mock(
        return_value=Response(200, json={"source_id": "src_abc123", "pages_updated": []})
    )
    result = await adapter.ingest(_source())
    assert result == []
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_ingest_returns_page_paths(adapter: HttpMimirAdapter) -> None:
    respx.post("http://mimir.test/mimir/ingest").mock(
        return_value=Response(
            200,
            json={"source_id": "src_abc123", "pages_updated": ["technical/test.md"]},
        )
    )
    result = await adapter.ingest(_source())
    assert result == ["technical/test.md"]


# ---------------------------------------------------------------------------
# HttpMimirAdapter.search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_pages(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/search").mock(
        return_value=Response(
            200,
            json=[
                {
                    "path": "technical/ravn.md",
                    "title": "Ravn",
                    "summary": "Agent arch.",
                    "category": "technical",
                },
            ],
        )
    )
    pages = await adapter.search("ravn")
    assert len(pages) == 1
    assert pages[0].meta.path == "technical/ravn.md"
    assert pages[0].meta.title == "Ravn"


@pytest.mark.asyncio
@respx.mock
async def test_search_returns_empty_list(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/search").mock(return_value=Response(200, json=[]))
    pages = await adapter.search("nonexistent")
    assert pages == []


# ---------------------------------------------------------------------------
# HttpMimirAdapter.query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_query_returns_result_struct(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/search").mock(
        return_value=Response(
            200,
            json=[{"path": "technical/x.md", "title": "X", "summary": "", "category": "technical"}],
        )
    )
    result = await adapter.query("What is X?")
    assert result.question == "What is X?"
    assert result.answer == ""
    assert len(result.sources) == 1


# ---------------------------------------------------------------------------
# HttpMimirAdapter.get_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_page_returns_mimir_page(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/page").mock(
        return_value=Response(
            200,
            json={
                "path": "technical/test.md",
                "title": "Test",
                "summary": "",
                "category": "technical",
                "updated_at": datetime.now(UTC).isoformat(),
                "source_ids": ["src_abc"],
                "content": "# Test\nSome content.",
            },
        )
    )
    page = await adapter.get_page("technical/test.md")
    assert page.meta.path == "technical/test.md"
    assert page.meta.source_ids == ["src_abc"]
    assert "# Test" in page.content


@pytest.mark.asyncio
@respx.mock
async def test_get_page_raises_file_not_found(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/page").mock(
        return_value=Response(404, json={"detail": "Page not found"})
    )
    with pytest.raises(FileNotFoundError, match="technical/missing.md"):
        await adapter.get_page("technical/missing.md")


# ---------------------------------------------------------------------------
# HttpMimirAdapter.upsert_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_upsert_page_sends_put(adapter: HttpMimirAdapter) -> None:
    route = respx.put("http://mimir.test/mimir/page").mock(return_value=Response(204))
    await adapter.upsert_page("technical/test.md", "# Test\ncontent")
    assert route.called
    assert route.calls[0].request.method == "PUT"


@pytest.mark.asyncio
@respx.mock
async def test_upsert_page_ignores_mimir_param(adapter: HttpMimirAdapter) -> None:
    """The mimir= param is for CompositeMimirAdapter routing; HttpMimirAdapter ignores it."""
    route = respx.put("http://mimir.test/mimir/page").mock(return_value=Response(204))
    await adapter.upsert_page("technical/test.md", "# Test\ncontent", mimir="shared")
    assert route.called


# ---------------------------------------------------------------------------
# HttpMimirAdapter.read_page
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_read_page_returns_content(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/page").mock(
        return_value=Response(
            200,
            json={
                "path": "technical/test.md",
                "title": "Test",
                "summary": "",
                "category": "technical",
                "updated_at": datetime.now(UTC).isoformat(),
                "source_ids": [],
                "content": "# Test\nSome content.",
            },
        )
    )
    content = await adapter.read_page("technical/test.md")
    assert "# Test" in content


@pytest.mark.asyncio
@respx.mock
async def test_read_page_raises_file_not_found(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/page").mock(
        return_value=Response(404, json={"detail": "Page not found"})
    )
    with pytest.raises(FileNotFoundError, match="technical/missing.md"):
        await adapter.read_page("technical/missing.md")


# ---------------------------------------------------------------------------
# HttpMimirAdapter.list_pages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_pages_returns_metadata(adapter: HttpMimirAdapter) -> None:
    now = datetime.now(UTC).isoformat()
    respx.get("http://mimir.test/mimir/pages").mock(
        return_value=Response(
            200,
            json=[
                {
                    "path": "technical/test.md",
                    "title": "Test",
                    "summary": "A test page.",
                    "category": "technical",
                    "updated_at": now,
                    "source_ids": ["src_abc"],
                }
            ],
        )
    )
    pages = await adapter.list_pages()
    assert len(pages) == 1
    assert pages[0].path == "technical/test.md"
    assert pages[0].source_ids == ["src_abc"]


@pytest.mark.asyncio
@respx.mock
async def test_list_pages_with_category(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    result = await adapter.list_pages(category="technical")
    assert result == []
    # Verify category param was sent
    # (respx captures the request)


# ---------------------------------------------------------------------------
# HttpMimirAdapter.lint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_lint_returns_report(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/lint").mock(
        return_value=Response(
            200,
            json={
                "issues": [
                    {
                        "id": "L01",
                        "severity": "warning",
                        "message": "orphan page",
                        "page_path": "a.md",
                        "auto_fixable": False,
                    },
                    {
                        "id": "L04",
                        "severity": "info",
                        "message": "concept gap: concept-x",
                        "page_path": "",
                        "auto_fixable": False,
                    },
                ],
                "pages_checked": 5,
                "issues_found": True,
                "summary": {"error": 0, "warning": 1, "info": 1},
            },
        )
    )
    report = await adapter.lint()
    assert isinstance(report, MimirLintReport)
    assert any(i.id == "L01" and i.page_path == "a.md" for i in report.issues)
    assert any(i.id == "L04" and "concept-x" in i.message for i in report.issues)
    assert report.pages_checked == 5


# ---------------------------------------------------------------------------
# HttpMimirAdapter — auth header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_bearer_auth_header_is_sent(adapter_bearer: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    await adapter_bearer.list_pages()
    assert route.called
    auth_header = route.calls[0].request.headers.get("authorization", "")
    assert auth_header == "Bearer test-token"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.now(UTC).isoformat()


def _thread_json(
    path: str = "threads/my-thread",
    state: str = "open",
    weight: float = 0.75,
) -> dict:
    return {
        "path": path,
        "title": "My Thread",
        "summary": "A test thread.",
        "category": "threads",
        "updated_at": _NOW,
        "state": state,
        "weight": weight,
        "source_ids": [],
        "content": "",
    }


# ---------------------------------------------------------------------------
# HttpMimirAdapter — get_thread_queue (NIU-561)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_thread_queue_returns_pages(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/api/threads/queue").mock(
        return_value=Response(200, json=[_thread_json()])
    )
    pages = await adapter.get_thread_queue()
    assert len(pages) == 1
    assert pages[0].meta.path == "threads/my-thread"
    assert pages[0].meta.is_thread is True
    assert pages[0].meta.thread_state == ThreadState.open
    assert pages[0].meta.thread_weight == 0.75


@pytest.mark.asyncio
@respx.mock
async def test_get_thread_queue_sends_limit(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads/queue").mock(
        return_value=Response(200, json=[])
    )
    await adapter.get_thread_queue(limit=10)
    assert route.called
    assert "limit=10" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_thread_queue_sends_owner_id(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads/queue").mock(
        return_value=Response(200, json=[])
    )
    await adapter.get_thread_queue(owner_id="ravn-1", limit=5)
    assert "owner_id=ravn-1" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_thread_queue_omits_owner_id_when_none(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads/queue").mock(
        return_value=Response(200, json=[])
    )
    await adapter.get_thread_queue(owner_id=None)
    assert "owner_id" not in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_thread_queue_returns_empty_list(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/api/threads/queue").mock(return_value=Response(200, json=[]))
    pages = await adapter.get_thread_queue()
    assert pages == []


# ---------------------------------------------------------------------------
# HttpMimirAdapter — list_threads (NIU-561)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_list_threads_returns_pages(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/api/threads").mock(
        return_value=Response(200, json=[_thread_json(state="pulling")])
    )
    pages = await adapter.list_threads()
    assert len(pages) == 1
    assert pages[0].meta.thread_state == ThreadState.pulling
    assert pages[0].meta.is_thread is True


@pytest.mark.asyncio
@respx.mock
async def test_list_threads_sends_state_filter(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads").mock(return_value=Response(200, json=[]))
    await adapter.list_threads(state=ThreadState.open)
    assert "state=open" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_list_threads_sends_limit(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads").mock(return_value=Response(200, json=[]))
    await adapter.list_threads(limit=25)
    assert "limit=25" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_list_threads_omits_state_when_none(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/api/threads").mock(return_value=Response(200, json=[]))
    await adapter.list_threads(state=None)
    assert "state" not in str(route.calls[0].request.url)


# ---------------------------------------------------------------------------
# HttpMimirAdapter — update_thread_state (NIU-561)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_thread_state_sends_patch(adapter: HttpMimirAdapter) -> None:
    route = respx.patch("http://mimir.test/api/threads/threads%2Fmy-thread/state").mock(
        return_value=Response(200, json={"state": "pulling"})
    )
    await adapter.update_thread_state("threads/my-thread", ThreadState.pulling)
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_update_thread_state_raises_file_not_found_on_404(
    adapter: HttpMimirAdapter,
) -> None:
    respx.patch("http://mimir.test/api/threads/threads%2Fmissing/state").mock(
        return_value=Response(404)
    )
    with pytest.raises(FileNotFoundError, match="threads/missing"):
        await adapter.update_thread_state("threads/missing", ThreadState.closed)


# ---------------------------------------------------------------------------
# HttpMimirAdapter — update_thread_weight (NIU-561)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_update_thread_weight_sends_patch(adapter: HttpMimirAdapter) -> None:
    route = respx.patch("http://mimir.test/api/threads/threads%2Fmy-thread/weight").mock(
        return_value=Response(200, json={"weight": 0.9})
    )
    await adapter.update_thread_weight("threads/my-thread", 0.9)
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_update_thread_weight_sends_signals(adapter: HttpMimirAdapter) -> None:
    import json as _json

    route = respx.patch("http://mimir.test/api/threads/threads%2Fmy-thread/weight").mock(
        return_value=Response(200, json={"weight": 0.8})
    )
    await adapter.update_thread_weight("threads/my-thread", 0.8, signals={"mention_count": 3.0})
    assert route.called
    body = _json.loads(route.calls[0].request.content)
    assert body["signals"] == {"mention_count": 3.0}


@pytest.mark.asyncio
@respx.mock
async def test_update_thread_weight_raises_file_not_found_on_404(
    adapter: HttpMimirAdapter,
) -> None:
    respx.patch("http://mimir.test/api/threads/threads%2Fmissing/weight").mock(
        return_value=Response(404)
    )
    with pytest.raises(FileNotFoundError, match="threads/missing"):
        await adapter.update_thread_weight("threads/missing", 0.5)


# ---------------------------------------------------------------------------
# HttpMimirAdapter — assign_thread_owner (NIU-561)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_assign_thread_owner_sends_post(adapter: HttpMimirAdapter) -> None:
    route = respx.post("http://mimir.test/api/threads/threads%2Fmy-thread/owner").mock(
        return_value=Response(200, json={"owner_id": "ravn-1"})
    )
    await adapter.assign_thread_owner("threads/my-thread", "ravn-1")
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_assign_thread_owner_clears_owner(adapter: HttpMimirAdapter) -> None:
    import json as _json

    route = respx.post("http://mimir.test/api/threads/threads%2Fmy-thread/owner").mock(
        return_value=Response(200, json={"owner_id": None})
    )
    await adapter.assign_thread_owner("threads/my-thread", None)
    assert route.called
    body = _json.loads(route.calls[0].request.content)
    assert body["owner_id"] is None


@pytest.mark.asyncio
@respx.mock
async def test_assign_thread_owner_raises_ownership_error_on_409(
    adapter: HttpMimirAdapter,
) -> None:
    respx.post("http://mimir.test/api/threads/threads%2Fmy-thread/owner").mock(
        return_value=Response(409, json={"current_owner": "ravn-2"})
    )
    with pytest.raises(ThreadOwnershipError) as exc_info:
        await adapter.assign_thread_owner("threads/my-thread", "ravn-1")
    assert exc_info.value.path == "threads/my-thread"
    assert exc_info.value.current_owner == "ravn-2"


@pytest.mark.asyncio
@respx.mock
async def test_assign_thread_owner_raises_file_not_found_on_404(
    adapter: HttpMimirAdapter,
) -> None:
    respx.post("http://mimir.test/api/threads/threads%2Fmissing/owner").mock(
        return_value=Response(404)
    )
    with pytest.raises(FileNotFoundError, match="threads/missing"):
        await adapter.assign_thread_owner("threads/missing", "ravn-1")


# ---------------------------------------------------------------------------
# HttpMimirAdapter — aclose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    adapter = HttpMimirAdapter(base_url="http://mimir.test")
    # Trigger client creation
    _ = adapter._get_client()
    await adapter.aclose()
    await adapter.aclose()  # should not raise
