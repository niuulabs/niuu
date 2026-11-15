"""Unit tests for HttpMimirAdapter — all MimirPort methods with mocked HTTP server.

Uses ``respx`` to mock HTTPX calls so no network is required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import respx
from httpx import Response

from mimir.adapters import http as http_module
from mimir.adapters.http import HttpMimirAdapter
from mimir.domain import MimirAuth
from mimir.exceptions import MimirConfigurationError
from niuu.domain.mimir import (
    MimirSource,
    compute_content_hash,
)

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


@pytest.mark.asyncio
@respx.mock
async def test_ingest_rejects_source_id_contract_mismatch(adapter: HttpMimirAdapter) -> None:
    respx.post("http://mimir.test/mimir/ingest").mock(
        return_value=Response(200, json={"source_id": "src_different", "pages_updated": []})
    )

    with pytest.raises(RuntimeError, match="source ID contract mismatch"):
        await adapter.ingest(_source())


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


@pytest.mark.asyncio
@respx.mock
async def test_workload_auth_uses_configured_identity_endpoints(
    tmp_path: Path,
) -> None:
    proof_file = tmp_path / "workload-token"
    proof_file.write_text("projected-proof", encoding="utf-8")
    exchange = respx.post("http://identity.test/exchange").mock(
        return_value=Response(200, json={"token": "mimir-token"})
    )
    request = respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(
            type="workload",
            token_file=str(proof_file),
            exchange_url="http://identity.test/exchange",
        ),
    )

    await adapter.list_pages()

    assert exchange.calls[0].request.content == (
        b'{"token":"projected-proof","audiences":["mimir"]}'
    )
    assert request.calls[0].request.headers["authorization"] == "Bearer mimir-token"


@pytest.mark.asyncio
@respx.mock
async def test_no_auth_configured_sends_no_authorization_header() -> None:
    """No auth configured at all is an operator decision — send no credentials."""
    route = respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    adapter = HttpMimirAdapter(base_url="http://mimir.test")

    await adapter.list_pages()

    assert route.called
    assert "authorization" not in route.calls[0].request.headers


@pytest.mark.asyncio
async def test_workload_auth_missing_token_file_raises_configuration_error(
    tmp_path: Path,
) -> None:
    missing_file = tmp_path / "does-not-exist"
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(
            type="workload",
            token_file=str(missing_file),
            exchange_url="http://identity.test/exchange",
        ),
    )

    with pytest.raises(MimirConfigurationError, match="http://mimir.test") as exc_info:
        await adapter._resolve_workload_token()
    assert str(missing_file) in str(exc_info.value)


@pytest.mark.asyncio
async def test_workload_auth_empty_token_file_raises_configuration_error(
    tmp_path: Path,
) -> None:
    empty_file = tmp_path / "workload-token"
    empty_file.write_text("   ", encoding="utf-8")
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(
            type="workload",
            token_file=str(empty_file),
            exchange_url="http://identity.test/exchange",
        ),
    )

    with pytest.raises(MimirConfigurationError, match="empty"):
        await adapter._resolve_workload_token()


@pytest.mark.asyncio
async def test_workload_auth_missing_exchange_url_raises_configuration_error(
    tmp_path: Path,
) -> None:
    proof_file = tmp_path / "workload-token"
    proof_file.write_text("projected-proof", encoding="utf-8")
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(type="workload", token_file=str(proof_file), exchange_url=None),
    )

    with pytest.raises(MimirConfigurationError, match="exchange_url"):
        await adapter._resolve_workload_token()


@pytest.mark.asyncio
@respx.mock
async def test_workload_token_is_cached_within_the_refresh_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof_file = tmp_path / "workload-token"
    proof_file.write_text("projected-proof", encoding="utf-8")
    exchange = respx.post("http://identity.test/exchange").mock(
        return_value=Response(200, json={"token": "mimir-token", "expiresAt": 1300.0})
    )
    respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    monkeypatch.setattr(http_module, "time", _FakeTimeModule(1000.0, 1010.0))
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(
            type="workload",
            token_file=str(proof_file),
            exchange_url="http://identity.test/exchange",
        ),
    )

    await adapter.list_pages()
    await adapter.list_pages()

    # 1300 - default 30s margin = 1270 > 1010 (second call's "now") — cache hit.
    assert exchange.calls.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_workload_token_refresh_margin_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof_file = tmp_path / "workload-token"
    proof_file.write_text("projected-proof", encoding="utf-8")
    exchange = respx.post("http://identity.test/exchange").mock(
        return_value=Response(200, json={"token": "mimir-token", "expiresAt": 1300.0})
    )
    respx.get("http://mimir.test/mimir/pages").mock(return_value=Response(200, json=[]))
    monkeypatch.setattr(http_module, "time", _FakeTimeModule(1000.0, 1010.0))
    adapter = HttpMimirAdapter(
        base_url="http://mimir.test",
        auth=MimirAuth(
            type="workload",
            token_file=str(proof_file),
            exchange_url="http://identity.test/exchange",
        ),
        workload_token_refresh_margin_seconds=500.0,
    )

    await adapter.list_pages()
    await adapter.list_pages()

    # 1300 - 500s margin = 800, which is *not* > 1010 — a fresh exchange fires.
    assert exchange.calls.call_count == 2


class _FakeTimeModule:
    """Stand-in for the ``time`` module binding inside ``mimir.adapters.http``.

    Replaces only that module's local ``time`` name (via monkeypatch), never the
    real global ``time`` module — httpx's own internals also call ``time.time()``
    per request and must keep seeing the real clock.
    """

    def __init__(self, *values: float) -> None:
        self._remaining = list(values)

    def time(self) -> float:
        if len(self._remaining) > 1:
            return self._remaining.pop(0)
        return self._remaining[0]


# ---------------------------------------------------------------------------
# HttpMimirAdapter — aclose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aclose_is_idempotent() -> None:
    adapter = HttpMimirAdapter(base_url="http://mimir.test")
    # Trigger client creation
    _ = await adapter._get_client()
    await adapter.aclose()
    await adapter.aclose()  # should not raise


# ---------------------------------------------------------------------------
# HttpMimirAdapter — summarize
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_summarize_parses_the_summary_endpoint(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/summary").mock(
        return_value=Response(
            200,
            json={
                "page_count": 568,
                "source_count": 282,
                "categories": ["research", "technical"],
                "last_write": "2026-08-06T20:53:29+00:00",
            },
        )
    )

    summary = await adapter.summarize()

    assert summary.page_count == 568
    assert summary.source_count == 282
    assert summary.categories == ["research", "technical"]
    assert summary.last_write is not None


@pytest.mark.asyncio
@respx.mock
async def test_summarize_tolerates_empty_timestamps(adapter: HttpMimirAdapter) -> None:
    """A never-written mount reports an empty string, not null."""
    respx.get("http://mimir.test/mimir/summary").mock(
        return_value=Response(
            200,
            json={
                "page_count": 0,
                "source_count": 0,
                "categories": [],
                "last_write": "",
            },
        )
    )

    summary = await adapter.summarize()

    assert summary.last_write is None


@pytest.mark.asyncio
@respx.mock
async def test_summarize_falls_back_when_the_service_predates_the_endpoint(
    adapter: HttpMimirAdapter,
) -> None:
    """Rollout is not atomic — an older Mímir must still be summarisable."""
    now = datetime.now(UTC).isoformat()
    respx.get("http://mimir.test/mimir/summary").mock(return_value=Response(404))
    respx.get("http://mimir.test/mimir/pages").mock(
        return_value=Response(
            200,
            json=[
                {
                    "path": "technical/test.md",
                    "title": "Test",
                    "summary": "",
                    "category": "technical",
                    "updated_at": now,
                    "source_ids": [],
                }
            ],
        )
    )
    respx.get("http://mimir.test/mimir/sources").mock(return_value=Response(200, json=[]))

    summary = await adapter.summarize()

    assert summary.page_count == 1
    assert summary.source_count == 0
    assert summary.categories == ["technical"]


@pytest.mark.asyncio
@respx.mock
async def test_read_source_excerpt_asks_the_service_to_bound_it(
    adapter: HttpMimirAdapter,
) -> None:
    route = respx.get("http://mimir.test/mimir/source").mock(
        return_value=Response(
            200,
            json={
                "source_id": "src_abc123",
                "title": "Test",
                "content": "x" * 100,
                "source_type": "document",
                "content_hash": "abc",
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        )
    )

    source = await adapter.read_source_excerpt("src_abc123", 100)

    assert source is not None
    assert len(source.content) == 100
    assert route.calls.last.request.url.params["max_chars"] == "100"


@pytest.mark.asyncio
@respx.mock
async def test_read_source_excerpt_bounds_locally_if_the_service_ignores_it(
    adapter: HttpMimirAdapter,
) -> None:
    """An older service returns the full blob — the caller's bound still holds."""
    respx.get("http://mimir.test/mimir/source").mock(
        return_value=Response(
            200,
            json={
                "source_id": "src_abc123",
                "title": "Test",
                "content": "x" * 5_000,
                "source_type": "document",
                "content_hash": "abc",
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        )
    )

    source = await adapter.read_source_excerpt("src_abc123", 100)

    assert source is not None
    assert len(source.content) == 100


@pytest.mark.asyncio
@respx.mock
async def test_read_source_does_not_send_max_chars(adapter: HttpMimirAdapter) -> None:
    route = respx.get("http://mimir.test/mimir/source").mock(
        return_value=Response(
            200,
            json={
                "source_id": "src_abc123",
                "title": "Test",
                "content": "full",
                "source_type": "document",
                "content_hash": "abc",
                "ingested_at": datetime.now(UTC).isoformat(),
            },
        )
    )

    await adapter.read_source("src_abc123")

    assert "max_chars" not in route.calls.last.request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_read_source_excerpt_of_a_missing_source(adapter: HttpMimirAdapter) -> None:
    respx.get("http://mimir.test/mimir/source").mock(return_value=Response(404))

    assert await adapter.read_source_excerpt("src_missing", 100) is None
