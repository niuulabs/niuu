"""MimirSourceTrigger — enqueues synthesis tasks for unprocessed raw sources.

Polls the Mímir adapter for raw sources that have been ingested but not yet
referenced in any wiki page.  For each unprocessed source, a synthesis task
is enqueued for the mimir-curator persona.

Implements ``TriggerPort`` (``ravn.ports.trigger``).

The trigger is composed explicitly by the Ravn daemon because it shares the
live Mimir port and drive-loop queue with sibling triggers. Its behavior is fully
typed by ``MimirSourceTriggerConfig``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from niuu.domain.mimir import OPERATIONAL_SOURCE_TYPES
from niuu.ports.mimir import MimirPort
from ravn.config import MimirSourceTriggerConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)


def _source_excerpt(content: str, max_chars: int) -> str:
    """Return a bounded excerpt for source-synthesis task context."""
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    omitted = len(content) - max_chars
    return (
        f"{content[:max_chars]}\n\n"
        f"[Source content truncated: omitted {omitted:,} characters from the raw source.]"
    )


class MimirSourceTrigger(TriggerPort):
    """TriggerPort implementation that synthesises unprocessed Mímir sources.

    Args:
        mimir:  The Mímir adapter to poll for unprocessed sources.
        config: Source trigger configuration (poll interval, persona).
    """

    def __init__(self, mimir: MimirPort, config: MimirSourceTriggerConfig) -> None:
        self._mimir = mimir
        self._config = config
        # source_id → time it was enqueued; cleared after retry_after_seconds
        # so failed tasks are automatically retried on the next eligible poll.
        self._enqueued: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "mimir_source"

    async def run(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        """Poll loop — runs until cancelled by the DriveLoop."""
        logger.info(
            "MimirSourceTrigger: starting (poll_interval=%ds, persona=%s)",
            self._config.poll_interval_seconds,
            self._config.persona,
        )

        while True:
            try:
                await self._poll_once(enqueue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MimirSourceTrigger: poll error: %s", exc)

            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        now = time.monotonic()
        retry_after = self._config.retry_after_seconds

        sources = await self._mimir.list_sources(unprocessed_only=True)
        # Oldest first, so a backlog drains in the order it accumulated instead
        # of being re-shuffled by directory order on every sweep.
        sources = sorted(sources, key=lambda meta: meta.ingested_at)

        for src in sources:
            if src.source_type in OPERATIONAL_SOURCE_TYPES:
                # The port contract excludes operational exhaust from
                # unprocessed listings, but a foreign mount may not honour it —
                # without this guard one such mount would put the sweep back on
                # a backlog it can never finish.
                continue

            enqueued_at = self._enqueued.get(src.source_id)
            if enqueued_at is not None and (now - enqueued_at) < retry_after:
                continue

            # Bounded read: the context below truncates to max_content_chars
            # anyway, and raw sources reach several megabytes.
            full_source = await self._mimir.read_source_excerpt(
                src.source_id,
                self._config.max_content_chars,
            )
            source_content = (
                _source_excerpt(full_source.content, self._config.max_content_chars)
                if full_source is not None
                else None
            )
            content_section = (
                f"\n\n## Source content\n\n{source_content}"
                if source_content is not None
                else "\n\n(Content unavailable — check raw/ directory manually.)"
            )

            context = (
                f"A new raw source has been ingested into Mímir and requires synthesis.\n\n"
                f"Source ID: {src.source_id}\n"
                f"Title: {src.title}\n"
                f"Type: {src.source_type}\n"
                f"Ingested: {src.ingested_at.isoformat()}\n\n"
                f"Synthesis workflow:\n"
                f"1. Call mimir_query on the source topic to find existing pages.\n"
                f"2. Ingest is already done (source_id: {src.source_id}).\n"
                f"3. Read the source content below and synthesise wiki pages.\n"
                f"4. Optionally run 1-2 targeted web searches if recency matters.\n"
                f"5. Call mimir_write to write or update each synthesised page. Every page\n"
                f"   MUST include a footer HTML comment whose body is `sources: {src.source_id}`.\n"
                f"   If a page already exists but lacks this source_id, call mimir_write\n"
                f"   to update it — do not skip synthesis because pages already exist.\n"
                f"6. Cross-link related pages, update wiki/index.md, append to wiki/log.md."
                f"{content_section}"
            )

            task_id = f"task_{int(time.time() * 1000):x}_{src.source_id[:8]}"
            task = AgentTask(
                task_id=task_id,
                title=f"Synthesise Mímir source: {src.title}",
                initiative_context=context,
                triggered_by=self.name,
                output_mode=OutputMode.SILENT,
                persona=self._config.persona,
                priority=8,
                max_tokens=self._config.max_tokens,
            )
            mount_tag = f" [mount={src.mount_name}]" if src.mount_name else ""
            logger.info(
                "MimirSourceTrigger: enqueuing synthesis for source %r (%s)%s",
                src.source_id,
                src.title,
                mount_tag,
            )
            if not await enqueue(task):
                # The drive loop is at capacity. Everything after this would be
                # discarded too, so stop the sweep rather than read and build
                # tasks for the rest of the backlog only to throw them away.
                logger.info(
                    "MimirSourceTrigger: queue is full — deferring source %r "
                    "and the rest of this sweep to the next poll",
                    src.source_id,
                )
                return

            # Marked only once accepted: a task the drive loop refused was never
            # given a chance to run, so suppressing its retry would strand it.
            self._enqueued[src.source_id] = now
