"""Unit tests for MimirRouter's HTTP endpoints.

Tests use an in-memory MimirPort and the HTTPX test client, so no network is
involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.adapters.identity import (
    AllowAllHeaderAuthenticationAdapter,
    EnvoyHeaderAuthenticationAdapter,
)
from mimir.adapters.composite import CompositeMimirAdapter
from mimir.domain import MimirMount, WriteRouting
from mimir.registry import MimirRegistryStore
from mimir.router import MimirRouter
from niuu.domain.mimir import compute_source_id
from tests.test_mimir.unit.fakes import InMemoryMimirAdapter

#: Headers satisfying both _require_write_auth (tenant + WRITE_ROLES) and
#: _require_deploy_auth for the default EnvoyHeaderAuthenticationAdapter fixture
#: these tests use — these tests exercise CRUD/search/ranking logic, not auth,
#: so they get a fixed admin identity rather than testing auth per call.
_ADMIN_HEADERS = {
    "x-auth-user-id": "test-user",
    "x-auth-tenant": "default",
    "x-auth-roles": "volundr:admin",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _make_composite_app(tmp_path: Path) -> FastAPI:
    local = InMemoryMimirAdapter()
    shared = InMemoryMimirAdapter()
    adapter = CompositeMimirAdapter(
        mounts=[
            MimirMount(name="local", port=local, role="local", read_priority=0),
            MimirMount(name="shared", port=shared, role="shared", read_priority=1),
        ],
        write_routing=WriteRouting(
            rules=[
                ("self/", ["local"]),
                ("projects/", ["shared"]),
            ],
            default=["local"],
        ),
    )
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _make_registry_app(tmp_path: Path) -> FastAPI:
    adapter = InMemoryMimirAdapter()
    registry_store = MimirRegistryStore(tmp_path / "mimir" / ".mimir-registry.json")
    router = MimirRouter(
        adapter=adapter,
        name="test",
        role="local",
        registry_store=registry_store,
        auth=EnvoyHeaderAuthenticationAdapter(),
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _make_none_mode_registry_app(tmp_path: Path) -> FastAPI:
    """A registry app under auth_mode: none — the only mode a *local*,

    host-path registry mount can be created in: _request_scope forces
    tenant "" there (the unrestricted host operator), while a real identity
    (envoy/oidc) always carries some tenant once it satisfies
    _require_write_auth's write-role requirement, and a tenant-scoped
    caller is correctly refused a local mount by _validate_registry_write.
    """
    adapter = InMemoryMimirAdapter()
    registry_store = MimirRegistryStore(tmp_path / "mimir" / ".mimir-registry.json")
    router = MimirRouter(
        adapter=adapter,
        name="test",
        role="local",
        registry_store=registry_store,
        auth=AllowAllHeaderAuthenticationAdapter(),
        auth_mode="none",
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(_make_app(tmp_path), headers=_ADMIN_HEADERS)


@pytest.fixture()
def client_with_page(tmp_path: Path) -> TestClient:
    """Build a TestClient pre-populated with one wiki page."""
    adapter = InMemoryMimirAdapter()
    # Populate via the HTTP client itself — avoids direct asyncio.run()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app, headers=_ADMIN_HEADERS)

    # Ingest a source and write a page via the API
    tc.post(
        "/mimir/ingest",
        json={
            "title": "Test Source",
            "content": "Hello world.",
            "source_type": "document",
        },
    )
    tc.put(
        "/mimir/page",
        json={
            "path": "technical/test.md",
            "content": (
                "# Test Page\n"
                "This is a test page about ravn and tools.\n"
                "<!-- sources: src_test1 -->"
            ),
        },
    )
    return tc


@pytest.fixture()
def client_with_sourced_page(tmp_path: Path) -> TestClient:
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app, headers=_ADMIN_HEADERS)

    ingest = tc.post(
        "/mimir/ingest",
        json={
            "title": "Architecture Source",
            "content": "Shared source content about Mimir architecture.",
            "source_type": "document",
        },
    )
    source_id = ingest.json()["source_id"]
    tc.put(
        "/mimir/page",
        json={
            "path": "entities/org/niuu.md",
            "content": (f"# Niuu\nPlatform knowledge graph.\n<!-- sources: {source_id} -->"),
        },
    )
    return tc


@pytest.fixture()
def client_with_compiled_truth_page(tmp_path: Path) -> TestClient:
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app, headers=_ADMIN_HEADERS)

    ingest = tc.post(
        "/mimir/ingest",
        json={
            "title": "Postmortem Source",
            "content": "Shared postmortem source content.",
            "source_type": "document",
        },
    )
    source_id = ingest.json()["source_id"]
    tc.put(
        "/mimir/page",
        json={
            "path": "runs/NIU-912-postmortem.md",
            "content": (
                "---\n"
                "type: topic\n"
                "confidence: medium\n"
                "related_entities: [project-volundr]\n"
                f"source_ids: [{source_id}]\n"
                "---\n\n"
                "# NIU-912 Postmortem\n\n"
                "Curated run summary.\n\n"
                "## Compiled Truth\n\n"
                "### Key Facts\n"
                "- Step 1 proof artifact was present.\n"
                "- Step 2 curator proof artifact was created.\n\n"
                "### Relationships\n"
                "- [[project-volundr]] — the workflow ran inside the Volundr stack.\n\n"
                "### Assessment\n"
                "The staged workflow completed cleanly and produced the expected proof.\n\n"
                "## Timeline\n\n"
                "- 2026-05-10: Curated the NIU-912 postmortem. "
                "[Source: tester, local, 2026-05-10]\n"
            ),
        },
    )
    return tc


@pytest.fixture()
def composite_client(tmp_path: Path) -> TestClient:
    tc = TestClient(_make_composite_app(tmp_path), headers=_ADMIN_HEADERS)
    tc.put(
        "/mimir/page",
        json={"path": "self/notes/local.md", "content": "# Local\nPersonal note."},
    )
    tc.put(
        "/mimir/page",
        json={"path": "projects/roadmap/shared.md", "content": "# Shared\nPlatform roadmap."},
    )
    return tc


@pytest.fixture()
def registry_client(tmp_path: Path) -> TestClient:
    return TestClient(_make_registry_app(tmp_path), headers=_ADMIN_HEADERS)


# ---------------------------------------------------------------------------
# GET /mimir/stats
# ---------------------------------------------------------------------------


def test_stats_empty_wiki(client: TestClient) -> None:
    resp = client.get("/mimir/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] == 0
    assert data["healthy"] is True
    assert data["categories"] == []


def test_stats_with_page(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] == 1
    assert "technical" in data["categories"]


# ---------------------------------------------------------------------------
# GET /mimir/summary
# ---------------------------------------------------------------------------


def test_summary_reports_counts_in_one_call(client_with_page: TestClient) -> None:
    """Remote mounts summarise in one call instead of shipping the whole corpus."""
    resp = client_with_page.get("/mimir/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] == 1
    assert data["source_count"] == 1
    assert "technical" in data["categories"]
    assert data["last_write"] != ""


def test_summary_of_empty_wiki(client: TestClient) -> None:
    resp = client.get("/mimir/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["page_count"] == 0
    assert data["source_count"] == 0
    assert data["last_write"] == ""


def test_summary_rejects_an_unknown_mount(client: TestClient) -> None:
    assert client.get("/mimir/summary", params={"mount": "missing"}).status_code == 404


# ---------------------------------------------------------------------------
# GET /mimir/source — bounded reads
# ---------------------------------------------------------------------------


def test_read_source_returns_full_content_by_default(client: TestClient) -> None:
    ingest = client.post(
        "/mimir/ingest",
        json={"title": "Big Source", "content": "x" * 5_000, "source_type": "document"},
    )
    source_id = ingest.json()["source_id"]

    resp = client.get("/mimir/source", params={"source_id": source_id})

    assert resp.status_code == 200
    assert len(resp.json()["content"]) == 5_000


def test_read_source_bounds_content_to_max_chars(client: TestClient) -> None:
    """A caller that reads a prefix should not make the service ship megabytes."""
    ingest = client.post(
        "/mimir/ingest",
        json={"title": "Big Source", "content": "x" * 5_000, "source_type": "document"},
    )
    source_id = ingest.json()["source_id"]

    resp = client.get("/mimir/source", params={"source_id": source_id, "max_chars": 100})

    assert resp.status_code == 200
    assert len(resp.json()["content"]) == 100


def test_read_source_max_chars_above_length_is_a_no_op(client: TestClient) -> None:
    ingest = client.post(
        "/mimir/ingest",
        json={"title": "Small Source", "content": "hello", "source_type": "document"},
    )
    source_id = ingest.json()["source_id"]

    resp = client.get("/mimir/source", params={"source_id": source_id, "max_chars": 10_000})

    assert resp.json()["content"] == "hello"


def test_read_source_rejects_a_non_positive_max_chars(client: TestClient) -> None:
    resp = client.get("/mimir/source", params={"source_id": "src_x", "max_chars": 0})

    assert resp.status_code == 422


def test_read_source_bounded_still_404s_for_a_missing_source(client: TestClient) -> None:
    resp = client.get("/mimir/source", params={"source_id": "src_nope", "max_chars": 10})

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /mimir/sources — metadata only
# ---------------------------------------------------------------------------


def test_sources_listing_does_not_carry_bodies(client: TestClient) -> None:
    """Building the listing from bodies OOM-killed the shared mount."""
    client.post(
        "/mimir/ingest",
        json={"title": "Big", "content": "x" * 200_000, "source_type": "document"},
    )

    resp = client.get("/mimir/sources")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["content"] is None
    assert body[0]["title"] == "Big"


def test_sources_listing_still_classifies_origin_without_bodies(client: TestClient) -> None:
    """origin_type comes from metadata now — it used to require a full read."""
    client.post(
        "/mimir/ingest",
        json={
            "title": "From the web",
            "content": "hello",
            "source_type": "web",
            "origin_url": "https://example.com/a",
        },
    )
    client.post(
        "/mimir/ingest",
        json={"title": "From a file", "content": "hello there", "source_type": "document"},
    )

    web = client.get("/mimir/sources", params={"origin_type": "web"}).json()
    files = client.get("/mimir/sources", params={"origin_type": "file"}).json()

    assert [s["title"] for s in web] == ["From the web"]
    assert [s["origin_url"] for s in web] == ["https://example.com/a"]
    assert [s["title"] for s in files] == ["From a file"]


# ---------------------------------------------------------------------------
# POST /mimir/ingest — operational sources are refused
# ---------------------------------------------------------------------------


def test_ingest_refuses_operational_source_types(client: TestClient) -> None:
    """Exhaust can never be synthesised, so it would sit unprocessed forever."""
    resp = client.post(
        "/mimir/ingest",
        json={
            "title": "Mimir health small ingest probe",
            "content": "ok",
            "source_type": "diagnostic",
        },
    )

    assert resp.status_code == 422
    assert "knowledge" in resp.json()["detail"]
    assert client.get("/mimir/sources").json() == []


def test_ingest_refuses_tool_output(client: TestClient) -> None:
    resp = client.post(
        "/mimir/ingest",
        json={
            "title": "Dream cycle 2026-06-18T08:15",
            "content": "ran",
            "source_type": "tool_output",
        },
    )

    assert resp.status_code == 422


def test_ingest_still_accepts_knowledge_bearing_types(client: TestClient) -> None:
    """The gate names exhaust explicitly — it must not police vocabulary.

    Agents legitimately invent types (pdf, reference, source) for real material;
    a UV LED datasheet and a firmware file are knowledge whatever they are called.
    """
    for source_type in ("web", "document", "research", "pdf", "reference"):
        resp = client.post(
            "/mimir/ingest",
            json={
                "title": f"A {source_type}",
                "content": f"content for {source_type}",
                "source_type": source_type,
            },
        )
        assert resp.status_code == 200, source_type


# ---------------------------------------------------------------------------
# GET /mimir/pages
# ---------------------------------------------------------------------------


def test_list_pages_empty(client: TestClient) -> None:
    resp = client.get("/mimir/pages")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_pages_returns_page(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/pages")
    assert resp.status_code == 200
    pages = resp.json()
    assert len(pages) == 1
    assert pages[0]["path"] == "technical/test.md"
    assert pages[0]["title"] == "Test Page"


def test_list_pages_category_filter(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/pages", params={"category": "technical"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    resp2 = client_with_page.get("/mimir/pages", params={"category": "research"})
    assert resp2.status_code == 200
    assert len(resp2.json()) == 0


def test_list_pages_prefix_filter() -> None:
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    client = TestClient(app, headers=_ADMIN_HEADERS)

    client.put(
        "/mimir/page",
        json={
            "path": "research/campaigns/alpha/final.md",
            "content": "# Alpha\n",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "research/campaigns/beta/final.md",
            "content": "# Beta\n",
        },
    )

    resp = client.get("/mimir/pages", params={"prefix": "research/campaigns/alpha/"})
    assert resp.status_code == 200
    assert [page["path"] for page in resp.json()] == ["research/campaigns/alpha/final.md"]


def test_registry_mount_crud(registry_client: TestClient) -> None:
    create = registry_client.post(
        "/mimir/registry/mounts",
        json={
            "name": "shared-kb",
            "kind": "remote",
            "lifecycle": "registered",
            "role": "shared",
            "url": "https://kb.example.test",
            "path": "",
            "categories": ["arch", "api"],
            "default_read_priority": 5,
            "enabled": True,
            "health_status": "unknown",
            "health_message": "",
            "desc": "Shared KB",
        },
    )
    assert create.status_code == 200
    created = create.json()
    assert created["name"] == "shared-kb"

    listed = registry_client.get("/mimir/registry/mounts")
    assert listed.status_code == 200
    assert any(item["id"] == created["id"] for item in listed.json())

    update = registry_client.put(
        f"/mimir/registry/mounts/{created['id']}",
        json={
            "name": "shared-kb",
            "kind": "remote",
            "lifecycle": "registered",
            "role": "shared",
            "url": "https://kb.example.test",
            "path": "",
            "categories": ["arch"],
            "default_read_priority": 3,
            "enabled": True,
            "health_status": "healthy",
            "health_message": "reachable",
            "desc": "Shared KB updated",
        },
    )
    assert update.status_code == 200
    updated = update.json()
    assert updated["default_read_priority"] == 3
    assert updated["health_status"] == "healthy"

    delete = registry_client.delete(f"/mimir/registry/mounts/{created['id']}")
    assert delete.status_code == 204
    remaining = registry_client.get("/mimir/registry/mounts")
    assert all(item["id"] != created["id"] for item in remaining.json())


# ---------------------------------------------------------------------------
# GET /mimir/page
# ---------------------------------------------------------------------------


def test_read_page_found(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/page", params={"path": "technical/test.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert "Test Page" in data["content"]
    assert data["path"] == "technical/test.md"


def test_read_page_returns_explicit_zones(client_with_compiled_truth_page: TestClient) -> None:
    resp = client_with_compiled_truth_page.get(
        "/mimir/page",
        params={"path": "runs/NIU-912-postmortem.md"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["related"] == ["project-volundr"]
    assert data["zones"] == [
        {
            "kind": "key-facts",
            "items": [
                "Step 1 proof artifact was present.",
                "Step 2 curator proof artifact was created.",
            ],
        },
        {
            "kind": "relationships",
            "items": [
                {
                    "slug": "project-volundr",
                    "note": "the workflow ran inside the Volundr stack.",
                }
            ],
        },
        {
            "kind": "assessment",
            "text": "The staged workflow completed cleanly and produced the expected proof.",
        },
        {
            "kind": "timeline",
            "items": [
                {
                    "date": "2026-05-10",
                    "note": "Curated the NIU-912 postmortem",
                    "source": "tester, local, 2026-05-10",
                }
            ],
        },
    ]


def test_read_page_falls_back_to_assessment_for_legacy_compiled_truth() -> None:
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app, headers=_ADMIN_HEADERS)

    tc.put(
        "/mimir/page",
        json={
            "path": "runs/legacy-postmortem.md",
            "content": (
                "# Legacy Postmortem\n\n"
                "## Compiled Truth\n\n"
                "**Outcome**: Complete.\n\n"
                "### What was done\n\n"
                "- Verified the step-1 artifact.\n"
                "- Created the step-2 artifact.\n"
            ),
        },
    )

    resp = tc.get("/mimir/page", params={"path": "runs/legacy-postmortem.md"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["zones"] == [
        {
            "kind": "assessment",
            "text": (
                "**Outcome**: Complete.\n\n"
                "### What was done\n\n"
                "- Verified the step-1 artifact.\n"
                "- Created the step-2 artifact."
            ),
        }
    ]


def test_read_page_not_found(client: TestClient) -> None:
    resp = client.get("/mimir/page", params={"path": "technical/missing.md"})
    assert resp.status_code == 404


def test_ingest_source_respects_requested_mount(composite_client: TestClient) -> None:
    resp = composite_client.post(
        "/mimir/ingest",
        json={
            "title": "Shared Source",
            "content": "Shared knowledge source.",
            "source_type": "document",
            "mount": "shared",
        },
    )
    assert resp.status_code == 200

    shared_sources = composite_client.get("/mimir/sources", params={"mount": "shared"})
    local_sources = composite_client.get("/mimir/sources", params={"mount": "local"})

    shared_titles = {item["title"] for item in shared_sources.json()}
    local_titles = {item["title"] for item in local_sources.json()}
    assert "Shared Source" in shared_titles
    assert "Shared Source" not in local_titles


# ---------------------------------------------------------------------------
# GET /mimir/search
# ---------------------------------------------------------------------------


def test_search_finds_page(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/search", params={"q": "ravn tools"})
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) >= 1
    assert results[0]["path"] == "technical/test.md"


def test_search_names_the_single_base_mount(client_with_page: TestClient) -> None:
    results = client_with_page.get("/mimir/search", params={"q": "ravn tools"}).json()
    assert {r["mount"] for r in results} == {"test"}


def test_search_names_the_mount_each_result_came_from(composite_client: TestClient) -> None:
    composite_client.put(
        "/mimir/page",
        json={"path": "self/notes.md", "content": "# Notes\n\nFjolnir keeps the gateway."},
    )
    composite_client.put(
        "/mimir/page",
        json={"path": "projects/gw.md", "content": "# Gateway\n\nFjolnir routes the gateway."},
    )
    results = composite_client.get("/mimir/search", params={"q": "gateway"}).json()
    assert {r["path"]: r["mount"] for r in results} == {
        "self/notes.md": "local",
        "projects/gw.md": "shared",
    }
    scoped = composite_client.get("/mimir/search", params={"q": "gateway", "mount": "shared"})
    assert [(r["path"], r["mount"]) for r in scoped.json()] == [("projects/gw.md", "shared")]


def test_search_no_results(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/search", params={"q": "kanuck valley models"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /mimir/graph
# ---------------------------------------------------------------------------


def test_graph_empty(client: TestClient) -> None:
    resp = client.get("/mimir/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert data["nodes"] == []
    assert data["edges"] == []


def test_graph_has_nodes(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/graph")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["path"] == "technical/test.md"
    assert data["nodes"][0]["mount"] == "test"
    assert data["nodes"][0]["category"] == "technical"


def test_graph_node_without_timeline_or_confidence(client_with_page: TestClient) -> None:
    """No frontmatter confidence and no Timeline zone: first_seen == updated_at,

    confidence is null (never invented, e.g. never defaulted to "medium")."""
    resp = client_with_page.get("/mimir/graph")
    node = resp.json()["nodes"][0]
    assert node["confidence"] is None
    assert node["updated_at"] != ""
    assert node["first_seen"] == node["updated_at"]


def test_graph_node_first_seen_from_older_timeline_entry(client: TestClient) -> None:
    """first_seen is the earliest dated Timeline entry when older than updated_at,

    and confidence reflects the page's frontmatter value."""
    content = (
        "---\n"
        "confidence: high\n"
        "---\n"
        "# Old Page\n"
        "A page with history.\n\n"
        "## Compiled Truth\n\n"
        "Some settled fact.\n\n"
        "## Timeline\n\n"
        "- 2020-01-01: The earliest thing happened. [Source: test]\n"
        "- 2020-06-15: A later thing happened. [Source: test]\n"
    )
    put = client.put("/mimir/page", json={"path": "technical/old.md", "content": content})
    assert put.status_code == 204

    node = client.get("/mimir/graph").json()["nodes"][0]
    assert node["confidence"] == "high"
    assert node["first_seen"] == "2020-01-01T00:00:00+00:00"
    assert node["first_seen"] != node["updated_at"]


def test_graph_node_confidence_low(client: TestClient) -> None:
    content = "---\nconfidence: low\n---\n# Shaky\nNot much evidence yet.\n"
    client.put("/mimir/page", json={"path": "technical/shaky.md", "content": content})
    node = client.get("/mimir/graph").json()["nodes"][0]
    assert node["confidence"] == "low"


# ---------------------------------------------------------------------------
# PUT /mimir/page
# ---------------------------------------------------------------------------


def test_upsert_page(client: TestClient) -> None:
    payload = {"path": "technical/new.md", "content": "# New\nSome content."}
    resp = client.put("/mimir/page", json=payload)
    assert resp.status_code == 204

    # Verify it was written
    resp2 = client.get("/mimir/pages")
    paths = [p["path"] for p in resp2.json()]
    assert "technical/new.md" in paths


# ---------------------------------------------------------------------------
# POST /mimir/ingest
# ---------------------------------------------------------------------------


def test_ingest_source(client: TestClient) -> None:
    payload = {
        "title": "Test Doc",
        "content": "Some raw content about ODIN.",
        "source_type": "document",
    }
    resp = client.post("/mimir/ingest", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["source_id"] == compute_source_id(payload["content"])


def test_mounts_for_composite_adapter(composite_client: TestClient) -> None:
    mounts = composite_client.get("/mimir/mounts")
    assert mounts.status_code == 200
    data = mounts.json()
    assert [mount["name"] for mount in data] == ["local", "shared"]
    assert {mount["pages"] for mount in data} == {1}


def test_recent_writes_and_activity_include_real_events(composite_client: TestClient) -> None:
    writes = composite_client.get("/mimir/mounts/recent-writes", params={"limit": 10})
    assert writes.status_code == 200
    kinds = {entry["kind"] for entry in writes.json()}
    assert "write" in kinds

    activity = composite_client.get("/mimir/activity", params={"limit": 10})
    assert activity.status_code == 200
    events = activity.json()
    assert any(event["kind"] == "write" for event in events)
    assert any(event["page"] == "projects/roadmap/shared.md" for event in events)


# ---------------------------------------------------------------------------
# GET /mimir/activity/live
# ---------------------------------------------------------------------------


def test_live_activity_records_read_with_verified_actor(client: TestClient) -> None:
    client.put(
        "/mimir/page",
        json={"path": "technical/live.md", "content": "# Live\nSome content."},
    )
    client.get("/mimir/page", params={"path": "technical/live.md"})

    events = client.get("/mimir/activity/live").json()
    reads = [e for e in events if e["kind"] == "read" and e["path"] == "technical/live.md"]
    assert len(reads) == 1
    assert reads[0]["actor"] == "test-user"  # from _ADMIN_HEADERS x-auth-user-id
    assert reads[0]["mount"] == "test"

    writes = [e for e in events if e["kind"] == "write" and e["path"] == "technical/live.md"]
    assert len(writes) == 1
    assert writes[0]["actor"] == "test-user"

    # Newest first.
    assert events[0]["timestamp"] >= events[-1]["timestamp"]


def test_live_activity_failed_read_is_not_recorded(client: TestClient) -> None:
    resp = client.get("/mimir/page", params={"path": "technical/does-not-exist.md"})
    assert resp.status_code == 404

    events = client.get("/mimir/activity/live").json()
    assert events == []


def test_live_activity_delete_is_recorded_as_write(client: TestClient) -> None:
    client.put(
        "/mimir/page",
        json={"path": "technical/deleteme.md", "content": "# Bye\nTemporary."},
    )
    resp = client.delete("/mimir/page", params={"path": "technical/deleteme.md"})
    assert resp.status_code == 204

    events = client.get("/mimir/activity/live").json()
    deletes = [e for e in events if e["path"] == "technical/deleteme.md" and e["kind"] == "write"]
    # One write from the PUT, one write from the DELETE.
    assert len(deletes) == 2


def test_live_activity_failed_delete_is_not_recorded(client: TestClient) -> None:
    resp = client.delete("/mimir/page", params={"path": "technical/never-existed.md"})
    assert resp.status_code == 404

    events = client.get("/mimir/activity/live").json()
    assert events == []


def test_live_activity_actor_null_when_unauthenticated(tmp_path: Path) -> None:
    """EnvoyHeaderAuthenticationAdapter with no x-auth-user-id header raises

    InvalidTokenError internally; _verified_principal turns that into None,
    so actor is null (auth_mode: envoy does not gate GET/PUT on identity)."""
    app = _make_app(tmp_path)
    anon = TestClient(app)  # no _ADMIN_HEADERS
    anon.put(
        "/mimir/page",
        json={"path": "technical/anon.md", "content": "# Anon\nNo credential."},
    )
    anon.get("/mimir/page", params={"path": "technical/anon.md"})

    events = anon.get("/mimir/activity/live").json()
    assert events
    assert all(e["actor"] is None for e in events)


def test_live_activity_allow_all_adapter_always_attributes_dev_user(tmp_path: Path) -> None:
    """AllowAllHeaderAuthenticationAdapter always asserts a fixed principal —

    actor is that adapter's user_id, never null, even with no credential."""
    app = _make_none_mode_registry_app(tmp_path)
    anon = TestClient(app)
    anon.put(
        "/mimir/page",
        json={"path": "technical/dev.md", "content": "# Dev\nLocal dev write."},
    )
    events = anon.get("/mimir/activity/live").json()
    assert events
    assert events[0]["actor"] == "dev-user"


def test_live_activity_since_excludes_older_events(client: TestClient) -> None:
    client.put(
        "/mimir/page",
        json={"path": "technical/first.md", "content": "# First\nBefore the cutoff."},
    )
    cutoff = client.get("/mimir/activity/live").json()[0]["timestamp"]
    client.put(
        "/mimir/page",
        json={"path": "technical/second.md", "content": "# Second\nAfter the cutoff."},
    )

    events = client.get("/mimir/activity/live", params={"since": cutoff}).json()
    paths = {e["path"] for e in events}
    assert "technical/second.md" in paths
    assert "technical/first.md" not in paths


def test_live_activity_malformed_since_is_422(client: TestClient) -> None:
    resp = client.get("/mimir/activity/live", params={"since": "not-a-timestamp"})
    assert resp.status_code == 422


def test_entities_and_page_sources_are_available(client_with_sourced_page: TestClient) -> None:
    entities = client_with_sourced_page.get("/mimir/entities")
    assert entities.status_code == 200
    assert entities.json() == [
        {
            "path": "entities/org/niuu.md",
            "title": "Niuu",
            "entity_kind": "org",
            "summary": "Platform knowledge graph.",
            "relationship_count": 0,
        }
    ]

    page = client_with_sourced_page.get("/mimir/page", params={"path": "entities/org/niuu.md"})
    source_id = page.json()["source_ids"][0]
    sources = client_with_sourced_page.get(
        "/mimir/page/sources",
        params={"path": "entities/org/niuu.md"},
    )
    assert sources.status_code == 200
    assert sources.json()[0]["source_id"] == source_id
    assert sources.json()[0]["content"].startswith("Shared source content")


def test_embedding_search_falls_back_to_page_search(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/embeddings/search", params={"q": "ravn", "top_k": 5})
    assert resp.status_code == 200
    result = resp.json()[0]
    assert result["path"] == "technical/test.md"
    assert result["mount_name"] == "test"


def test_file_ingest_endpoint_returns_source_shape(client: TestClient) -> None:
    resp = client.post(
        "/mimir/sources/ingest/file",
        files={"file": ("notes.md", b"# Notes\nUploaded content", "text/markdown")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "notes.md"
    assert data["source_id"].startswith("src_")
    assert data["origin_type"] == "file"


def test_url_ingest_endpoint_fetches_and_ingests(client: TestClient, respx_mock) -> None:
    respx_mock.get("https://example.com/mimir").mock(
        return_value=httpx.Response(
            200,
            text="<html><head><title>Mimir Doc</title></head><body>Hello world</body></html>",
        )
    )

    resp = client.post("/mimir/sources/ingest/url", json={"url": "https://example.com/mimir"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Mimir Doc"
    assert data["origin_url"] == "https://example.com/mimir"


def test_url_ingest_rejects_private_hosts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "mimir.router.check_ssrf",
        lambda hostname: f"Blocked: '{hostname}' resolves to a private/reserved address",
    )

    resp = client.post("/mimir/sources/ingest/url", json={"url": "http://127.0.0.1/secret"})

    assert resp.status_code == 400
    assert "private/reserved" in resp.json()["detail"]


def test_url_ingest_rejects_unsupported_schemes(client: TestClient) -> None:
    resp = client.post("/mimir/sources/ingest/url", json={"url": "file:///tmp/secret.txt"})

    assert resp.status_code == 400
    assert "Unsupported URL scheme" in resp.json()["detail"]


def test_url_ingest_rejects_fragments(client: TestClient) -> None:
    resp = client.post("/mimir/sources/ingest/url", json={"url": "https://example.com/doc#frag"})

    assert resp.status_code == 400
    assert "fragments are not supported" in resp.json()["detail"]


def test_url_ingest_rejects_embedded_credentials(client: TestClient) -> None:
    resp = client.post(
        "/mimir/sources/ingest/url",
        json={"url": "https://user:pass@example.com/private"},
    )

    assert resp.status_code == 400
    assert "embedded credentials" in resp.json()["detail"]


def test_url_ingest_rejects_path_traversal(client: TestClient) -> None:
    resp = client.post("/mimir/sources/ingest/url", json={"url": "https://example.com/a/../b"})

    assert resp.status_code == 400
    assert "Invalid URL path" in resp.json()["detail"]


def test_unknown_mount_returns_404_for_single_and_composite(
    client: TestClient,
    composite_client: TestClient,
) -> None:
    assert client.get("/mimir/stats", params={"mount": "missing"}).status_code == 404
    assert composite_client.get("/mimir/stats", params={"mount": "missing"}).status_code == 404


def test_sources_unprocessed_excludes_operational_exhaust() -> None:
    """The API's backlog definition must match the adapter's: exhaust stored
    before the ingest gate existed is never pending synthesis."""
    from datetime import UTC, datetime

    from niuu.domain.mimir import MimirSource, compute_content_hash

    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app, headers=_ADMIN_HEADERS)

    tc.post(
        "/mimir/ingest",
        json={"title": "Real", "content": "knowledge", "source_type": "document"},
    )
    # Ingest refuses operational types now, so plant one the way it actually
    # got there: written before the gate existed.
    asyncio.run(
        adapter.ingest(
            MimirSource(
                source_id="src-tool",
                title="Probe output",
                content="exit 0",
                source_type="tool_output",
                ingested_at=datetime.now(UTC),
                content_hash=compute_content_hash("exit 0"),
            )
        )
    )

    unprocessed = tc.get("/mimir/sources", params={"unprocessed": True})
    assert unprocessed.status_code == 200
    assert [s["source_type"] for s in unprocessed.json()] == ["document"]

    everything = tc.get("/mimir/sources")
    assert len(everything.json()) == 2


def test_sources_filters_unprocessed_and_missing_page_sources(
    client_with_sourced_page: TestClient,
) -> None:
    extra = client_with_sourced_page.post(
        "/mimir/ingest",
        json={
            "title": "Loose Source",
            "content": "Not yet referenced by any page.",
            "source_type": "document",
        },
    )
    loose_source_id = extra.json()["source_id"]

    all_sources = client_with_sourced_page.get("/mimir/sources")
    assert all_sources.status_code == 200
    assert any(source["source_id"] == loose_source_id for source in all_sources.json())
    # A listing carries metadata only. Loading every body to build it held the
    # whole corpus in memory and OOM-killed the service; fetch /mimir/source for
    # content.
    assert all(source["content"] is None for source in all_sources.json())

    file_sources = client_with_sourced_page.get("/mimir/sources", params={"origin_type": "file"})
    assert file_sources.status_code == 200
    assert len(file_sources.json()) >= 2

    web_sources = client_with_sourced_page.get("/mimir/sources", params={"origin_type": "web"})
    assert web_sources.status_code == 200
    assert web_sources.json() == []

    unprocessed = client_with_sourced_page.get("/mimir/sources", params={"unprocessed": True})
    assert unprocessed.status_code == 200
    assert [source["source_id"] for source in unprocessed.json()] == [loose_source_id]

    missing_page = client_with_sourced_page.get(
        "/mimir/page/sources",
        params={"path": "entities/org/missing.md"},
    )
    assert missing_page.status_code == 404


def test_page_sources_skips_unknown_source_ids(client: TestClient) -> None:
    client.put(
        "/mimir/page",
        json={
            "path": "technical/orphan-source.md",
            "content": "# Orphan\nUnknown source link.\n<!-- sources: src_missing -->",
        },
    )

    resp = client.get("/mimir/page/sources", params={"path": "technical/orphan-source.md"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_graph_edges_entity_filters_and_type_inference(client: TestClient) -> None:
    ingest = client.post(
        "/mimir/ingest",
        json={
            "title": "Directive Source",
            "content": "Directive and preference source content.",
            "source_type": "document",
        },
    )
    source_id = ingest.json()["source_id"]
    client.put(
        "/mimir/page",
        json={
            "path": "policies/preferences/team.md",
            "content": f"# Team Preference\nPreference summary.\n<!-- sources: {source_id} -->",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "policies/directives/style.md",
            "content": f"# Style Directive\nDirective summary.\n<!-- sources: {source_id} -->",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "entities/people/alice.md",
            "content": "# Alice\nPerson summary.",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "entities/project/odin.md",
            "content": "# Odin\nProject summary.",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "entities/component/gateway.md",
            "content": "# Gateway\nComponent summary.",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "entities/tech/postgres.md",
            "content": "# Postgres\nTechnology summary.",
        },
    )
    client.put(
        "/mimir/page",
        json={
            "path": "entities/misc/idea.md",
            "content": "# Idea\nConcept summary.",
        },
    )

    pages = client.get("/mimir/pages").json()
    assert any(page["type"] == "preference" for page in pages)
    assert any(page["type"] == "directive" for page in pages)

    graph = client.get("/mimir/graph")
    assert graph.status_code == 200
    edges = graph.json()["edges"]
    assert len(edges) == 1
    edge_pair = {edges[0]["source"], edges[0]["target"]}
    paths = {node["id"]: node["path"] for node in graph.json()["nodes"]}
    assert {paths[node_id] for node_id in edge_pair} == {
        "policies/directives/style.md",
        "policies/preferences/team.md",
    }

    people = client.get("/mimir/entities", params={"kind": "person"})
    assert people.status_code == 200
    assert [entity["path"] for entity in people.json()] == ["entities/people/alice.md"]

    all_entities = client.get("/mimir/entities").json()
    kinds = {entity["entity_kind"] for entity in all_entities}
    assert {"person", "project", "component", "technology", "concept"} <= kinds


def test_mounts_support_remote_http_host_metadata(tmp_path: Path) -> None:
    app = _make_app(tmp_path / "hosted")
    from mimir.adapters.http import HttpMimirAdapter

    transport = httpx.ASGITransport(app=app)
    remote = HttpMimirAdapter(base_url="http://mimir-test")
    remote._client = httpx.AsyncClient(
        transport=transport,
        base_url="http://mimir-test",
        timeout=30.0,
    )
    adapter = CompositeMimirAdapter(
        mounts=[MimirMount(name="remote", port=remote, role="shared", read_priority=0)],
        write_routing=WriteRouting(default=["remote"]),
    )
    router = MimirRouter(
        adapter=adapter, name="test", role="local", auth=EnvoyHeaderAuthenticationAdapter()
    )
    composite_app = FastAPI()
    composite_app.include_router(router.router, prefix="/mimir")
    client = TestClient(composite_app, headers=_ADMIN_HEADERS)

    mounts = client.get("/mimir/mounts")
    assert mounts.status_code == 200
    assert mounts.json()[0]["host"] == "mimir-test"


def test_url_ingest_failure_returns_bad_gateway(client: TestClient, respx_mock) -> None:
    respx_mock.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="boom"),
    )

    resp = client.post("/mimir/sources/ingest/url", json={"url": "https://example.com/fail"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Page reads must not walk the corpus to decorate one page
# ---------------------------------------------------------------------------


def test_page_response_still_reports_its_mount(client: TestClient) -> None:
    """Scoping the lookup must not cost the answer it exists to give."""
    client.put("/mimir/page", json={"path": "research/campaigns/x/final.md", "content": "# F\nx."})

    resp = client.get("/mimir/page", params={"path": "research/campaigns/x/final.md"})

    assert resp.status_code == 200
    assert resp.json()["mounts"] == ["test"]


def test_graph_keeps_duplicate_paths_in_separate_mounts(tmp_path):
    client = TestClient(_make_composite_app(tmp_path), headers=_ADMIN_HEADERS)
    for mount in ("local", "shared"):
        assert (
            client.put(
                "/mimir/page",
                json={
                    "path": "research/comparison.md",
                    "content": "# Comparison\n\n[[result]]",
                    "mount": mount,
                },
            ).status_code
            == 204
        )
        assert (
            client.put(
                "/mimir/page",
                json={
                    "path": "research/result.md",
                    "content": "# Result",
                    "mount": mount,
                },
            ).status_code
            == 204
        )
    response = client.get("/mimir/graph")
    assert response.status_code == 200
    data = response.json()
    assert len(data["nodes"]) == 4
    assert len({node["id"] for node in data["nodes"]}) == 4
    by_id = {node["id"]: node for node in data["nodes"]}
    assert len(data["edges"]) == 2
    assert all(by_id[e["source"]]["mount"] == by_id[e["target"]]["mount"] for e in data["edges"])
    selected = client.get("/mimir/graph?mount=shared").json()
    assert len(selected["nodes"]) == 2
    assert {node["mount"] for node in selected["nodes"]} == {"shared"}


def test_graph_preserves_shared_provenance_across_mounts(tmp_path):
    client = TestClient(_make_composite_app(tmp_path), headers=_ADMIN_HEADERS)
    for mount in ("local", "shared"):
        response = client.put(
            "/mimir/page",
            json={
                "path": f"research/{mount}.md",
                "content": "# Shared evidence\n\n<!-- sources: source-paper -->",
                "mount": mount,
            },
        )
        assert response.status_code == 204
    data = client.get("/mimir/graph").json()
    assert len(data["nodes"]) == 2
    assert len(data["edges"]) == 1
    assert data["edges"][0]["type"] == "shared_source"


def test_remote_registry_connection_becomes_a_routable_mount(tmp_path):
    from mimir.adapters.http import HttpMimirAdapter
    from mimir.registry import MimirRegistryEntry

    store = MimirRegistryStore(tmp_path / "registry.json")
    entry = store.save_entry(MimirRegistryEntry(name="ymir", url="http://ymir.test"))
    router = MimirRouter(
        InMemoryMimirAdapter(),
        registry_store=store,
        auth=EnvoyHeaderAuthenticationAdapter(),
    )
    port, name = router._resolve_port("ymir")
    assert isinstance(port, HttpMimirAdapter)
    assert name == "ymir"
    assert port._base_url == "http://ymir.test"
    assert router._resolve_port("ymir")[0] is port
    store.save_entry(entry.model_copy(update={"url": "http://moved.test"}))
    assert router._resolve_port("ymir")[0]._base_url == "http://moved.test"
    store.save_entry(entry.model_copy(update={"enabled": False}))
    assert "ymir" not in {m["name"] for m in router._mount_definitions()}


def test_federation_diagnostics_read_configured_capture_directory(tmp_path):
    from mimir.adapters.composite import CompositeMimirAdapter
    from mimir.domain import MimirMount

    adapter = CompositeMimirAdapter(
        mounts=[MimirMount(name="notes", port=InMemoryMimirAdapter(), role="local")]
    )
    app = FastAPI()
    app.include_router(
        MimirRouter(
            adapter, eval_capture_dir=tmp_path / "captures", auth=EnvoyHeaderAuthenticationAdapter()
        ).router
    )
    with TestClient(app, headers=_ADMIN_HEADERS) as client:
        assert client.get("/eval/queries").status_code == 404
        assert (
            client.get("/search", params={"q": "test query", "mount": "notes"}).status_code == 200
        )
        stats = client.get("/eval/queries").json()
        assert stats["total"] == 1
        assert stats["recent"][0]["query"] == "test query"
        assert client.get("/eval/latest").status_code == 404
        (tmp_path / "captures/eval-latest.json").write_text('{"query_count": 2}')
        assert client.get("/eval/latest").json() == {"query_count": 2}


def test_registry_mounts_preserve_named_service_write_destination(tmp_path):
    from mimir.registry import MimirRegistryEntry

    store = MimirRegistryStore(tmp_path / "registry.json")
    store.save_entry(MimirRegistryEntry(name="remote", url="https://mimir.example"))
    adapter = InMemoryMimirAdapter()
    router = MimirRouter(
        adapter,
        name="shared",
        role="shared",
        registry_store=store,
        auth=EnvoyHeaderAuthenticationAdapter(),
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    with TestClient(app, headers=_ADMIN_HEADERS) as client:
        response = client.put(
            "/mimir/page",
            json={
                "path": "deliveries/test/10-implementation.md",
                "content": "# Delivery\nVerified.",
            },
        )
    assert response.status_code == 204
    assert adapter.pages["deliveries/test/10-implementation.md"][0] == "# Delivery\nVerified."
