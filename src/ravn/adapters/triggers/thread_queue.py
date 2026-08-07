"""ThreadQueueTrigger — connects the Mímir thread queue to the Ravn drive loop.

Polls ``MimirPort.get_thread_queue()`` on a configurable interval and converts
the highest-weight open thread into an ``AgentTask`` for the drive loop.

Ownership semantics
-------------------
Before enqueuing, the trigger atomically claims the thread via
``assign_thread_owner``.  If another Ravn instance has already claimed the
thread (``ThreadOwnershipError``), this cycle is skipped — the thread
reappears on the next poll, so no retry logic is needed.

Output mode
-----------
All wakefulness tasks use ``OutputMode.AMBIENT``: work produced during
wakefulness is published to Sleipnir rather than surfaced directly to the
operator.  The Recap (M3) surfaces results on the operator's return.

Enabled flag
------------
``ThreadConfig.enabled`` defaults to ``False`` — the trigger is registered in
M1 but never fires.  M2 flips the switch via ``thread.enabled: true`` in the
deployment YAML.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from niuu.domain.mimir import ThreadOwnershipError, ThreadState
from niuu.ports.mimir import MimirPort
from ravn.config import ThreadConfig
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

# DriveLoop priority range (inclusive): 1 = highest urgency, 10 = lowest.
_PRIORITY_MIN = 1
_PRIORITY_MAX = 10

# Keyword → persona mapping for wakefulness action shapes.
# Keywords are matched case-insensitively against the next_action_hint.
# Order matters: first match wins.
_PERSONA_KEYWORDS: list[tuple[str, list[str]]] = [
    ("draft-a-note", ["draft", "note", "capture", "observe"]),
]

_DEFAULT_PERSONA = "research-and-distill"


class ThreadQueueTrigger(TriggerPort):
    """TriggerPort that polls the Mímir thread queue and enqueues wakefulness tasks.

    Args:
        mimir:  The Mímir adapter to poll.
        config: Thread enrichment configuration (poll interval, owner_id, …).
    """

    def __init__(self, mimir: MimirPort, config: ThreadConfig) -> None:
        self._mimir = mimir
        self._config = config
        self._owner_id: str = config.owner_id or _generate_owner_id()

    @property
    def name(self) -> str:
        return "thread_queue"

    async def run(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        """Poll loop — runs until cancelled by the DriveLoop."""
        logger.info(
            "ThreadQueueTrigger: starting (enabled=%s, poll_interval=%ds, owner=%r)",
            self._config.enabled,
            self._config.enricher_poll_interval_seconds,
            self._owner_id,
        )

        while True:
            try:
                await self._poll_once(enqueue)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ThreadQueueTrigger: poll error: %s", exc)

            await asyncio.sleep(self._config.enricher_poll_interval_seconds)

    async def _poll_once(
        self,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        """Single poll cycle: fetch queue → claim → transition → enqueue.

        No-ops when ``config.enabled`` is ``False`` — the trigger exists in M1
        but never fires until M2 sets ``thread.enabled: true``.
        """
        if not self._config.enabled:
            return

        queue = await self._mimir.get_thread_queue(owner_id=self._owner_id, limit=1)
        if not queue:
            return

        thread = queue[0]
        path = thread.meta.path
        weight = thread.meta.thread_weight or 1.0

        try:
            await self._mimir.assign_thread_owner(path, self._owner_id)
        except ThreadOwnershipError as exc:
            logger.debug(
                "ThreadQueueTrigger: thread %r already owned by %r — skipping cycle",
                path,
                exc.current_owner,
            )
            return

        await self._mimir.update_thread_state(path, ThreadState.pulling)

        # For thread MimirPages, meta.summary stores the next_action_hint
        # (populated by MarkdownMimirAdapter._schema_to_page and _parse_thread_page).
        next_action_hint = thread.meta.summary or ""
        thread_title = thread.meta.title or _title_from_path(path)
        title = next_action_hint or thread_title

        persona = _select_persona(next_action_hint)

        if persona == "research-and-distill":
            action_instruction = (
                "Read relevant sources, synthesise findings, and write a distilled Mímir page "
                "under research/ that answers the thread's open question. "
                "Mark the output with produced_by_thread: true."
            )
        else:
            action_instruction = (
                "Draft a concise note capturing the thread's key points. "
                "Mark the output with produced_by_thread: true."
            )

        initiative_context = (
            f"Thread: {path}\n"
            f"Title: {thread_title}\n"
            f"Weight: {weight:.2f}\n"
            f"Next action: {next_action_hint}\n"
            f"\n"
            f"{action_instruction}\n"
        )

        priority = max(_PRIORITY_MIN, min(_PRIORITY_MAX, int(_PRIORITY_MAX - weight)))

        task_id = f"task_{int(time.time() * 1000):x}_tq"
        task = AgentTask(
            task_id=task_id,
            title=title,
            initiative_context=initiative_context,
            triggered_by=f"thread:{path}",
            output_mode=OutputMode.AMBIENT,
            priority=priority,
            persona=persona,
        )
        logger.info(
            "ThreadQueueTrigger: enqueuing task for thread %r (weight=%.2f, priority=%d)",
            path,
            weight,
            priority,
        )
        await enqueue(task)


def _generate_owner_id() -> str:
    """Return a stable owner ID for this process lifetime."""
    return f"ravn-{uuid.uuid4().hex[:8]}"


def _select_persona(hint: str) -> str:
    """Map a next_action_hint to a persona name via keyword matching.

    Iterates ``_PERSONA_KEYWORDS`` in order; returns the first persona whose
    keyword list contains any word present in *hint* (case-insensitive).
    Falls back to ``_DEFAULT_PERSONA`` (``"research-and-distill"``) when no
    keyword matches.
    """
    hint_lower = hint.lower()
    for persona_name, keywords in _PERSONA_KEYWORDS:
        if any(kw in hint_lower for kw in keywords):
            return persona_name
    return _DEFAULT_PERSONA


def _title_from_path(path: str) -> str:
    """Derive a human-readable title from a thread path stem."""
    slug = path.rsplit("/", 1)[-1]
    return slug.replace("-", " ").title()
