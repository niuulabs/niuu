"""DreamCycleTrigger — nightly Mímir enrichment, lint, and cross-reference.

Fires the ``mimir-curator`` persona on a cron schedule (default: 3 am daily)
to run an end-to-end knowledge-base maintenance pass:

1. Query Mímir log entries since the last dream cycle.
2. Detect entities in new/modified raw sources (if not done by ingest).
3. Update compiled truth pages where new evidence changes understanding.
4. Auto-fix safe lint issues via ``mimir_lint --fix``.
5. Cross-reference pages that mention the same entities without links.
6. Write a dream cycle log entry to ``wiki/log.md``.
7. Emit a ``mimir.dream.completed`` Sleipnir event with summary counts.

State is persisted to ``<state_dir>/dream_cycle_state.json`` so the
``last_dream_at`` timestamp survives daemon restarts.  Running the dream
cycle twice within the same cron minute is deduplicated.

Implements :class:`~ravn.ports.trigger.TriggerPort`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from ravn.config import DreamCycleTriggerConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_STATE_FILE_NAME = "dream_cycle_state.json"


class DreamCycleTrigger(TriggerPort):
    """TriggerPort that fires a nightly dream cycle for the Mímir knowledge base.

    Args:
        config:    Dream cycle trigger configuration.
        state_dir: Directory for persisting ``last_dream_at`` state.
                   Defaults to ``config.state_dir`` (``~/.ravn/daemon``).
    """

    def __init__(
        self,
        config: DreamCycleTriggerConfig,
        state_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._state_dir = state_dir or Path(config.state_dir).expanduser()
        self._last_dream_at: datetime | None = None
        self._last_cron_minute: str = ""

    @property
    def name(self) -> str:
        return "dream_cycle"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Poll loop — runs until cancelled by the DriveLoop."""
        if not self._config.enabled:
            logger.info("DreamCycleTrigger: disabled — exiting without polling")
            return

        self._load_state()

        logger.info(
            "DreamCycleTrigger: starting (cron=%r, persona=%r, poll=%ds)",
            self._config.cron_expression,
            self._config.persona,
            self._config.poll_interval_seconds,
        )

        while True:
            try:
                await self._poll_once(enqueue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("DreamCycleTrigger: poll error: %s", exc)

            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Single poll cycle: check cron schedule, optionally enqueue dream cycle."""
        now = datetime.now(UTC)

        if not self._is_due(now):
            return

        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        if minute_key == self._last_cron_minute:
            return

        self._last_cron_minute = minute_key
        logger.info(
            "DreamCycleTrigger: cron expression %r matched — enqueueing dream cycle",
            self._config.cron_expression,
        )

        await self._enqueue_dream_cycle(enqueue, now)
        self._last_dream_at = now
        self._save_state()

    def _is_due(self, now: datetime) -> bool:
        """Return True when the configured cron expression matches *now*."""
        try:
            from ravn.adapters.triggers.cron import _cron_matches  # noqa: PLC0415
        except ImportError:
            logger.warning("DreamCycleTrigger: cron module unavailable — skipping check")
            return False

        return _cron_matches(self._config.cron_expression, now)

    async def _enqueue_dream_cycle(
        self,
        enqueue: Callable[[AgentTask], Awaitable[None]],
        now: datetime,
    ) -> None:
        """Build the dream cycle initiative context and enqueue an AgentTask."""
        since_str = self._last_dream_at.isoformat() if self._last_dream_at else "the beginning"

        initiative_context = (
            f"Dream cycle run — {now.isoformat()}\n"
            f"Last run: {since_str}\n"
            f"Token budget: ~${self._config.token_budget_usd:.2f} USD\n"
            f"\n"
            f"## Dream cycle steps\n"
            f"\n"
            f"Complete **all** steps in order.  Only process sources and pages "
            f"modified or created since {since_str} (use timestamps to filter). "
            f"If a step produces no changes, note that and continue.\n"
            f"\n"
            f"**Step 1 — Scan log**\n"
            f"Call `mimir_read` on `wiki/log.md` and identify all sources and pages "
            f"ingested or modified since {since_str}.\n"
            f"\n"
            f"**Step 2 — Entity detection**\n"
            f"For each new or modified raw source found in Step 1, call `mimir_ingest` "
            f"(skip if the source was already ingested by the ingest pipeline).  "
            f"Count the number of new entities created.\n"
            f"\n"
            f"**Step 3 — Compiled truth audit**\n"
            f"For each entity from Step 2, call `mimir_search` to find related compiled "
            f"truth pages.  Determine whether new evidence changes the current understanding.\n"
            f"\n"
            f"**Step 4 — Compiled truth update**\n"
            f"For each page that requires an update, call `mimir_write` to rewrite the "
            f"affected sections.  Keep prior timeline entries intact; only update the "
            f"Compiled Truth zone.  Count pages updated.\n"
            f"\n"
            f"**Step 5 — Lint with auto-fix**\n"
            f"Call `mimir_lint` with `fix=true` to auto-fix safe issues (L05, L11, L12).  "
            f"Record the number of fixes applied.\n"
            f"\n"
            f"**Step 6 — Cross-reference**\n"
            f"Search for pages that mention the same entities as the updated pages but "
            f"lack wikilinks to them.  Add the missing links via `mimir_write`.\n"
            f"\n"
            f"**Step 7 — Log and emit**\n"
            f"Append a dream cycle summary entry to `wiki/log.md` with: timestamp, "
            f"pages_updated count, entities_created count, lint_fixes count.\n"
            f"Then call `sleipnir_publish` to emit a `mimir.dream.completed` event "
            f"with those three counts.\n"
            f"\n"
            f"Stay within the token budget.  If budget is running low before all steps "
            f"are done, skip Step 6, complete Steps 7, and note which steps were skipped.\n"
        )

        task_id = f"task_{int(time.time() * 1000):x}_dream_cycle"
        task = AgentTask(
            task_id=task_id,
            title=self._config.task_description,
            initiative_context=initiative_context,
            triggered_by="dream_cycle:cron",
            output_mode=OutputMode.SILENT,
            priority=10,
            persona=self._config.persona,
        )

        logger.info(
            "DreamCycleTrigger: enqueueing dream cycle task (task_id=%s, since=%s)",
            task_id,
            since_str,
        )
        await enqueue(task)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted dream cycle state from the state file."""
        state_file = self._state_dir / _STATE_FILE_NAME
        if not state_file.exists():
            return
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if "last_dream_at" in raw:
                self._last_dream_at = datetime.fromisoformat(raw["last_dream_at"])
        except Exception as exc:
            logger.warning("DreamCycleTrigger: could not load state: %s", exc)

    def _save_state(self) -> None:
        """Persist dream cycle state to the state file."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self._state_dir / _STATE_FILE_NAME
        state: dict = {}
        if self._last_dream_at is not None:
            state["last_dream_at"] = self._last_dream_at.isoformat()
        try:
            state_file.write_text(json.dumps(state), encoding="utf-8")
        except Exception as exc:
            logger.warning("DreamCycleTrigger: could not save state: %s", exc)
