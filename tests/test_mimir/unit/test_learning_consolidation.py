"""Write-time scoped consolidation tests (NIU-1062 "micro-dream").

Exit criterion under test: ingesting a source that supports an existing fact
raises its proof count within one consolidation cycle, without a nightly
dream run.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mimir.adapters.markdown import MarkdownMimirAdapter
from mimir.config import EvidenceConfig
from mimir.learning import compute_page_evidence, consolidate_source
from mimir.router import MimirRouter
from niuu.domain.mimir import MimirSource, compute_content_hash

NOW = datetime(2026, 6, 12, tzinfo=UTC)

ADAPTER_LOGGER = "mimir.adapters.markdown"

ALICE_PAGE = """---
type: entity
confidence: high
entity_type: person
---

# Alice Smith

## Compiled Truth

### Key Facts
- Alice leads the widget programme.

## Timeline

- 2026-06-01: Alice presented the widget programme roadmap. [Source: a, b, c]
"""

ESPRESSO_PAGE = """---
type: topic
---

# Espresso machine

## Compiled Truth

### Key Facts
- The espresso machine needs descaling monthly.

## Timeline

- 2026-05-20: Descaled the espresso machine. [Source: a, b, c]
"""

SUPPORTING_TEXT = (
    "Quarterly update: Alice Smith confirmed she still leads the widget "
    "programme and outlined the next milestones."
)


def _build_adapter(
    tmp_path: Path,
    evidence: EvidenceConfig | None = None,
) -> MarkdownMimirAdapter:
    adapter = MarkdownMimirAdapter(root=tmp_path / "mimir", evidence_config=evidence)
    wiki = tmp_path / "mimir" / "wiki"
    (wiki / "entities").mkdir(parents=True, exist_ok=True)
    (wiki / "household").mkdir(parents=True, exist_ok=True)
    (wiki / "entities" / "alice-smith.md").write_text(ALICE_PAGE, encoding="utf-8")
    (wiki / "household" / "espresso.md").write_text(ESPRESSO_PAGE, encoding="utf-8")
    return adapter


def _source(content: str, title: str = "Quarterly widget update") -> MimirSource:
    return MimirSource(
        source_id="src_" + compute_content_hash(content)[:16],
        title=title,
        content=content,
        source_type="document",
        origin_url=None,
        content_hash=compute_content_hash(content),
        ingested_at=NOW,
    )


def _client(adapter: MarkdownMimirAdapter) -> TestClient:
    router = MimirRouter(adapter=adapter)
    app = FastAPI()
    app.include_router(router.router, prefix="/mimir")
    return TestClient(app)


# ---------------------------------------------------------------------------
# Source-backed evidence (compute_page_evidence + sources)
# ---------------------------------------------------------------------------


def test_evidence_without_sources_is_backwards_compatible() -> None:
    evidence = compute_page_evidence(ALICE_PAGE, now=NOW)
    assert len(evidence) == 1
    assert evidence[0].proof_count == 1  # one timeline proof
    assert evidence[0].source_proof_count == 0
    assert evidence[0].to_dict()["source_proof_count"] == 0


def test_sources_add_to_proof_count() -> None:
    evidence = compute_page_evidence(
        ALICE_PAGE,
        now=NOW,
        sources=[("src_1", SUPPORTING_TEXT), ("src_2", "Completely unrelated note.")],
    )
    assert evidence[0].proof_count == 2  # timeline + bearing source
    assert evidence[0].source_proof_count == 1
    assert evidence[0].supporting_dates == ["2026-06-01"]  # trend stays timeline-driven


def test_source_below_overlap_threshold_does_not_count() -> None:
    config = EvidenceConfig(min_token_overlap=4)
    evidence = compute_page_evidence(
        ALICE_PAGE,
        config,
        now=NOW,
        sources=[("src_1", "Alice went sailing.")],  # only 1 shared token
    )
    assert evidence[0].source_proof_count == 0


# ---------------------------------------------------------------------------
# Pure consolidation step
# ---------------------------------------------------------------------------


def test_consolidate_source_counts_strengthened_facts() -> None:
    pages = [
        ("entities/alice-smith.md", "Alice Smith", ALICE_PAGE),
        ("household/espresso.md", "Espresso machine", ESPRESSO_PAGE),
    ]
    result = consolidate_source(
        "src_new",
        "Quarterly widget update",
        SUPPORTING_TEXT,
        pages,
        other_sources=[],
        now=NOW,
    )
    assert result.pages == ["entities/alice-smith.md"]  # espresso page untouched
    assert result.strengthened == 1
    assert result.to_dict() == {
        "source_id": "src_new",
        "pages": ["entities/alice-smith.md"],
        "strengthened": 1,
    }


def test_consolidate_source_ignores_non_bearing_source() -> None:
    pages = [("entities/alice-smith.md", "Alice Smith", ALICE_PAGE)]
    result = consolidate_source(
        "src_new",
        "Espresso descaling tips",
        "Use citric acid for the espresso machine boiler.",
        pages,
        other_sources=[],
        now=NOW,
    )
    assert result.pages == []
    assert result.strengthened == 0


# ---------------------------------------------------------------------------
# Integration: ingest fires consolidation (no dream cycle involved)
# ---------------------------------------------------------------------------


async def test_ingest_raises_proof_count_without_dream_run(
    tmp_path: Path,
    caplog,
) -> None:
    adapter = _build_adapter(tmp_path)
    content = await adapter.read_page("entities/alice-smith.md")

    before = compute_page_evidence(content, now=NOW, sources=adapter._all_raw_source_pairs())
    p0 = before[0].proof_count
    assert p0 == 1

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOGGER):
        pages_updated = await adapter.ingest(_source(SUPPORTING_TEXT))

    assert pages_updated == []  # page synthesis stays delegated to the agent
    assert adapter._last_consolidated == ["entities/alice-smith.md"]

    after = compute_page_evidence(content, now=NOW, sources=adapter._all_raw_source_pairs())
    assert after[0].proof_count > p0
    assert after[0].proof_count == 2
    assert after[0].source_proof_count == 1

    messages = [record.getMessage() for record in caplog.records]
    expected = (
        f"mimir.consolidation: source={_source(SUPPORTING_TEXT).source_id} pages=1 strengthened=1"
    )
    assert expected in messages

    # No dream cycle ran: the activity log records only the ingest.
    log_content = (tmp_path / "mimir" / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "dream" not in log_content.lower()


async def test_consolidate_on_ingest_disabled_skips_pass(
    tmp_path: Path,
    caplog,
) -> None:
    adapter = _build_adapter(tmp_path, evidence=EvidenceConfig(consolidate_on_ingest=False))
    with caplog.at_level(logging.INFO, logger=ADAPTER_LOGGER):
        await adapter.ingest(_source(SUPPORTING_TEXT))

    assert adapter._last_consolidated == []
    assert not any("mimir.consolidation" in record.getMessage() for record in caplog.records)


def test_all_raw_source_pairs_skips_corrupt_json(tmp_path: Path) -> None:
    adapter = _build_adapter(tmp_path)
    raw_dir = tmp_path / "mimir" / "raw"
    (raw_dir / "src_broken.json").write_text("{not json", encoding="utf-8")
    source = _source(SUPPORTING_TEXT)
    adapter._write_raw_source(source)

    pairs = adapter._all_raw_source_pairs()
    assert pairs == [(source.source_id, SUPPORTING_TEXT)]


# ---------------------------------------------------------------------------
# Router level: ingest response + evidence endpoint
# ---------------------------------------------------------------------------


def test_ingest_endpoint_returns_consolidated_paths(tmp_path: Path, caplog) -> None:
    adapter = _build_adapter(tmp_path)
    client = _client(adapter)

    before = client.get("/mimir/evidence", params={"path": "entities/alice-smith.md"}).json()
    p0 = before[0]["proof_count"]
    assert before[0]["source_proof_count"] == 0

    with caplog.at_level(logging.INFO, logger=ADAPTER_LOGGER):
        response = client.post(
            "/mimir/ingest",
            json={"title": "Quarterly widget update", "content": SUPPORTING_TEXT},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["pages_updated"] == []
    assert body["consolidated"] == ["entities/alice-smith.md"]
    assert any("mimir.consolidation: source=" in record.getMessage() for record in caplog.records)

    after = client.get("/mimir/evidence", params={"path": "entities/alice-smith.md"}).json()
    assert after[0]["proof_count"] > p0
    assert after[0]["source_proof_count"] == 1
