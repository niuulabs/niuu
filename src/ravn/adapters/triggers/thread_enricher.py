"""ThreadEnricher — tags new Mímir pages as open threads.

On each poll cycle, fetches pages updated since the last check, applies
eligibility rules, and uses a cheap LLM call to classify each candidate.
Pages classified as threads have their thread fields written back via
``MimirPort.upsert_page()``.

Cascade prevention
------------------
The enricher guards against re-processing its own output with three rules:

1. Pages in ``threads/`` are never scanned.
2. Pages whose ``thread_state`` is already set are skipped (already classified).
3. Pages with ``produced_by_thread=True`` are skipped (artifacts written by
   action shapes in M2+).

Source-type guard
-----------------
Pages ingested from ``tool_output`` or ``research`` sources are never threads —
these are artifacts, not unfinished business.

M1 note
-------
The ``enqueue`` callback passed to ``run()`` is NOT used in M1 — only
classification and metadata writing happen here.  M2 will wire the callback
to forward high-weight threads into the wakefulness queue.

Implements :class:`~ravn.ports.trigger.TriggerPort`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from niuu.domain.mimir import MimirPage, MimirPageMeta, ThreadContextRef, ThreadState
from niuu.ports.mimir import MimirPort
from ravn.config import ThreadConfig
from ravn.domain.models import AgentTask
from ravn.domain.thread_weight import ThreadWeightConfig, ThreadWeightSignals, compute_weight
from ravn.ports.llm import LLMPort
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_INELIGIBLE_SOURCE_TYPES: frozenset[str] = frozenset({"tool_output", "research"})

_CLASSIFICATION_PROMPT = """\
You are classifying a Mímir wiki page as unfinished business or not.

Page title: {title}
Page summary: {summary}
Page content (first 500 chars): {content}

Respond with JSON:
{{
  "is_thread": true | false,
  "confidence": 0.0-1.0,
  "next_action_hint": "one sentence describing what to do next, or null"
}}

Return is_thread=true only for open questions, follow-ups, or unfinished tasks.
Facts, completed work, and reference material are NOT threads.\
"""

_STATE_FILE_NAME = "thread_enricher_state.json"

# Maximum number of tokens for the LLM classification call.
_CLASSIFICATION_MAX_TOKENS = 256


class ThreadEnricher(TriggerPort):
    """TriggerPort that classifies new Mímir pages as open threads.

    On each poll cycle the enricher:

    1. Calls ``mimir.list_pages()`` to get all pages.
    2. Filters to pages updated since ``last_checked_at`` and eligible for
       classification (not already threads, not artifacts, not in threads/).
    3. Calls a cheap LLM for each eligible page.
    4. Writes thread fields back via ``mimir.upsert_page()`` for hits above
       the configured confidence threshold.
    5. Persists ``last_checked_at`` to ``~/.ravn/daemon/thread_enricher_state.json``.

    The ``enqueue`` callback is NOT used in M1.

    Args:
        mimir:      Mímir adapter for page reads and writes.
        llm:        LLM adapter used for page classification.
        config:     Thread enrichment configuration.
        state_dir:  Directory for persisting ``last_checked_at``.  Defaults to
                    ``~/.ravn/daemon``.
    """

    def __init__(
        self,
        mimir: MimirPort,
        llm: LLMPort,
        config: ThreadConfig,
        state_dir: Path | None = None,
    ) -> None:
        self._mimir = mimir
        self._llm = llm
        self._config = config
        self._state_dir = state_dir or Path.home() / ".ravn" / "daemon"
        self._last_checked_at: datetime | None = None

    @property
    def name(self) -> str:
        return "thread_enricher"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Poll Mímir forever on the configured interval.

        Exits immediately (without polling) when ``config.enabled`` is False.
        Raises :exc:`asyncio.CancelledError` on task cancellation.
        """
        if not self._config.enabled:
            logger.info("ThreadEnricher: disabled — exiting without polling")
            return

        self._last_checked_at = self._load_state()

        logger.info(
            "ThreadEnricher: starting (interval=%ds, threshold=%.2f)",
            self._config.enricher_poll_interval_seconds,
            self._config.confidence_threshold,
        )

        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ThreadEnricher: poll error: %s", exc)

            await asyncio.sleep(self._config.enricher_poll_interval_seconds)

    async def _poll_once(self) -> None:
        """Execute a single enrichment sweep."""
        now = datetime.now(UTC)

        pages = await self._mimir.list_pages()

        # Pre-filter without source type information — avoids a list_sources()
        # round-trip when there is nothing new to process.
        candidates = [
            p
            for p in pages
            if not p.path.startswith("threads/")
            and p.thread_state is None
            and not p.is_thread
            and not p.produced_by_thread
            and (self._last_checked_at is None or p.updated_at > self._last_checked_at)
        ]

        if not candidates:
            self._last_checked_at = now
            self._save_state(now)
            return

        source_metas = await self._mimir.list_sources()
        source_type_map: dict[str, str] = {s.source_id: s.source_type for s in source_metas}

        for page_meta in candidates:
            if not self._is_eligible(page_meta, source_type_map):
                # source-type guard may still exclude this candidate
                continue

            try:
                page = await self._mimir.get_page(page_meta.path)
                await self._classify_and_tag(page, source_type_map)
            except Exception as exc:
                logger.warning(
                    "ThreadEnricher: error processing page %r: %s",
                    page_meta.path,
                    exc,
                )

        self._last_checked_at = now
        self._save_state(now)

    def _is_eligible(
        self,
        meta: MimirPageMeta,
        source_type_map: dict[str, str],
    ) -> bool:
        """Return True when *meta* should be sent to the LLM for classification."""
        if meta.path.startswith("threads/"):
            return False

        if meta.thread_state is not None:
            return False

        if meta.is_thread:
            return False

        if meta.produced_by_thread:
            return False

        if self._last_checked_at is not None and meta.updated_at <= self._last_checked_at:
            return False

        for source_id in meta.source_ids:
            if source_type_map.get(source_id) in _INELIGIBLE_SOURCE_TYPES:
                return False

        return True

    async def _classify_and_tag(
        self,
        page: MimirPage,
        source_type_map: dict[str, str],
    ) -> None:
        """Classify *page* and write thread fields back if it qualifies."""
        result = await self._call_llm(page)
        if result is None:
            return

        if not result.get("is_thread", False):
            return

        confidence = float(result.get("confidence", 0.0))
        if confidence < self._config.confidence_threshold:
            return

        operator_engagement = 1 if self._has_conversation_source(page.meta, source_type_map) else 0

        signals = ThreadWeightSignals(
            age_days=0.0,
            mention_count=0,
            operator_engagement_count=operator_engagement,
            peer_interest_count=0,
            sub_thread_count=0,
        )
        weight_config = ThreadWeightConfig(
            decay_rate_per_day=self._config.decay_rate_per_day,
            recency_weight=self._config.recency_weight,
            mention_weight=self._config.mention_weight,
            engagement_weight=self._config.engagement_weight,
            peer_weight=self._config.peer_weight,
            sub_thread_weight=self._config.sub_thread_weight,
        )
        weight = compute_weight(signals, weight_config)

        page.meta.thread_state = ThreadState.open
        page.meta.thread_weight = weight
        page.meta.is_thread = True
        page.meta.thread_weight_signals = asdict(signals)
        page.meta.thread_next_action_hint = result.get("next_action_hint")
        page.meta.thread_context_refs = [
            ThreadContextRef(ref_type="ingest", ref_id=sid, ref_summary="")
            for sid in page.meta.source_ids
        ]

        await self._mimir.upsert_page(page.meta.path, page.content, meta=page.meta)
        logger.info(
            "ThreadEnricher: tagged %r as thread (weight=%.3f, confidence=%.2f)",
            page.meta.path,
            weight,
            confidence,
        )

    def _has_conversation_source(
        self,
        meta: MimirPageMeta,
        source_type_map: dict[str, str],
    ) -> bool:
        return any(source_type_map.get(sid) == "conversation" for sid in meta.source_ids)

    async def _call_llm(self, page: MimirPage) -> dict | None:
        """Call the LLM classifier.  Returns parsed JSON or None on failure."""
        prompt = _CLASSIFICATION_PROMPT.format(
            title=page.meta.title,
            summary=page.meta.summary,
            content=page.content[:500],
        )
        try:
            response = await self._llm.generate(
                messages=[{"role": "user", "content": prompt}],
                tools=[],
                system="You are a page classification assistant. Respond only with valid JSON.",
                model=self._config.enricher_llm_alias,
                max_tokens=_CLASSIFICATION_MAX_TOKENS,
            )
            return json.loads(response.content)
        except Exception as exc:
            logger.warning(
                "ThreadEnricher: LLM classification failed for %r: %s",
                page.meta.title,
                exc,
            )
            return None

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> datetime | None:
        """Load persisted ``last_checked_at`` from the state file."""
        state_file = self._state_dir / _STATE_FILE_NAME
        if not state_file.exists():
            return None
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            return datetime.fromisoformat(raw["last_checked_at"])
        except Exception as exc:
            logger.warning("ThreadEnricher: could not load state: %s", exc)
            return None

    def _save_state(self, last_checked_at: datetime) -> None:
        """Persist ``last_checked_at`` to the state file."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self._state_dir / _STATE_FILE_NAME
        try:
            state_file.write_text(
                json.dumps({"last_checked_at": last_checked_at.isoformat()}),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("ThreadEnricher: could not save state: %s", exc)
