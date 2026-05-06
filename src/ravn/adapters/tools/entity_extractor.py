"""Entity extractor — LLM-based entity detection during Mímir ingest (NIU-578).

After a raw source is persisted, the extractor calls a cheap LLM to identify
named entities (people, projects, technologies, etc.) and auto-creates or
updates compiled-truth entity pages in the wiki.

Confidence gating
-----------------
- ``high``   — create new pages and update existing ones
- ``medium`` — append timeline entry to existing pages only (never create new)
- ``low``    — log only, no writes

Idempotency
-----------
Timeline entries include the ``source_id`` so re-ingesting the same source
will not add a duplicate entry (the existing entry already cites the source).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mimir.compiled_truth import (
    append_timeline_entry,
    parse_page,
    rewrite_compiled_truth,
)
from niuu.domain.mimir import EntityType, MimirSource, PageConfidence, slugify
from niuu.ports.mimir import MimirPort
from ravn.config import MimirIngestConfig
from ravn.ports.llm import LLMPort

logger = logging.getLogger(__name__)

_EXTRACTION_SYSTEM = (
    "You are an entity extraction assistant. "
    "Extract named entities from the provided text and respond only with valid JSON."
)

_EXTRACTION_PROMPT = """\
Extract named entities from the following source document.

Source title: {title}
Source content (first 3000 chars):
{content}

Return a JSON object with a single key "entities" containing an array of objects.
Each object must have:
  - "name":        string — the canonical entity name
  - "type":        one of "person", "project", "concept", "technology", "organization", "strategy"
  - "confidence":  one of "high", "medium", "low"
  - "key_facts":   array of strings — 2-5 concise facts extracted from the source

Only include entities that are clearly named and meaningfully discussed.
Respond with valid JSON only, no prose.\
"""


@dataclass
class ExtractedEntity:
    """A single entity detected by the LLM extractor."""

    name: str
    entity_type: EntityType
    confidence: PageConfidence
    key_facts: list[str] = field(default_factory=list)


class EntityExtractor:
    """Extracts entities from a MimirSource and applies them to the wiki.

    Args:
        mimir:  Mímir adapter for reading and writing entity pages.
        llm:    LLM adapter for the extraction call.
        config: Ingest configuration (model, max_tokens, enabled flag).
    """

    def __init__(
        self,
        mimir: MimirPort,
        llm: LLMPort,
        config: MimirIngestConfig,
    ) -> None:
        self._mimir = mimir
        self._llm = llm
        self._config = config

    async def run(self, source: MimirSource) -> list[str]:
        """Extract entities from *source* and upsert entity pages.

        Returns a list of wiki paths that were created or updated.
        """
        if not self._config.entity_detection:
            return []

        entities = await self._extract(source)
        if not entities:
            return []

        date_str = datetime.now(UTC).strftime("%Y-%m-%d")
        updated_paths: list[str] = []

        for entity in entities:
            try:
                path = await self._apply(entity, source, date_str)
                if path:
                    updated_paths.append(path)
            except Exception as exc:
                logger.warning(
                    "entity_extractor: failed to apply entity %r: %s",
                    entity.name,
                    exc,
                )

        return updated_paths

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    async def _extract(self, source: MimirSource) -> list[ExtractedEntity]:
        """Call the LLM and parse the entity list.  Returns [] on failure."""
        prompt = _EXTRACTION_PROMPT.format(
            title=source.title,
            content=source.content[:3000],
        )
        try:
            response = await self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system=_EXTRACTION_SYSTEM,
                model=self._config.entity_model,
                max_tokens=self._config.entity_max_tokens,
            )
            raw = json.loads(response.content)
            return self._parse_entities(raw.get("entities", []))
        except Exception as exc:
            logger.warning(
                "entity_extractor: LLM extraction failed for source %r: %s",
                source.source_id,
                exc,
            )
            return []

    def _parse_entities(self, raw: list[dict]) -> list[ExtractedEntity]:
        """Validate and convert the raw LLM response into ExtractedEntity objects."""
        entities: list[ExtractedEntity] = []
        for item in raw:
            try:
                entity_type = EntityType(item.get("type", ""))
                confidence = PageConfidence(item.get("confidence", ""))
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                key_facts = [str(f) for f in item.get("key_facts", []) if f]
                entities.append(
                    ExtractedEntity(
                        name=name,
                        entity_type=entity_type,
                        confidence=confidence,
                        key_facts=key_facts,
                    )
                )
            except (ValueError, KeyError) as exc:
                logger.debug("entity_extractor: skipping malformed entity entry: %s", exc)
        return entities

    # ------------------------------------------------------------------
    # Page creation / update
    # ------------------------------------------------------------------

    async def _apply(
        self,
        entity: ExtractedEntity,
        source: MimirSource,
        date_str: str,
    ) -> str | None:
        """Create or update the entity page for *entity*.

        Returns the page path on write, None when the action is skipped (low
        confidence, or medium confidence with no existing page).
        """
        if entity.confidence == PageConfidence.low:
            logger.debug(
                "entity_extractor: skipping low-confidence entity %r",
                entity.name,
            )
            return None

        page_path = self._entity_page_path(entity)
        timeline_entry = self._build_timeline_entry(entity, source, date_str)

        try:
            existing_content = await self._mimir.read_page(page_path)
        except FileNotFoundError:
            existing_content = None

        if existing_content is not None:
            return await self._update_page(
                page_path, existing_content, entity, source, timeline_entry
            )

        if entity.confidence == PageConfidence.medium:
            logger.debug(
                "entity_extractor: medium-confidence entity %r has no existing page — skipping",
                entity.name,
            )
            return None

        # high confidence, new page
        return await self._create_page(page_path, entity, source, timeline_entry)

    async def _create_page(
        self,
        page_path: str,
        entity: ExtractedEntity,
        source: MimirSource,
        timeline_entry: str,
    ) -> str:
        """Build and write a new compiled-truth entity page."""
        content = self._build_entity_page(entity, source, timeline_entry)
        await self._mimir.upsert_page(page_path, content)
        logger.info(
            "entity_extractor: created entity page %r for %r",
            page_path,
            entity.name,
        )
        return page_path

    async def _update_page(
        self,
        page_path: str,
        existing_content: str,
        entity: ExtractedEntity,
        source: MimirSource,
        timeline_entry: str,
    ) -> str | None:
        """Merge new facts and append timeline entry to an existing page.

        Returns None if the source is already cited (idempotency guard).
        """
        parsed = parse_page(existing_content)

        # Idempotency: don't add a duplicate timeline entry for the same source
        for entry in parsed.timeline_entries:
            if source.source_id in entry.source:
                logger.debug(
                    "entity_extractor: source %r already cited in %r — skipping",
                    source.source_id,
                    page_path,
                )
                return None

        # Append timeline entry
        updated = append_timeline_entry(existing_content, timeline_entry)

        # For high confidence, also merge in new facts into Compiled Truth
        if entity.confidence == PageConfidence.high and entity.key_facts:
            updated = self._merge_facts(updated, entity)

        await self._mimir.upsert_page(page_path, updated)
        logger.info(
            "entity_extractor: updated entity page %r for %r",
            page_path,
            entity.name,
        )
        return page_path

    # ------------------------------------------------------------------
    # Content builders
    # ------------------------------------------------------------------

    def _entity_page_path(self, entity: ExtractedEntity) -> str:
        """Return the wiki-relative path for an entity page.

        Format: ``entities/{type}-{slug}.md``
        """
        slug = slugify(entity.name)
        return f"entities/{entity.entity_type}-{slug}.md"

    def _build_timeline_entry(
        self,
        entity: ExtractedEntity,
        source: MimirSource,
        date_str: str,
    ) -> str:
        """Return a single timeline line for this entity/source pair."""
        return (
            f"- {date_str}: Detected in source '{source.title}'. "
            f"[Source: mimir_ingest, {source.source_id}, {date_str}]"
        )

    def _build_entity_page(
        self,
        entity: ExtractedEntity,
        source: MimirSource,
        timeline_entry: str,
    ) -> str:
        """Return the full Markdown content for a new entity page."""
        facts_block = (
            "\n".join(f"- {f}" for f in entity.key_facts)
            if entity.key_facts
            else "- (no facts extracted)"
        )

        return (
            f"---\n"
            f"type: entity\n"
            f"confidence: {entity.confidence}\n"
            f"entity_type: {entity.entity_type}\n"
            f"source_ids: [{source.source_id}]\n"
            f"---\n\n"
            f"# {entity.name}\n\n"
            f"## Compiled Truth\n\n"
            f"### Key Facts\n\n"
            f"{facts_block}\n\n"
            f"### Relationships\n\n"
            f"### Assessment\n\n"
            f"## Timeline\n\n"
            f"{timeline_entry}\n"
        )

    def _merge_facts(self, content: str, entity: ExtractedEntity) -> str:
        """Merge *entity.key_facts* into the Compiled Truth section.

        New facts are appended under Key Facts; existing content is preserved.
        """
        parsed = parse_page(content)
        existing_truth = parsed.compiled_truth

        new_facts_block = "\n".join(f"- {f}" for f in entity.key_facts)

        if existing_truth.strip():
            merged_truth = f"{existing_truth.rstrip()}\n{new_facts_block}"
        else:
            merged_truth = (
                f"### Key Facts\n\n{new_facts_block}\n\n### Relationships\n\n### Assessment"
            )

        return rewrite_compiled_truth(content, merged_truth)
