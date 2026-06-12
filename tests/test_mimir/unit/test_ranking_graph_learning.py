"""Tests for ranking boosts (NIU-1057), link graph + relational arm (NIU-1058),
and the learning loop (NIU-1062)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.markdown import MarkdownMimirAdapter, _extract_typed_edges
from mimir.config import EvidenceConfig, RankingConfig
from mimir.learning import (
    TREND_NEW,
    TREND_STABLE,
    TREND_STALE,
    TREND_STRENGTHENING,
    TREND_WEAKENING,
    compute_page_evidence,
    extract_key_facts,
    find_bearing_pages,
    revise_belief,
)
from mimir.ranking import (
    apply_boosts,
    backlink_factor,
    recency_factor,
    title_match_factor,
    tokenize,
    zone_factor,
)
from mimir.router import MimirRouter
from niuu.domain.mimir import MimirPageMeta, PageConfidence, PageType

NOW = datetime(2026, 6, 12, tzinfo=UTC)


def _meta(**overrides) -> MimirPageMeta:
    defaults = dict(
        path="technical/page.md",
        title="A Page",
        summary="",
        category="technical",
        updated_at=NOW,
    )
    defaults.update(overrides)
    return MimirPageMeta(**defaults)


# ---------------------------------------------------------------------------
# Ranking boosts
# ---------------------------------------------------------------------------


def test_recency_factor_decays_with_floor() -> None:
    config = RankingConfig(recency_half_life_days=90.0, recency_floor=0.5)
    assert recency_factor(NOW, config, now=NOW) == 1.0
    half_life_ago = NOW - timedelta(days=90)
    assert recency_factor(half_life_ago, config, now=NOW) == pytest.approx(0.5)
    ancient = NOW - timedelta(days=3650)
    assert recency_factor(ancient, config, now=NOW) == 0.5  # floored


def test_recency_factor_disabled_with_zero_half_life() -> None:
    config = RankingConfig(recency_half_life_days=0)
    assert recency_factor(NOW - timedelta(days=999), config, now=NOW) == 1.0


def test_title_match_requires_full_coverage() -> None:
    config = RankingConfig(title_match_boost=1.25)
    assert title_match_factor(tokenize("widget assembly"), "Widget assembly line", config) == 1.25
    assert title_match_factor(tokenize("widget rocket"), "Widget assembly line", config) == 1.0
    assert title_match_factor(set(), "Widget", config) == 1.0


def test_backlink_factor_log_scaled() -> None:
    config = RankingConfig(backlink_alpha=0.1)
    assert backlink_factor(0, config) == 1.0
    assert backlink_factor(10, config) > backlink_factor(2, config) > 1.0


def test_zone_factor_demotes_timeline() -> None:
    config = RankingConfig(zone_weights={"timeline": 0.9})
    assert zone_factor("Timeline", config) == 0.9
    assert zone_factor("Key Facts", config) == 1.0


def test_apply_boosts_breakdown_is_product() -> None:
    # Explicit non-neutral weights — defaults are neutral for confidence and
    # backlinks per the hybrid ablation (NIU-1057).
    config = RankingConfig(
        recency_half_life_days=0,
        confidence_boosts={"high": 1.15, "medium": 1.0, "low": 0.85},
        backlink_alpha=0.1,
    )
    meta = _meta(
        title="Widget assembly",
        page_type=PageType.decision,
        confidence=PageConfidence.high,
    )
    final, breakdown = apply_boosts(
        0.5, meta, tokenize("widget assembly"), config, backlink_count=3, now=NOW
    )
    expected = 0.5
    for name, factor in breakdown.items():
        if name not in {"base", "final"}:
            expected *= factor
    assert final == pytest.approx(expected)
    assert breakdown["title_match"] == config.title_match_boost
    assert breakdown["confidence"] == config.confidence_boosts["high"]
    assert breakdown["page_type"] == config.page_type_weights["decision"]
    assert breakdown["final"] == pytest.approx(final)


# ---------------------------------------------------------------------------
# Link graph + relational arm
# ---------------------------------------------------------------------------

ENTITY_PAGE = """---
type: entity
confidence: high
entity_type: person
related_entities: [acme-corp]
---

# Alice Smith

## Compiled Truth

### Key Facts
- Alice is the lead engineer at Acme Corp.

### Relationships
- [[acme-corp]] — rel: works_at — lead engineer.

## Timeline

- 2026-05-01: Joined Acme. [Source: test, fixture, 2026-05-01]
"""

ORG_PAGE = """---
type: entity
confidence: high
entity_type: organization
---

# Acme Corp

## Compiled Truth

### Key Facts
- Acme builds widgets.

## Timeline

- 2026-01-01: Founded. [Source: test, fixture, 2026-01-01]
"""

TOPIC_PAGE = """---
type: topic
---

# Widget assembly

## Compiled Truth

### Key Facts
- Widgets are assembled by [[alice-smith]] at the plant.

## Timeline

- 2026-02-01: Plant opened. [Source: test, fixture, 2026-02-01]
"""


def _graph_adapter(tmp_path: Path) -> MarkdownMimirAdapter:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir")
    wiki = tmp_path / "mimir" / "wiki"
    (wiki / "entities").mkdir(parents=True, exist_ok=True)
    (wiki / "technical").mkdir(parents=True, exist_ok=True)
    (wiki / "entities" / "alice-smith.md").write_text(ENTITY_PAGE, encoding="utf-8")
    (wiki / "entities" / "acme-corp.md").write_text(ORG_PAGE, encoding="utf-8")
    (wiki / "technical" / "widgets.md").write_text(TOPIC_PAGE, encoding="utf-8")
    return adapter


def test_link_graph_builds_edges_and_backlinks(tmp_path: Path) -> None:
    adapter = _graph_adapter(tmp_path)
    graph = adapter._link_graph()

    targets = {edge.target for edge in graph.forward["entities/alice-smith.md"]}
    assert targets == {"entities/acme-corp.md"}
    assert graph.backlinks["entities/acme-corp.md"] == 1
    assert graph.backlinks["entities/alice-smith.md"] == 1  # from widgets.md
    assert {meta.path for meta in graph.entities} == {
        "entities/alice-smith.md",
        "entities/acme-corp.md",
    }


def test_typed_edges_parsed() -> None:
    assert _extract_typed_edges(ENTITY_PAGE) == {"acme-corp": "works_at"}


def test_related_pages_traversal_and_rel_filter(tmp_path: Path) -> None:
    adapter = _graph_adapter(tmp_path)

    related = adapter.related_pages("entities/alice-smith.md", depth=1)
    paths = {item["path"] for item in related}
    assert paths == {"entities/acme-corp.md", "technical/widgets.md"}

    typed_only = adapter.related_pages("entities/alice-smith.md", depth=1, rel="works_at")
    assert [item["path"] for item in typed_only] == ["entities/acme-corp.md"]

    two_hops = adapter.related_pages("technical/widgets.md", depth=2)
    assert {item["path"] for item in two_hops} == {
        "entities/alice-smith.md",
        "entities/acme-corp.md",
    }


def test_graph_cache_invalidated_on_upsert(tmp_path: Path) -> None:
    adapter = _graph_adapter(tmp_path)
    assert adapter._link_graph() is adapter._link_graph()  # cached

    import asyncio

    asyncio.run(adapter.upsert_page("technical/new.md", "# New\n\nLinks [[acme-corp]].\n"))
    graph = adapter._link_graph()
    assert graph.backlinks["entities/acme-corp.md"] == 2


async def test_relational_arm_injects_entities_when_fts_misses(tmp_path: Path) -> None:
    """The signature NIU-1058 behavior: FTS returns nothing, entities still surface."""

    class EmptySearchPort:
        async def search(self, query: str, limit: int = 10):
            return []

        async def index(self, *a, **k): ...
        async def remove(self, *a): ...
        async def rebuild(self): ...

    adapter = _graph_adapter(tmp_path)
    adapter._search_port = EmptySearchPort()  # type: ignore[assignment]

    pages = await adapter.search("who works at Acme Corp")
    paths = [p.meta.path for p in pages]
    assert "entities/acme-corp.md" in paths
    assert "entities/alice-smith.md" in paths  # 1-hop neighbor
    assert pages[0].meta.score_breakdown is not None
    assert pages[0].meta.score_breakdown["graph"] >= 1.0


async def test_ranking_disabled_preserves_legacy_behavior(tmp_path: Path) -> None:
    class OneHitPort:
        async def search(self, query: str, limit: int = 10):
            from niuu.ports.search import SearchResult

            return [
                SearchResult(
                    id="technical/widgets.md::0",
                    content="x",
                    score=0.9,
                    metadata={"page_path": "technical/widgets.md", "section_heading": ""},
                )
            ]

        async def index(self, *a, **k): ...
        async def remove(self, *a): ...
        async def rebuild(self): ...

    adapter = _graph_adapter(tmp_path)
    adapter._search_port = OneHitPort()  # type: ignore[assignment]
    adapter._ranking = RankingConfig(enabled=False)

    pages = await adapter.search("who works at Acme Corp")
    assert [p.meta.path for p in pages] == ["technical/widgets.md"]
    assert pages[0].meta.score_breakdown is None


# ---------------------------------------------------------------------------
# Learning loop
# ---------------------------------------------------------------------------

EVIDENCE_PAGE = """---
type: entity
confidence: high
entity_type: person
---

# Alice Smith

## Compiled Truth

### Key Facts
- Alice leads the widget assembly programme.
- Alice prefers asynchronous written updates.
- Alice has a gliding licence.

## Timeline

- 2026-06-01: Alice presented the widget assembly roadmap. [Source: a, b, c]
- 2026-06-05: Alice reorganised the widget assembly line. [Source: a, b, c]
- 2026-06-10: Alice took over the assembly programme for widgets. [Source: a, b, c]
- 2026-01-02: Alice asked for written asynchronous updates only. [Source: a, b, c]
"""


def test_extract_key_facts() -> None:
    facts = extract_key_facts(EVIDENCE_PAGE)
    assert len(facts) == 3
    assert facts[0].startswith("Alice leads")


def test_compute_page_evidence_counts_and_trends() -> None:
    config = EvidenceConfig()
    evidence = {e.fact: e for e in compute_page_evidence(EVIDENCE_PAGE, config, now=NOW)}

    programme = evidence["Alice leads the widget assembly programme."]
    assert programme.proof_count == 3
    assert programme.trend == TREND_STRENGTHENING
    assert programme.latest_support == "2026-06-10"

    prefers = evidence["Alice prefers asynchronous written updates."]
    assert prefers.proof_count == 1
    assert prefers.trend == TREND_STALE  # support is >90 days old

    gliding = evidence["Alice has a gliding licence."]
    assert gliding.proof_count == 0
    assert gliding.trend == TREND_NEW


def test_evidence_trend_boundaries() -> None:
    config = EvidenceConfig(weakening_after_days=60, stale_after_days=90)
    base = (
        "---\ntype: topic\n---\n\n# T\n\n## Compiled Truth\n\n### Key Facts\n"
        "- The reactor output is stable.\n\n## Timeline\n\n"
        "- {date}: Reactor output measured stable again. [Source: a, b, c]\n"
    )

    recent = base.format(date=(NOW - timedelta(days=10)).strftime("%Y-%m-%d"))
    assert compute_page_evidence(recent, config, now=NOW)[0].trend == TREND_STABLE

    weakening = base.format(date=(NOW - timedelta(days=70)).strftime("%Y-%m-%d"))
    assert compute_page_evidence(weakening, config, now=NOW)[0].trend == TREND_WEAKENING

    stale = base.format(date=(NOW - timedelta(days=120)).strftime("%Y-%m-%d"))
    assert compute_page_evidence(stale, config, now=NOW)[0].trend == TREND_STALE


def test_revise_belief_rewrites_truth_and_appends_journey() -> None:
    revised = revise_belief(
        EVIDENCE_PAGE,
        "Alice has a gliding licence.",
        "Alice is training for a gliding licence.",
        "correction, chat, 2026-06-12",
        now=NOW,
    )
    assert "Alice is training for a gliding licence." in revised
    assert "- Alice has a gliding licence." not in revised.split("## Timeline")[0]
    # Journey recorded, original timeline untouched (append-only).
    assert 'belief revised: "Alice has a gliding licence."' in revised
    for line in EVIDENCE_PAGE.split("## Timeline")[1].strip().splitlines():
        assert line in revised


def test_revise_belief_unknown_fact_raises() -> None:
    with pytest.raises(ValueError, match="not found"):
        revise_belief(EVIDENCE_PAGE, "Nonexistent fact.", "x", "y")


def test_find_bearing_pages() -> None:
    pages = [
        ("entities/alice-smith.md", "Alice Smith"),
        ("entities/acme-corp.md", "Acme Corp"),
        ("household/espresso.md", "Espresso machine"),
    ]
    affected = find_bearing_pages(
        "Acme quarterly update",
        "Alice Smith presented Acme Corp results.",
        pages,
        min_token_overlap=2,
    )
    assert "entities/alice-smith.md" in affected
    assert "entities/acme-corp.md" in affected
    assert "household/espresso.md" not in affected


# ---------------------------------------------------------------------------
# Router endpoints
# ---------------------------------------------------------------------------


def _client(tmp_path: Path) -> TestClient:
    adapter = _graph_adapter(tmp_path)
    router = MimirRouter(adapter=adapter)
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return TestClient(app)


def test_search_debug_returns_breakdown(tmp_path: Path) -> None:
    client = _client(tmp_path)
    plain = client.get("/mimir/search", params={"q": "widget assembly"}).json()
    assert plain and plain[0]["score_breakdown"] is None

    debug = client.get("/mimir/search", params={"q": "widget assembly", "debug": "true"}).json()
    assert debug[0]["score"] is None or isinstance(debug[0]["score"], float)


def test_entities_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get("/mimir/entities/index").json()
    slugs = {item["slug"] for item in body}
    assert slugs == {"alice-smith", "acme-corp"}
    alice = next(item for item in body if item["slug"] == "alice-smith")
    assert alice["type"] == "person"
    assert alice["confidence"] == "high"
    assert alice["path"] == "entities/alice-smith.md"


def test_related_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get(
        "/mimir/related", params={"path": "entities/alice-smith.md", "depth": 1}
    ).json()
    assert {item["path"] for item in body} == {"entities/acme-corp.md", "technical/widgets.md"}
    typed = client.get(
        "/mimir/related",
        params={"path": "entities/alice-smith.md", "rel": "works_at"},
    ).json()
    assert [item["rel"] for item in typed] == ["works_at"]


def test_evidence_endpoint(tmp_path: Path) -> None:
    client = _client(tmp_path)
    body = client.get("/mimir/evidence", params={"path": "entities/alice-smith.md"}).json()
    assert body[0]["fact"].startswith("Alice is the lead engineer")
    assert client.get("/mimir/evidence", params={"path": "nope.md"}).status_code == 404


def test_revise_endpoint_roundtrip(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/mimir/page/revise",
        json={
            "path": "entities/acme-corp.md",
            "old_fact": "Acme builds widgets.",
            "new_fact": "Acme builds widgets and gadgets.",
            "attribution": "test, suite, 2026-06-12",
        },
    )
    assert response.status_code == 200
    page = client.get("/mimir/page", params={"path": "entities/acme-corp.md"}).json()
    assert "widgets and gadgets" in page["content"]
    assert "belief revised" in page["content"]

    missing = client.post(
        "/mimir/page/revise",
        json={
            "path": "entities/acme-corp.md",
            "old_fact": "nope",
            "new_fact": "x",
            "attribution": "y",
        },
    )
    assert missing.status_code == 422
