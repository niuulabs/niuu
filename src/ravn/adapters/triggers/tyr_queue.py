"""TyrQueueTrigger — polls Tyr's dispatch queue and enqueues ready raids.

Enables Level 4 autonomy: the ravn notices work is available in Tyr's queue
and picks it up without human intervention.

Before enqueuing, the trigger checks the dispatcher state to ensure:
- The dispatcher is running
- auto_continue is enabled
- Active (in-flight) raids are below the max_concurrent_raids cap

Deduplication: each ``issue_id`` is tracked in ``_enqueued_ids``.  Raids
that no longer appear in the queue (dispatched / completed) are cleared from
tracking on the next poll, freeing capacity for new raids.

Configuration (``ravn.yaml``)::

    initiative:
      trigger_adapters:
        - adapter: ravn.adapters.triggers.tyr_queue.TyrQueueTrigger
          kwargs:
            tyr_base_url: "http://tyr:8080"
            poll_interval_s: 30
          secret_kwargs_env:
            pat_token: VOLUNDR_PAT
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx

from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_S = 10.0
_DISPATCHER_PATH = "/api/v1/tyr/dispatcher"
_QUEUE_PATH = "/api/v1/tyr/dispatch/queue"


def _build_initiative_context(item: dict) -> str:
    """Assemble the initiative context from a QueueItemResponse dict."""
    identifier = item.get("identifier", "")
    title = item.get("title", "")
    description = item.get("description", "").strip()
    repos = item.get("repos", [])
    feature_branch = item.get("feature_branch", "")
    phase_name = item.get("phase_name", "")
    saga_name = item.get("saga_name", "")

    lines: list[str] = [
        f"# Raid: {identifier} — {title}",
        "",
    ]

    if saga_name:
        lines.append(f"**Saga:** {saga_name}")
    if phase_name:
        lines.append(f"**Phase:** {phase_name}")
    if repos:
        lines.append(f"**Repositories:** {', '.join(repos)}")
    if feature_branch:
        lines.append(f"**Feature branch:** {feature_branch}")

    if description:
        lines.extend(["", "## Description", "", description])

    lines.extend(
        [
            "",
            "## Instructions",
            "",
                (
                    "Execute this raid to completion. Decompose into coding tasks, "
                    + "delegate to the coding peer via task_create, collect results, "
                    + "run review, and publish your final outcome event."
                ),
        ]
    )

    return "\n".join(lines)


class TyrQueueTrigger(TriggerPort):
    """Polls Tyr dispatch queue, enqueues ready raids as AgentTasks.

    Args:
        tyr_base_url: Base URL for the Tyr service (e.g. ``http://tyr:8080``).
        poll_interval_s: Seconds between queue polls.
        pat_token: Personal access token for authenticating with Tyr.
    """

    def __init__(
        self,
        tyr_base_url: str,
        poll_interval_s: float,
        pat_token: str,
    ) -> None:
        self._tyr_base_url = tyr_base_url.rstrip("/")
        self._poll_interval_s = poll_interval_s
        self._pat_token = pat_token
        self._enqueued_ids: set[str] = set()
        self._counter = 0

    @property
    def name(self) -> str:
        return "tyr_queue"

    def _make_task_id(self, identifier: str) -> str:
        self._counter += 1
        hex_ts = hex(int(time.time() * 1000))[2:]
        return f"tyr_raid_{identifier}_{hex_ts}_{self._counter:04d}"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._pat_token}"}

    def _build_task(self, item: dict) -> AgentTask:
        identifier = item.get("identifier", item.get("issue_id", "unknown"))
        title = item.get("title", identifier)
        saga_name = item.get("saga_name", "")
        task_id = self._make_task_id(identifier)

        return AgentTask(
            task_id=task_id,
            title=title,
            initiative_context=_build_initiative_context(item),
            triggered_by=f"tyr_queue:{saga_name}",
            output_mode=OutputMode.AMBIENT,
            persona="raid-executor",
        )

    async def _fetch_dispatcher_state(self, client: httpx.AsyncClient) -> dict | None:
        url = f"{self._tyr_base_url}{_DISPATCHER_PATH}"
        try:
            resp = await client.get(
                url,
                headers=self._auth_headers(),
                timeout=_REQUEST_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning("TyrQueueTrigger: dispatcher fetch error: %s", exc)
            return None

        if resp.status_code != 200:
            logger.warning("TyrQueueTrigger: dispatcher returned HTTP %s", resp.status_code)
            return None

        return resp.json()

    async def _fetch_queue(self, client: httpx.AsyncClient) -> list[dict] | None:
        url = f"{self._tyr_base_url}{_QUEUE_PATH}"
        try:
            resp = await client.get(
                url,
                headers=self._auth_headers(),
                timeout=_REQUEST_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning("TyrQueueTrigger: queue fetch error: %s", exc)
            return None

        if resp.status_code != 200:
            logger.warning("TyrQueueTrigger: queue returned HTTP %s", resp.status_code)
            return None

        return resp.json()

    async def _poll_once(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Single poll cycle — checks dispatcher state then enqueues ready raids."""
        async with httpx.AsyncClient() as client:
            dispatcher = await self._fetch_dispatcher_state(client)
            if dispatcher is None:
                return

            if not dispatcher.get("running", False):
                logger.debug("TyrQueueTrigger: dispatcher not running, skipping poll")
                return

            if not dispatcher.get("auto_continue", False):
                logger.debug("TyrQueueTrigger: auto_continue disabled, skipping poll")
                return

            max_concurrent = dispatcher.get("max_concurrent_raids", 1)

            items = await self._fetch_queue(client)
            if items is None:
                return

        # Clear IDs that are no longer in the queue (completed / removed by Tyr).
        # Do this before the capacity check so completed raids free up slots.
        current_issue_ids = {iid for item in items if (iid := item.get("issue_id", ""))}
        self._enqueued_ids = {eid for eid in self._enqueued_ids if eid in current_issue_ids}

        if len(self._enqueued_ids) >= max_concurrent:
            logger.debug(
                "TyrQueueTrigger: %d/%d raids in-flight, skipping enqueue",
                len(self._enqueued_ids),
                max_concurrent,
            )
            return

        available_slots = max_concurrent - len(self._enqueued_ids)
        for item in items:
            if available_slots <= 0:
                break

            issue_id = item.get("issue_id", "")
            if not issue_id:
                continue

            if issue_id in self._enqueued_ids:
                continue

            task = self._build_task(item)
            self._enqueued_ids.add(issue_id)
            available_slots -= 1

            logger.info(
                "TyrQueueTrigger: enqueuing raid %s (%s) as task %s",
                item.get("identifier", issue_id),
                item.get("title", ""),
                task.task_id,
            )
            await enqueue(task)

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[None]]) -> None:
        """Run forever, polling the Tyr dispatch queue on each interval."""
        while True:
            await asyncio.sleep(self._poll_interval_s)

            try:
                await self._poll_once(enqueue)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("TyrQueueTrigger: unexpected poll error: %s", exc)
