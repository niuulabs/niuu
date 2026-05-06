"""MimirStalenessTrigger — enqueues refresh tasks for stale frequently-used pages.

On a configurable schedule, queries ``MimirUsagePort`` for the most-accessed
wiki pages, checks each for staleness via the Mímir adapter, and enqueues a
refresh task for any page whose source content has changed.

Implements ``TriggerPort`` (``ravn.ports.trigger``).

TODO (dynamic trigger loading): This trigger should eventually be loadable
from config YAML using the same fully-qualified class path pattern as adapters:

    mimir:
      triggers:
        - trigger: "ravn.adapters.triggers.mimir_staleness.MimirStalenessTrigger"
          schedule_hours: 6
          top_n: 20

See ``rules/dynamic-adapters.md`` for the pattern.  Until dynamic loading is
implemented, this trigger is wired explicitly in ``cli/commands.py``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from niuu.ports.mimir import MimirPort
from ravn.config import MimirStalenessTriggerConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.mimir_usage import MimirUsagePort
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)


class MimirStalenessTrigger(TriggerPort):
    """TriggerPort implementation that refreshes stale high-priority pages.

    Args:
        mimir:        The Mímir adapter to check staleness against.
        usage:        The usage port that surfaces frequently-accessed pages.
        config:       Staleness trigger configuration (schedule, top_n, persona).
    """

    def __init__(
        self,
        mimir: MimirPort,
        usage: MimirUsagePort,
        config: MimirStalenessTriggerConfig,
    ) -> None:
        self._mimir = mimir
        self._usage = usage
        self._config = config
        self._enqueued: set[str] = set()

    @property
    def name(self) -> str:
        return "mimir_staleness"

    async def run(
        self,
        enqueue: Callable[[AgentTask], Awaitable[None]],
    ) -> None:
        """Schedule loop — fires every ``schedule_hours`` hours until cancelled."""
        interval_seconds = self._config.schedule_hours * 3600
        logger.info(
            "MimirStalenessTrigger: starting (schedule=%dh, top_n=%d, persona=%s)",
            self._config.schedule_hours,
            self._config.top_n,
            self._config.persona,
        )

        while True:
            try:
                await self._check_once(enqueue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("MimirStalenessTrigger: check error: %s", exc)

            await asyncio.sleep(interval_seconds)

    async def _check_once(
        self,
        enqueue: Callable[[AgentTask], Awaitable[None]],
    ) -> None:
        top = await self._usage.top_pages(self._config.top_n)
        if not top:
            logger.debug("MimirStalenessTrigger: no usage data — skipping")
            return

        pages = await self._mimir.list_pages()
        page_meta = {p.path: p for p in pages}

        for path, _count in top:
            meta = page_meta.get(path)
            if meta is None:
                continue

            # Check staleness for each source_id referenced by this page
            stale_sources: list[str] = []
            for source_id in meta.source_ids:
                try:
                    lint = await self._mimir.lint()
                    if any(i.id == "L08" and i.page_path == path for i in lint.issues):
                        stale_sources.append(source_id)
                        break
                except Exception as exc:
                    logger.warning("MimirStalenessTrigger: lint check failed for %r: %s", path, exc)
                    break

            if not stale_sources:
                continue

            dedup_key = f"{path}:{','.join(sorted(stale_sources))}"
            if dedup_key in self._enqueued:
                continue
            self._enqueued.add(dedup_key)

            context = (
                f"A frequently-used Mímir wiki page has become stale and needs refreshing.\n\n"
                f"Page: {path}\n"
                f"Title: {meta.title}\n"
                f"Stale source IDs: {', '.join(stale_sources)}\n\n"
                f"Steps:\n"
                f"1. Call mimir_read to read the current page content.\n"
                f"2. Review the source IDs in the page footer.\n"
                f"3. Check the raw/ directory for the latest source content.\n"
                f"4. Update the page with any changed facts using mimir_write.\n"
                f"5. Update the log entry."
            )

            safe_path = path.replace("/", "_").replace(".", "_")
            task_id = f"task_{int(time.time() * 1000):x}_{safe_path[:16]}"
            task = AgentTask(
                task_id=task_id,
                title=f"Refresh stale Mímir page: {meta.title}",
                initiative_context=context,
                triggered_by=self.name,
                output_mode=OutputMode.SILENT,
                persona=self._config.persona,
                priority=9,
                max_tokens=self._config.max_tokens,
            )
            logger.info("MimirStalenessTrigger: enqueuing refresh for stale page %r", path)
            await enqueue(task)
