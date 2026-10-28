"""DreamCycleTrigger — nightly Mímir enrichment, lint, and cross-reference.

Fires the ``mimir-curator`` persona on a cron schedule (default: 3 am daily)
to run an end-to-end knowledge-base maintenance pass:

1. Query Mímir log entries since the last dream cycle.
2. Detect entities in new/modified raw sources (if not done by ingest).
3. Update compiled truth pages where new evidence changes understanding.
4. Auto-fix safe lint issues via ``mimir_lint --fix``.
5. Cross-reference pages that mention the same entities without links.
6. Write a dream cycle log entry to ``wiki/log.md``.
7. Return summary counts; the drive loop emits ``mimir.dream.completed``.

State is persisted to ``<state_dir>/dream_cycle_state.json``: ``last_dream_at``
and ``last_checked_at``, the last minute the schedule was evaluated.  Each poll
evaluates every minute since ``last_checked_at``, so a scheduled run is never
skipped because the resident was restarting at that minute or a poll slept past
it; a run missed that way fires on the first poll after it.  Several missed runs
collapse into one dream, which covers everything since ``last_dream_at``.

The schedule is validated when the trigger is built and a state file that
cannot be read or written raises: a resident that silently stops dreaming
looks healthy while it stops consolidating what it learned.

Implements :class:`~ravn.ports.trigger.TriggerPort`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ravn.adapters.triggers.cron import _cron_matches, validate_cron_fields
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
        if config.enabled:
            validate_cron_fields(config.cron_expression)
        self._config = config
        self._state_dir = state_dir or Path(config.state_dir).expanduser()
        self._last_dream_at: datetime | None = None
        self._last_checked_at: datetime | None = None

    @property
    def name(self) -> str:
        return "dream_cycle"

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
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
            await self._poll_once(enqueue)
            await asyncio.sleep(self._config.poll_interval_seconds)

    async def _poll_once(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        """Enqueue a dream when a scheduled minute passed since the last check."""
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        if self._last_checked_at is None:
            # No state: the schedule starts now, so only this minute can be due.
            self._last_checked_at = now - timedelta(minutes=1)

        scheduled_at = self._scheduled_run(self._last_checked_at, now)
        if scheduled_at is None:
            self._last_checked_at = now
            self._save_state()
            return

        if scheduled_at < now:
            logger.info(
                "DreamCycleTrigger: the run scheduled for %s was missed (the resident "
                "was not polling then) — running it now",
                scheduled_at.isoformat(),
            )
        logger.info(
            "DreamCycleTrigger: cron expression %r matched %s — enqueueing dream cycle",
            self._config.cron_expression,
            scheduled_at.isoformat(),
        )
        accepted = await self._enqueue_dream_cycle(enqueue, now)
        self._last_checked_at = now
        if accepted:
            self._last_dream_at = now
        else:
            logger.info(
                "DreamCycleTrigger: a dream cycle is already queued or running; it "
                "covers the run scheduled for %s",
                scheduled_at.isoformat(),
            )
        self._save_state()

    def _scheduled_run(self, after: datetime, upto: datetime) -> datetime | None:
        """Return the latest minute in ``(after, upto]`` the schedule matches.

        Walks back one minute at a time from ``upto``; the span is the time
        since the last poll (or since the last dream for state written before
        ``last_checked_at`` existed), so it is bounded by real elapsed time.
        """
        minute = upto
        while minute > after:
            if _cron_matches(self._config.cron_expression, minute):
                return minute
            minute -= timedelta(minutes=1)
        return None

    async def _enqueue_dream_cycle(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
        now: datetime,
    ) -> bool:
        """Build the dream cycle task and return whether the drive loop accepted it."""
        since_str = self._last_dream_at.isoformat() if self._last_dream_at else "the beginning"
        warden_id = self._state_dir.parent.name or "mimir"
        environment_line = self._config.environment_id or "private"

        initiative_context = (
            f"Dream cycle run — {now.isoformat()}\n"
            f"Last run: {since_str}\n"
            f"Token budget: ~${self._config.token_budget_usd:.2f} USD\n"
            f"Autonomy mode: {self._config.autonomy_mode}\n"
            f"Environment: {environment_line}\n"
            f"Proposal store: {self._config.proposal_store_path}\n"
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
            f"**Step 7 — Log and finish**\n"
            f"Append a dream cycle summary entry to `wiki/log.md` with: timestamp, "
            f"`ravn={warden_id}`, pages_updated count, entities_created count, "
            f"lint_fixes count.\n"
            f"Do not call `sleipnir_publish`; the runner emits `mimir.dream.completed` "
            f"automatically from your final outcome. Include `pages_updated`, "
            f"`entities_created`, and `lint_fixes` as integer fields in the outcome block.\n"
            f"\n"
            f"Stay within the token budget.  If budget is running low before all steps "
            f"are done, skip Step 6, complete Steps 7, and note which steps were skipped.\n"
            f"\n"
            f"## Self-improvement policy\n"
            f"\n"
            f"When this dream cycle discovers a better private or Environment-scoped "
            f"skill/tool routine, call `skill_manage` with `autonomy_mode`, "
            f"`environment_id`, and `proposal_store_path` exactly as listed above. "
            f"Do not directly mutate global doctrine, bundled personas, credentials, "
            f"spending limits, authority boundaries, or external-send behavior; those "
            f"must remain review proposals even in YOLO mode.\n"
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
        return await enqueue(task)

    # ------------------------------------------------------------------
    # State persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load persisted dream cycle state; a missing file means no dream has run.

        :raises ValueError: the state file exists but cannot be parsed.
        """
        state_file = self._state_dir / _STATE_FILE_NAME
        if not state_file.exists():
            return
        try:
            raw = json.loads(state_file.read_text(encoding="utf-8"))
            if "last_dream_at" in raw:
                self._last_dream_at = datetime.fromisoformat(raw["last_dream_at"])
            if "last_checked_at" in raw:
                self._last_checked_at = datetime.fromisoformat(raw["last_checked_at"])
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"dream cycle state {state_file} is unreadable ({exc}); fix it, or delete "
                "it to restart the schedule from now (the next dream then covers all "
                "lessons since the beginning)"
            ) from exc
        if self._last_checked_at is None and self._last_dream_at is not None:
            # State written before last_checked_at existed: nothing ran since the last dream.
            self._last_checked_at = self._last_dream_at.replace(second=0, microsecond=0)

    def _save_state(self) -> None:
        """Persist dream cycle state, replacing the file atomically."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self._state_dir / _STATE_FILE_NAME
        state: dict = {}
        if self._last_dream_at is not None:
            state["last_dream_at"] = self._last_dream_at.isoformat()
        if self._last_checked_at is not None:
            state["last_checked_at"] = self._last_checked_at.isoformat()
        tmp_file = state_file.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(state), encoding="utf-8")
        tmp_file.replace(state_file)
