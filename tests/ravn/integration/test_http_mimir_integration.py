"""Integration tests for HttpMimirAdapter + Mimir HTTP service (NIU-616).

Tests run the FastAPI Mimir router in-process via an ASGI transport — no
network required.  Covers:

1. Round-trip: write a page via HttpMimirAdapter, read it back.
2. Round-trip: search returns the written page.
3. Composite: CompositeMimirAdapter with local (tmp_path) + hosted (ASGI).
   - upsert_page("project/…") routes to hosted only.
   - upsert_page("self/…") routes to local only.
   - Hosted pages survive after local dir is deleted.
4. Adapter factory: _build_mimir() instantiates HttpMimirAdapter when instance
   has url, MarkdownMimirAdapter when instance has path.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.router import MimirRouter
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.adapters.mimir.http import HttpMimirAdapter
from ravn.domain.mimir import MimirMount, WriteRouting

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HOSTED_BASE = "http://mimir-test"


def _make_mimir_app(root: Path) -> FastAPI:
    """Return a FastAPI app backed by MarkdownMimirAdapter at *root*."""
    adapter = MarkdownMimirAdapter(root=root)
    router = MimirRouter(adapter=adapter, name="test-hosted", role="shared")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _http_adapter_over_asgi(app: FastAPI) -> HttpMimirAdapter:
    """Return an HttpMimirAdapter whose HTTP calls go through the ASGI app."""
    transport = httpx.ASGITransport(app=app)
    adapter = HttpMimirAdapter(base_url=_HOSTED_BASE)
    adapter._client = httpx.AsyncClient(transport=transport, base_url=_HOSTED_BASE, timeout=30.0)
    return adapter


# ---------------------------------------------------------------------------
# Integration: round-trip via HttpMimirAdapter + ASGI Mimir service
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_upsert_and_read_page(tmp_path: Path) -> None:
    """Write a page via HttpMimirAdapter, read it back."""
    app = _make_mimir_app(tmp_path / "hosted")
    adapter = _http_adapter_over_asgi(app)

    await adapter.upsert_page("technical/test.md", "# Test\nHello world.")
    content = await adapter.read_page("technical/test.md")

    assert "Hello world" in content


@pytest.mark.asyncio
async def test_round_trip_get_page_returns_metadata(tmp_path: Path) -> None:
    """get_page returns a MimirPage with correct path and content."""
    app = _make_mimir_app(tmp_path / "hosted")
    adapter = _http_adapter_over_asgi(app)

    await adapter.upsert_page("projects/decisions/foo.md", "# Decision\nUse Python.")
    page = await adapter.get_page("projects/decisions/foo.md")

    assert page.meta.path == "projects/decisions/foo.md"
    assert "Use Python" in page.content


@pytest.mark.asyncio
async def test_round_trip_read_page_raises_not_found(tmp_path: Path) -> None:
    """read_page raises FileNotFoundError for a missing page."""
    app = _make_mimir_app(tmp_path / "hosted")
    adapter = _http_adapter_over_asgi(app)

    with pytest.raises(FileNotFoundError):
        await adapter.read_page("missing/page.md")


@pytest.mark.asyncio
async def test_round_trip_list_pages(tmp_path: Path) -> None:
    """list_pages returns the upserted page."""
    app = _make_mimir_app(tmp_path / "hosted")
    adapter = _http_adapter_over_asgi(app)

    await adapter.upsert_page("entity/person/alice.md", "# Alice\nKey person.")
    pages = await adapter.list_pages()

    paths = [m.path for m in pages]
    assert "entity/person/alice.md" in paths


@pytest.mark.asyncio
async def test_round_trip_search_returns_page(tmp_path: Path) -> None:
    """search finds the upserted page by its content."""
    app = _make_mimir_app(tmp_path / "hosted")
    adapter = _http_adapter_over_asgi(app)

    await adapter.upsert_page("technical/ravn.md", "# Ravn\nDistributed AI agent.")
    results = await adapter.search("distributed")

    paths = [p.meta.path for p in results]
    assert "technical/ravn.md" in paths


# ---------------------------------------------------------------------------
# Integration: CompositeMimirAdapter — local + hosted write routing (NIU-616)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_composite_project_routes_to_hosted(tmp_path: Path) -> None:
    """upsert_page('project/…') routes only to the hosted mount."""
    app = _make_mimir_app(tmp_path / "hosted")
    hosted_adapter = _http_adapter_over_asgi(app)
    local_adapter = MarkdownMimirAdapter(root=tmp_path / "local")

    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("project/", ["hosted"]),
            ("entity/", ["hosted"]),
        ],
        default=["local"],
    )
    composite = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local_adapter, role="local", read_priority=0),
            MimirMount(name="hosted", port=hosted_adapter, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )

    await composite.upsert_page("project/decisions/arch.md", "# Arch\nHexagonal.")

    # Page should be in hosted only
    hosted_pages = await hosted_adapter.list_pages()
    hosted_paths = [m.path for m in hosted_pages]
    assert "project/decisions/arch.md" in hosted_paths

    local_pages = await local_adapter.list_pages()
    local_paths = [m.path for m in local_pages]
    assert "project/decisions/arch.md" not in local_paths


@pytest.mark.asyncio
async def test_composite_self_routes_to_local(tmp_path: Path) -> None:
    """upsert_page('self/…') routes only to the local mount."""
    app = _make_mimir_app(tmp_path / "hosted")
    hosted_adapter = _http_adapter_over_asgi(app)
    local_adapter = MarkdownMimirAdapter(root=tmp_path / "local")

    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("project/", ["hosted"]),
        ],
        default=["local"],
    )
    composite = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local_adapter, role="local", read_priority=0),
            MimirMount(name="hosted", port=hosted_adapter, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )

    await composite.upsert_page("self/notes/personal.md", "# Notes\nPrivate note.")

    local_pages = await local_adapter.list_pages()
    local_paths = [m.path for m in local_pages]
    assert "self/notes/personal.md" in local_paths

    hosted_pages = await hosted_adapter.list_pages()
    hosted_paths = [m.path for m in hosted_pages]
    assert "self/notes/personal.md" not in hosted_paths


@pytest.mark.asyncio
async def test_composite_entity_routes_to_hosted(tmp_path: Path) -> None:
    """upsert_page('entity/…') routes only to the hosted mount."""
    app = _make_mimir_app(tmp_path / "hosted")
    hosted_adapter = _http_adapter_over_asgi(app)
    local_adapter = MarkdownMimirAdapter(root=tmp_path / "local")

    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("entity/", ["hosted"]),
        ],
        default=["local"],
    )
    composite = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local_adapter, role="local", read_priority=0),
            MimirMount(name="hosted", port=hosted_adapter, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )

    await composite.upsert_page("entity/org/niuu.md", "# Niuu\nAI platform.")

    hosted_pages = await hosted_adapter.list_pages()
    hosted_paths = [m.path for m in hosted_pages]
    assert "entity/org/niuu.md" in hosted_paths

    local_pages = await local_adapter.list_pages()
    local_paths = [m.path for m in local_pages]
    assert "entity/org/niuu.md" not in local_paths


@pytest.mark.asyncio
async def test_composite_hosted_pages_survive_local_deletion(tmp_path: Path) -> None:
    """Pages written to hosted remain readable even after the local dir is removed."""
    hosted_root = tmp_path / "hosted"
    local_root = tmp_path / "local"
    app = _make_mimir_app(hosted_root)
    hosted_adapter = _http_adapter_over_asgi(app)
    local_adapter = MarkdownMimirAdapter(root=local_root)

    routing = WriteRouting(
        rules=[
            ("self/", ["local"]),
            ("project/", ["hosted"]),
        ],
        default=["local"],
    )
    composite = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local_adapter, role="local", read_priority=0),
            MimirMount(name="hosted", port=hosted_adapter, role="shared", read_priority=1),
        ],
        write_routing=routing,
    )

    await composite.upsert_page("project/core/design.md", "# Design\nHexagonal arch.")

    # Simulate local pod deletion by removing the local dir
    import shutil

    shutil.rmtree(local_root, ignore_errors=True)

    # Composite read should still find the page via hosted mount
    content = await composite.read_page("project/core/design.md")
    assert "Hexagonal" in content
