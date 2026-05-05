"""Unit tests for MimirRouter — all nine HTTP endpoints.

Tests use a real MarkdownMimirAdapter backed by a tmp_path and the HTTPX
test client, so no network is involved.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.registry import MimirRegistryStore
from mimir.router import MimirRouter
from ravn.adapters.mimir.composite import CompositeMimirAdapter
from ravn.domain.mimir import MimirMount, WriteRouting

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_app(tmp_path: Path) -> FastAPI:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _make_composite_app(tmp_path: Path) -> FastAPI:
    local = MarkdownMimirAdapter(root=tmp_path / "local")
    shared = MarkdownMimirAdapter(root=tmp_path / "shared")
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
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


def _make_registry_app(tmp_path: Path) -> FastAPI:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    registry_store = MimirRegistryStore(tmp_path / "mimir" / ".mimir-registry.json")
    router = MimirRouter(
        adapter=adapter,
        name="test",
        role="local",
        registry_store=registry_store,
    )
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return app


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(_make_app(tmp_path))


@pytest.fixture()
def client_with_page(tmp_path: Path) -> TestClient:
    """Build a TestClient pre-populated with one wiki page."""
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    # Populate via the HTTP client itself — avoids direct asyncio.run()
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app)

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
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    router = MimirRouter(adapter=adapter, name="test", role="local")
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    tc = TestClient(app)

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
def composite_client(tmp_path: Path) -> TestClient:
    tc = TestClient(_make_composite_app(tmp_path))
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
    return TestClient(_make_registry_app(tmp_path))


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


def test_search_no_results(client_with_page: TestClient) -> None:
    resp = client_with_page.get("/mimir/search", params={"q": "kanuck valley models"})
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /mimir/lint
# ---------------------------------------------------------------------------


def test_lint_empty_wiki(client: TestClient) -> None:
    resp = client.get("/mimir/lint")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pages_checked"] == 0
    assert data["issues_found"] is False
    assert data["issues"] == []
    assert "summary" in data


def test_lint_finds_issues_with_page(client_with_page: TestClient) -> None:
    # The page exists and is indexed; new structural checks (e.g. L12) may fire
    resp = client_with_page.get("/mimir/lint")
    assert resp.status_code == 200
    data = resp.json()
    assert "pages_checked" in data
    assert data["pages_checked"] >= 1
    assert "issues" in data
    assert "summary" in data
    # Every issue must have the required fields
    for issue in data["issues"]:
        assert "id" in issue
        assert "severity" in issue
        assert issue["severity"] in ("error", "warning", "info")
        assert "message" in issue
        assert "page_path" in issue
        assert "auto_fixable" in issue


def test_lint_fix_endpoint(client_with_page: TestClient) -> None:
    resp = client_with_page.post("/mimir/lint/fix")
    assert resp.status_code == 200
    data = resp.json()
    assert "issues" in data
    assert "summary" in data


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
    assert data["nodes"][0]["id"] == "technical/test.md"
    assert data["nodes"][0]["category"] == "technical"


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
    assert "source_id" in data
    assert data["source_id"].startswith("src_")


# ---------------------------------------------------------------------------
# GET /mimir/log
# ---------------------------------------------------------------------------


def test_log_after_ingest(client: TestClient) -> None:
    client.post(
        "/mimir/ingest",
        json={"title": "Log Test", "content": "log test content"},
    )
    resp = client.get("/mimir/log")
    assert resp.status_code == 200
    data = resp.json()
    assert "entries" in data


def test_mounts_and_routing_rules_for_composite_adapter(composite_client: TestClient) -> None:
    mounts = composite_client.get("/mimir/mounts")
    assert mounts.status_code == 200
    data = mounts.json()
    assert [mount["name"] for mount in data] == ["local", "shared"]
    assert {mount["pages"] for mount in data} == {1}

    rules = composite_client.get("/mimir/routing/rules")
    assert rules.status_code == 200
    assert rules.json() == [
        {
            "id": "rule-1",
            "prefix": "self/",
            "mountName": "local",
            "priority": 0,
            "active": True,
            "desc": None,
        },
        {
            "id": "rule-2",
            "prefix": "projects/",
            "mountName": "shared",
            "priority": 1,
            "active": True,
            "desc": None,
        },
    ]


def test_upserting_routing_rule_changes_write_target(composite_client: TestClient) -> None:
    update = composite_client.put(
        "/mimir/routing/rules/rule-2",
        json={
            "id": "rule-2",
            "prefix": "projects/",
            "mountName": "local",
            "priority": 1,
            "active": True,
            "desc": "route projects locally",
        },
    )
    assert update.status_code == 200

    write = composite_client.put(
        "/mimir/page",
        json={
            "path": "projects/roadmap/local-now.md",
            "content": "# Local Project\nRouted locally.",
        },
    )
    assert write.status_code == 204

    local_pages = composite_client.get("/mimir/pages", params={"mount": "local"}).json()
    shared_pages = composite_client.get("/mimir/pages", params={"mount": "shared"}).json()
    assert "projects/roadmap/local-now.md" in [page["path"] for page in local_pages]
    assert "projects/roadmap/local-now.md" not in [page["path"] for page in shared_pages]


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


def test_lint_reassign_persists_assignee_on_response(client_with_page: TestClient) -> None:
    lint_report = client_with_page.get("/mimir/lint")
    issue_id = lint_report.json()["issues"][0]["id"]
    resp = client_with_page.post(
        "/mimir/lint/reassign",
        json={"issue_ids": [issue_id], "assignee": "ravn-fjolnir"},
    )
    assert resp.status_code == 200
    issues = [issue for issue in resp.json()["issues"] if issue["id"] == issue_id]
    assert issues
    assert all(issue["assignee"] == "ravn-fjolnir" for issue in issues)


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


def test_dreams_endpoint_parses_dream_cycle_entries(tmp_path: Path) -> None:
    app = _make_app(tmp_path)
    log_path = tmp_path / "mimir" / "wiki" / "log.md"
    log_path.write_text(
        (
            "# Mímir — activity log\n\n"
            "## [2026-04-20] dream | dream cycle complete\n"
            "ravn=ravn-fjolnir pages_updated=4 entities_created=2 lint_fixes=1 duration_ms=3000\n"
        ),
        encoding="utf-8",
    )
    client = TestClient(app)

    resp = client.get("/mimir/dreams")
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": resp.json()[0]["id"],
            "timestamp": "2026-04-20T00:00:00+00:00",
            "ravn": "ravn-fjolnir",
            "mounts": ["test"],
            "pages_updated": 4,
            "entities_created": 2,
            "lint_fixes": 1,
            "duration_ms": 3000,
        }
    ]


def test_unknown_mount_returns_404_for_single_and_composite(
    client: TestClient,
    composite_client: TestClient,
) -> None:
    assert client.get("/mimir/stats", params={"mount": "missing"}).status_code == 404
    assert composite_client.get("/mimir/stats", params={"mount": "missing"}).status_code == 404


def test_empty_ravn_bindings_and_routing_rule_lifecycle(composite_client: TestClient) -> None:
    bindings = composite_client.get("/mimir/ravns/bindings")
    assert bindings.status_code == 200
    assert bindings.json() == []

    create = composite_client.put(
        "/mimir/routing/rules/rule-3",
        json={
            "id": "rule-3",
            "prefix": "directives/",
            "mountName": "shared",
            "priority": 2,
            "active": False,
            "desc": "disabled rule",
        },
    )
    assert create.status_code == 200
    rules = composite_client.get("/mimir/routing/rules").json()
    assert any(rule["id"] == "rule-3" for rule in rules)

    delete = composite_client.delete("/mimir/routing/rules/rule-3")
    assert delete.status_code == 204
    rules_after = composite_client.get("/mimir/routing/rules").json()
    assert all(rule["id"] != "rule-3" for rule in rules_after)


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
    assert edge_pair == {"policies/directives/style.md", "policies/preferences/team.md"}

    people = client.get("/mimir/entities", params={"kind": "person"})
    assert people.status_code == 200
    assert [entity["path"] for entity in people.json()] == ["entities/people/alice.md"]

    all_entities = client.get("/mimir/entities").json()
    kinds = {entity["entity_kind"] for entity in all_entities}
    assert {"person", "project", "component", "technology", "concept"} <= kinds


def test_activity_recent_writes_and_dreams_cover_log_variants(tmp_path: Path) -> None:
    app = _make_composite_app(tmp_path)
    (tmp_path / "local" / "wiki" / "log.md").write_text(
        (
            "# Mímir — activity log\n\n"
            "## [2026-04-21] query | architecture\n"
            "ravn=ravn-fjolnir page=technical/test.md\n"
            "## [invalid-date] dream | dream cycle complete\n"
            "pages_updated 3 entities_created 1 lint_fixes 2\n"
        ),
        encoding="utf-8",
    )
    client = TestClient(app)
    client.post(
        "/mimir/ingest",
        json={"title": "Recent Source", "content": "recent content", "source_type": "document"},
    )

    writes = client.get("/mimir/mounts/recent-writes").json()
    assert any(entry["kind"] == "compile" for entry in writes)
    assert any(entry["kind"] == "dream" for entry in writes)

    activity = client.get("/mimir/activity").json()
    assert any(entry["kind"] == "query" for entry in activity)
    assert any(entry["kind"] == "dream" for entry in activity)

    dreams = client.get("/mimir/dreams").json()
    assert dreams[0]["pages_updated"] == 3
    assert dreams[0]["entities_created"] == 1
    assert dreams[0]["lint_fixes"] == 2


def test_mounts_support_remote_http_host_metadata(tmp_path: Path) -> None:
    app = _make_app(tmp_path / "hosted")
    from ravn.adapters.mimir.http import HttpMimirAdapter

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
    router = MimirRouter(adapter=adapter, name="test", role="local")
    composite_app = FastAPI()
    composite_app.include_router(router.router, prefix="/mimir")
    client = TestClient(composite_app)

    mounts = client.get("/mimir/mounts")
    assert mounts.status_code == 200
    assert mounts.json()[0]["host"] == "mimir-test"


def test_url_ingest_failure_returns_bad_gateway(client: TestClient, respx_mock) -> None:
    respx_mock.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="boom"),
    )

    resp = client.post("/mimir/sources/ingest/url", json={"url": "https://example.com/fail"})
    assert resp.status_code == 502
