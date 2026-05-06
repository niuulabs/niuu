"""Unit tests for EntityExtractor (NIU-578).

Uses mocked LLMPort and MimirPort — no real filesystem or network calls.

Covers:
- Entity extraction returns entities from LLM JSON response
- High confidence entities create new pages
- Medium confidence entities only update existing pages, not create new
- Low confidence entities are logged only (no writes)
- Re-ingesting same source skips duplicate timeline entries (idempotency)
- LLM failure returns empty list and does not crash
- entity_detection=False disables all writes
- MimirIngestTool wires EntityExtractor and reports created paths
- build_mimir_tools accepts optional entity_extractor
- MimirIngestConfig defaults
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from niuu.domain.mimir import EntityType, MimirSource, PageConfidence
from ravn.adapters.tools.entity_extractor import EntityExtractor, ExtractedEntity
from ravn.adapters.tools.mimir_tools import MimirIngestTool, build_mimir_tools
from ravn.config import MimirIngestConfig
from ravn.domain.models import LLMResponse, StopReason, TokenUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pg_entity(confidence: str = "high", key_facts: list | None = None) -> dict:
    return {
        "name": "PostgreSQL",
        "type": "technology",
        "confidence": confidence,
        "key_facts": key_facts or [],
    }


def _config(
    entity_detection: bool = True,
    entity_model: str = "claude-haiku-4-5-20251001",
    entity_max_tokens: int = 1024,
) -> MimirIngestConfig:
    return MimirIngestConfig(
        entity_detection=entity_detection,
        entity_model=entity_model,
        entity_max_tokens=entity_max_tokens,
    )


def _source(
    source_id: str = "src_abc123",
    title: str = "PostgreSQL vs MongoDB",
    content: str = "PostgreSQL is a relational database. MongoDB is a document database.",
) -> MimirSource:
    return MimirSource(
        source_id=source_id,
        title=title,
        content=content,
        source_type="document",
        content_hash="deadbeef",
        ingested_at=datetime(2026, 4, 12, tzinfo=UTC),
    )


def _llm_response(entities: list[dict]) -> LLMResponse:
    payload = json.dumps({"entities": entities})
    return LLMResponse(
        content=payload,
        tool_calls=[],
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=100, output_tokens=200),
    )


def _make_extractor(
    mimir: object | None = None,
    llm: object | None = None,
    config: MimirIngestConfig | None = None,
) -> EntityExtractor:
    if mimir is None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError("not found"))
        mimir.upsert_page = AsyncMock()
    if llm is None:
        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([]))
    if config is None:
        config = _config()
    return EntityExtractor(mimir=mimir, llm=llm, config=config)


# ---------------------------------------------------------------------------
# MimirIngestConfig defaults
# ---------------------------------------------------------------------------


class TestMimirIngestConfig:
    def test_defaults(self) -> None:
        cfg = MimirIngestConfig()
        assert cfg.entity_detection is True
        assert cfg.entity_model == "claude-haiku-4-5-20251001"
        assert cfg.entity_max_tokens == 1024

    def test_disabled(self) -> None:
        cfg = MimirIngestConfig(entity_detection=False)
        assert cfg.entity_detection is False


# ---------------------------------------------------------------------------
# EntityExtractor._extract
# ---------------------------------------------------------------------------


class TestExtractEntities:
    @pytest.mark.asyncio
    async def test_parses_valid_response(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [
                    {
                        "name": "PostgreSQL",
                        "type": "technology",
                        "confidence": "high",
                        "key_facts": ["Relational DB", "Uses SQL"],
                    },
                    {
                        "name": "MongoDB",
                        "type": "technology",
                        "confidence": "medium",
                        "key_facts": ["Document DB"],
                    },
                ]
            )
        )
        extractor = _make_extractor(llm=llm)
        source = _source()
        entities = await extractor._extract(source)

        assert len(entities) == 2
        assert entities[0].name == "PostgreSQL"
        assert entities[0].entity_type == EntityType.technology
        assert entities[0].confidence == PageConfidence.high
        assert entities[0].key_facts == ["Relational DB", "Uses SQL"]
        assert entities[1].name == "MongoDB"
        assert entities[1].confidence == PageConfidence.medium

    @pytest.mark.asyncio
    async def test_llm_json_error_returns_empty(self) -> None:
        llm = AsyncMock()
        bad_response = LLMResponse(
            content="not valid json {{{",
            tool_calls=[],
            stop_reason=StopReason.END_TURN,
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )
        llm.generate = AsyncMock(return_value=bad_response)
        extractor = _make_extractor(llm=llm)
        entities = await extractor._extract(_source())
        assert entities == []

    @pytest.mark.asyncio
    async def test_llm_exception_returns_empty(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("network error"))
        extractor = _make_extractor(llm=llm)
        entities = await extractor._extract(_source())
        assert entities == []

    @pytest.mark.asyncio
    async def test_skips_malformed_entity(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [
                    {"name": "Valid", "type": "technology", "confidence": "high", "key_facts": []},
                    {"name": "Bad", "type": "INVALID_TYPE", "confidence": "high", "key_facts": []},
                ]
            )
        )
        extractor = _make_extractor(llm=llm)
        entities = await extractor._extract(_source())
        assert len(entities) == 1
        assert entities[0].name == "Valid"

    @pytest.mark.asyncio
    async def test_skips_empty_name(self) -> None:
        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [{"name": "  ", "type": "technology", "confidence": "high", "key_facts": []}]
            )
        )
        extractor = _make_extractor(llm=llm)
        entities = await extractor._extract(_source())
        assert entities == []


# ---------------------------------------------------------------------------
# EntityExtractor.run — confidence gating
# ---------------------------------------------------------------------------


class TestConfidenceGating:
    @pytest.mark.asyncio
    async def test_high_confidence_creates_new_page(self) -> None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity()]))
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(_source())

        assert paths == ["entities/technology-postgresql.md"]
        mimir.upsert_page.assert_awaited_once()
        call_path = mimir.upsert_page.call_args[0][0]
        assert call_path == "entities/technology-postgresql.md"

    @pytest.mark.asyncio
    async def test_medium_confidence_no_existing_page_skipped(self) -> None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity("medium")]))
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(_source())

        assert paths == []
        mimir.upsert_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_medium_confidence_updates_existing_page(self) -> None:
        existing_content = (
            "---\ntype: entity\nconfidence: high\nentity_type: technology\n"
            "source_ids: [src_old]\n---\n\n# PostgreSQL\n\n"
            "## Compiled Truth\n\n### Key Facts\n\n- Old fact\n\n"
            "## Timeline\n\n- 2026-01-01: Old entry. [Source: mimir_ingest, src_old, 2026-01-01]\n"
        )
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(return_value=existing_content)
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity("medium")]))
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(_source())

        assert paths == ["entities/technology-postgresql.md"]
        mimir.upsert_page.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_low_confidence_never_writes(self) -> None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [{"name": "PostgreSQL", "type": "technology", "confidence": "low", "key_facts": []}]
            )
        )
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(_source())

        assert paths == []
        mimir.upsert_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_low_confidence_ignores_existing_page(self) -> None:
        existing = "# Some content\n\n## Timeline\n\n- 2026-01-01: Entry. [Source: x, y, z]\n"
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(return_value=existing)
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [{"name": "PostgreSQL", "type": "technology", "confidence": "low", "key_facts": []}]
            )
        )
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(_source())

        assert paths == []
        mimir.upsert_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_same_source_no_duplicate_entry(self) -> None:
        source = _source(source_id="src_abc123")
        existing_content = (
            "---\ntype: entity\nconfidence: high\nentity_type: technology\n"
            "source_ids: [src_abc123]\n---\n\n# PostgreSQL\n\n"
            "## Compiled Truth\n\n### Key Facts\n\n- A fact\n\n"
            "## Timeline\n\n"
            "- 2026-04-12: Detected in source 'PostgreSQL vs MongoDB'. "
            "[Source: mimir_ingest, src_abc123, 2026-04-12]\n"
        )
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(return_value=existing_content)
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity()]))
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(source)

        assert paths == []
        mimir.upsert_page.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_different_source_does_append(self) -> None:
        source = _source(source_id="src_new456")
        existing_content = (
            "---\ntype: entity\nconfidence: high\nentity_type: technology\n"
            "source_ids: [src_abc123]\n---\n\n# PostgreSQL\n\n"
            "## Compiled Truth\n\n### Key Facts\n\n- A fact\n\n"
            "## Timeline\n\n"
            "- 2026-01-01: Old entry. [Source: mimir_ingest, src_abc123, 2026-01-01]\n"
        )
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(return_value=existing_content)
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity()]))
        extractor = _make_extractor(mimir=mimir, llm=llm)
        paths = await extractor.run(source)

        assert paths == ["entities/technology-postgresql.md"]
        mimir.upsert_page.assert_awaited_once()


# ---------------------------------------------------------------------------
# entity_detection disabled
# ---------------------------------------------------------------------------


class TestDetectionDisabled:
    @pytest.mark.asyncio
    async def test_disabled_config_no_llm_call(self) -> None:
        mimir = AsyncMock()
        llm = AsyncMock()
        llm.generate = AsyncMock()
        extractor = EntityExtractor(mimir=mimir, llm=llm, config=_config(entity_detection=False))
        paths = await extractor.run(_source())

        assert paths == []
        llm.generate.assert_not_awaited()
        mimir.upsert_page.assert_not_awaited()


# ---------------------------------------------------------------------------
# Page content format
# ---------------------------------------------------------------------------


class TestPageContent:
    @pytest.mark.asyncio
    async def test_new_page_has_compiled_truth_and_timeline(self) -> None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [
                    {
                        "name": "PostgreSQL",
                        "type": "technology",
                        "confidence": "high",
                        "key_facts": ["Relational DB", "Uses SQL"],
                    }
                ]
            )
        )
        extractor = _make_extractor(mimir=mimir, llm=llm)
        await extractor.run(_source())

        written_content = mimir.upsert_page.call_args[0][1]
        assert "## Compiled Truth" in written_content
        assert "## Timeline" in written_content
        assert "Relational DB" in written_content
        assert "Uses SQL" in written_content
        assert "[Source: mimir_ingest, src_abc123," in written_content
        assert "type: entity" in written_content
        assert "entity_type: technology" in written_content

    @pytest.mark.asyncio
    async def test_timeline_entry_cites_source_id(self) -> None:
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [{"name": "MongoDB", "type": "technology", "confidence": "high", "key_facts": []}]
            )
        )
        extractor = _make_extractor(mimir=mimir, llm=llm)
        source = _source(source_id="src_mongo99")
        await extractor.run(source)

        written_content = mimir.upsert_page.call_args[0][1]
        assert "src_mongo99" in written_content

    def test_entity_page_path_format(self) -> None:
        extractor = _make_extractor()
        entity = ExtractedEntity(
            name="PostgreSQL",
            entity_type=EntityType.technology,
            confidence=PageConfidence.high,
            key_facts=[],
        )
        path = extractor._entity_page_path(entity)
        assert path == "entities/technology-postgresql.md"

    def test_entity_page_path_person(self) -> None:
        extractor = _make_extractor()
        entity = ExtractedEntity(
            name="Andrej Karpathy",
            entity_type=EntityType.person,
            confidence=PageConfidence.high,
            key_facts=[],
        )
        path = extractor._entity_page_path(entity)
        assert path == "entities/person-andrej-karpathy.md"


# ---------------------------------------------------------------------------
# MimirIngestTool integration
# ---------------------------------------------------------------------------


class TestMimirIngestToolWithExtractor:
    @pytest.mark.asyncio
    async def test_ingest_reports_entity_pages(self) -> None:
        adapter = AsyncMock()
        adapter.ingest = AsyncMock(return_value=[])

        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [
                    {
                        "name": "PostgreSQL",
                        "type": "technology",
                        "confidence": "high",
                        "key_facts": [],
                    },
                    {
                        "name": "MongoDB",
                        "type": "technology",
                        "confidence": "high",
                        "key_facts": [],
                    },
                ]
            )
        )
        extractor = EntityExtractor(mimir=mimir, llm=llm, config=_config())
        tool = MimirIngestTool(adapter=adapter, entity_extractor=extractor)

        result = await tool.execute(
            {
                "content": "PostgreSQL vs MongoDB comparison.",
                "title": "DB Comparison",
                "source_type": "document",
            }
        )

        assert not result.is_error
        assert "entities/technology-postgresql.md" in result.content
        assert "entities/technology-mongodb.md" in result.content

    @pytest.mark.asyncio
    async def test_ingest_without_extractor_returns_base_result(self) -> None:
        adapter = AsyncMock()
        adapter.ingest = AsyncMock(return_value=[])
        tool = MimirIngestTool(adapter=adapter)

        result = await tool.execute(
            {"content": "Some content", "title": "Some Title", "source_type": "document"}
        )

        assert not result.is_error
        assert "Ingested source" in result.content

    @pytest.mark.asyncio
    async def test_extractor_failure_does_not_crash_ingest(self) -> None:
        adapter = AsyncMock()
        adapter.ingest = AsyncMock(return_value=[])

        mimir = AsyncMock()
        mimir.read_page = AsyncMock(side_effect=FileNotFoundError())
        mimir.upsert_page = AsyncMock(side_effect=RuntimeError("disk full"))

        llm = AsyncMock()
        llm.generate = AsyncMock(return_value=_llm_response([_pg_entity()]))
        extractor = EntityExtractor(mimir=mimir, llm=llm, config=_config())
        tool = MimirIngestTool(adapter=adapter, entity_extractor=extractor)

        result = await tool.execute({"content": "Some content", "title": "Some Title"})

        assert not result.is_error
        assert "Ingested source" in result.content


# ---------------------------------------------------------------------------
# build_mimir_tools
# ---------------------------------------------------------------------------


class TestBuildMimirTools:
    def test_build_without_extractor(self) -> None:
        adapter = MagicMock()
        tools = build_mimir_tools(adapter)
        assert len(tools) == 6
        ingest_tool = tools[0]
        assert isinstance(ingest_tool, MimirIngestTool)
        assert ingest_tool._entity_extractor is None

    def test_build_with_extractor(self) -> None:
        adapter = MagicMock()
        extractor = MagicMock(spec=EntityExtractor)
        tools = build_mimir_tools(adapter, entity_extractor=extractor)
        ingest_tool = tools[0]
        assert isinstance(ingest_tool, MimirIngestTool)
        assert ingest_tool._entity_extractor is extractor


# ---------------------------------------------------------------------------
# High confidence page updates existing with merged facts
# ---------------------------------------------------------------------------


class TestHighConfidenceMergesFacts:
    @pytest.mark.asyncio
    async def test_high_confidence_merges_facts_into_existing(self) -> None:
        existing_content = (
            "---\ntype: entity\nconfidence: high\nentity_type: technology\n"
            "source_ids: [src_old]\n---\n\n# PostgreSQL\n\n"
            "## Compiled Truth\n\n### Key Facts\n\n- Old fact\n\n"
            "## Timeline\n\n- 2026-01-01: Old entry. [Source: mimir_ingest, src_old, 2026-01-01]\n"
        )
        mimir = AsyncMock()
        mimir.read_page = AsyncMock(return_value=existing_content)
        mimir.upsert_page = AsyncMock()

        llm = AsyncMock()
        llm.generate = AsyncMock(
            return_value=_llm_response(
                [
                    {
                        "name": "PostgreSQL",
                        "type": "technology",
                        "confidence": "high",
                        "key_facts": ["New fact A", "New fact B"],
                    }
                ]
            )
        )
        extractor = _make_extractor(mimir=mimir, llm=llm)
        await extractor.run(_source(source_id="src_new"))

        written = mimir.upsert_page.call_args[0][1]
        assert "New fact A" in written
        assert "New fact B" in written
