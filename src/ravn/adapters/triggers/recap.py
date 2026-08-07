"""RecapTrigger — produce-recap trigger for operator return.

Fires when the operator returns after an absence and surfaces a summary of
what happened while they were away (the "morning greeting" from the Vaka
acceptance scenario).

Two firing modes
----------------
1. **Return detection** — operator interaction within
   ``return_detection_window_seconds`` AND the previous gap was longer than
   ``absence_threshold_seconds``.
2. **Scheduled fallback** — ``scheduled_recap_cron`` fires at the configured
   time (e.g. ``"0 7 * * *"``) even if no absence is detected.

Query
-----
Queries Mímir for threads in ``closed`` state whose ``updated_at`` is after
``last_recap_at``.  Skips firing when nothing has changed (no empty recaps).

State persistence
-----------------
``last_recap_at`` and ``was_away`` are persisted to
``~/.ravn/daemon/recap_state.json`` so state survives daemon restarts.

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

from niuu.domain.mimir import MimirPage, ThreadState
from niuu.ports.mimir import MimirPort
from ravn.config import RecapConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_STATE_FILE_NAME = "recap_state.json"


class RecapTrigger(TriggerPort):
    """TriggerPort that detects operator return and enqueues a recap task.

    Args:
        mimir:            Mímir adapter for querying closed threads.
        config:           Recap configuration.
        last_interaction: Callable that returns the UTC timestamp of the last
                          operator interaction, or ``None`` if never touched.
                          Typically ``tracker.last`` from a shared
                          :class:`~ravn.domain.interaction_tracker.LastInteractionTracker`.
        state_dir:        Directory for persisting recap state.
                          Defaults to ``~/.ravn/daemon``.
    """

    def __init__(
        self,
        mimir: MimirPort,
        config: RecapConfig,
        last_interaction: Callable[[], datetime | None],
        state_dir: Path | None = None,
    ) -> None:
        self._mimir = mimir
        self._config = config
        self._last_interaction = last_interaction
        self._state_dir = state_dir or Path.home() / ".ravn" / "daemon"
        self._last_recap_at: datetime | None = None
        self._was_away: bool = True  # conservative: assume away on startup
        self._last_cron_minute: str = ""  # "YYYY-MM-DDTHH:MM" — prevents double-fire

    @property
    def name(self) -> str:
        return "recap"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        """Poll loop — runs until cancelled by the DriveLoop."""
        if not self._config.enabled:
            logger.info("RecapTrigger: disabled — exiting without polling")
            return

        self._load_state()

        logger.info(
            "RecapTrigger: starting (absence=%ds, window=%ds, poll=%ds)",
            self._config.absence_threshold_seconds,
            self._config.return_detection_window_seconds,
            self._config.poll_interval_seconds,
        )

        while True:
            try:
                await self._poll_once(enqueue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("RecapTrigger: poll error: %s", exc)

            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        """Single poll cycle: check return / schedule, optionally enqueue recap."""
        now = datetime.now(UTC)

        fired_on_return = await self._check_return(enqueue, now)
        if fired_on_return:
            return

        await self._check_schedule(enqueue, now)

    async def _check_return(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
        now: datetime,
    ) -> bool:
        """Detect operator return after absence; enqueue recap if warranted.

        Returns True when a recap was enqueued (caller can skip cron check).
        """
        last = self._last_interaction()
        if last is None:
            return False

        time_since = (now - last).total_seconds()

        if time_since > self._config.absence_threshold_seconds:
            if not self._was_away:
                logger.debug("RecapTrigger: operator went away (gap=%.0fs)", time_since)
            self._was_away = True
            self._save_state()
            return False

        if time_since > self._config.return_detection_window_seconds:
            # Active but within neither the "away" nor the "just returned" window —
            # normal interaction, no state change needed.
            return False

        # Operator is within the return window.
        if not self._was_away:
            return False

        # Operator just returned after an absence.
        logger.info("RecapTrigger: operator returned after absence — checking for recap content")
        self._was_away = False
        enqueued = await self._try_enqueue_recap(enqueue, now, triggered_by="recap:return")
        self._save_state()
        return enqueued

    async def _check_schedule(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
        now: datetime,
    ) -> None:
        """Fire a scheduled recap if the cron expression matches *now*."""
        if not self._config.scheduled_recap_cron:
            return

        try:
            from ravn.adapters.triggers.cron import _cron_matches  # noqa: PLC0415
        except ImportError:
            logger.warning("RecapTrigger: cron module unavailable — skipping scheduled check")
            return

        if not _cron_matches(self._config.scheduled_recap_cron, now):
            return

        # Deduplicate within the same minute.
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        if minute_key == self._last_cron_minute:
            return

        self._last_cron_minute = minute_key
        logger.info(
            "RecapTrigger: scheduled recap firing (cron=%r)",
            self._config.scheduled_recap_cron,
        )
        enqueued = await self._try_enqueue_recap(enqueue, now, triggered_by="recap:schedule")
        if enqueued:
            self._save_state()

    async def _try_enqueue_recap(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
        now: datetime,
        *,
        triggered_by: str,
    ) -> bool:
        """Query Mímir for recap content and enqueue if there is something to report.

        Returns True when a task was enqueued.
        """
        threads = await self._fetch_closed_threads()
        if not threads:
            logger.info("RecapTrigger: no closed threads since last recap — skipping")
            return False

        thread_lines = "\n".join(f"- {t.meta.title} ({t.meta.path})" for t in threads)
        since_str = self._last_recap_at.isoformat() if self._last_recap_at else "the beginning"

        initiative_context = (
            f"Recap window: since {since_str}\n"
            f"\n"
            f"Closed threads ({len(threads)}):\n"
            f"{thread_lines}\n"
            f"\n"
            f"Use mimir_search and mimir_read to gather details about each thread "
            f"and any artifacts they produced.  Assemble a concise operator-facing "
            f"recap following the produce-recap persona instructions.\n"
        )

        task_id = f"task_{int(time.time() * 1000):x}_recap"
        task = AgentTask(
            task_id=task_id,
            title="Morning recap",
            initiative_context=initiative_context,
            triggered_by=triggered_by,
            output_mode=OutputMode.SURFACE,
            priority=1,
            persona=self._config.persona,
        )

        logger.info(
            "RecapTrigger: enqueuing recap task (threads=%d, since=%s)",
            len(threads),
            since_str,
        )
        await enqueue(task)
        self._last_recap_at = now
        return True

    async def _fetch_closed_threads(self) -> list[MimirPage]:
        """Return closed Mímir threads updated since ``last_recap_at``."""
        try:
            threads = await self._mimir.list_threads(
                state=ThreadState.closed,
                limit=self._config.max_threads_in_recap,
            )
        except NotImplementedError:
            logger.warning("RecapTrigger: mimir.list_threads not implemented — skipping")
            return []
        except Exception as exc:
            logger.warning("RecapTrigger: failed to list threads: %s", exc)
            return []

        if self._last_recap_at is None:
            return threads

        return [t for t in threads if t.meta.updated_at > self._last_recap_at]

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted recap state from the state file."""
        state_file = self._state_dir / _STATE_FILE_NAME
        if not state_file.exists():
            return
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if "last_recap_at" in raw:
                self._last_recap_at = datetime.fromisoformat(raw["last_recap_at"])
            if "was_away" in raw:
                self._was_away = bool(raw["was_away"])
        except Exception as exc:
            logger.warning("RecapTrigger: could not load state: %s", exc)

    def _save_state(self) -> None:
        """Persist recap state to the state file."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self._state_dir / _STATE_FILE_NAME
        state: dict = {"was_away": self._was_away}
        if self._last_recap_at is not None:
            state["last_recap_at"] = self._last_recap_at.isoformat()
        try:
            state_file.write_text(json.dumps(state), encoding="utf-8")
        except Exception as exc:
            logger.warning("RecapTrigger: could not save state: %s", exc)
