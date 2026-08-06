"""ConditionPollTrigger — fires tasks when a sensor agent says TRIGGER.

The sensor agent runs a lightweight agent turn on a polling schedule.
It must respond with exactly ``TRIGGER`` or ``CLEAR`` as its sole content.
On ``TRIGGER``, the full task is enqueued and a cooldown period begins.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ravn.adapters.channels.silent import SilentChannel
from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)

_DEFAULT_CHECK_INTERVAL = 300  # seconds
_DEFAULT_COOLDOWN = 60  # minutes


class ConditionPollTrigger(TriggerPort):
    """Trigger that polls a sensor agent and fires when it says ``TRIGGER``."""

    def __init__(
        self,
        name: str,
        sensor_prompt: str,
        task_context: str,
        sensor_agent_factory: Callable[[], object],
        output_mode: OutputMode = OutputMode.SILENT,
        persona: str | None = None,
        priority: int = 10,
        check_interval_seconds: float = _DEFAULT_CHECK_INTERVAL,
        cooldown_minutes: float = _DEFAULT_COOLDOWN,
    ) -> None:
        self._name = name
        self._sensor_prompt = sensor_prompt
        self._task_context = task_context
        self._sensor_agent_factory = sensor_agent_factory
        self._output_mode = output_mode
        self._persona = persona
        self._priority = priority
        self._check_interval = check_interval_seconds
        self._cooldown_seconds = cooldown_minutes * 60
        self._last_trigger_at: datetime | None = None
        self._counter = 0

    @property
    def name(self) -> str:
        return f"condition_poll:{self._name}"

    def _make_task_id(self) -> str:
        self._counter += 1
        hex_ts = hex(int(time.time() * 1000))[2:]
        return f"task_{hex_ts}_{self._counter:04d}"

    def _in_cooldown(self) -> bool:
        if self._last_trigger_at is None:
            return False
        elapsed = (datetime.now(UTC) - self._last_trigger_at).total_seconds()
        return elapsed < self._cooldown_seconds

    async def _run_sensor(self) -> str:
        """Run the sensor agent and return its response text."""
        channel = SilentChannel()
        agent = self._sensor_agent_factory()
        try:
            await agent.run_turn(self._sensor_prompt)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.warning("ConditionPollTrigger %r sensor error: %s", self._name, exc)
            return "CLEAR"
        return channel.response_text.strip().upper()

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        while True:
            await asyncio.sleep(self._check_interval)

            if self._in_cooldown():
                logger.debug("ConditionPollTrigger %r: in cooldown, skipping poll", self._name)
                continue

            try:
                verdict = await self._run_sensor()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("ConditionPollTrigger %r: sensor run failed: %s", self._name, exc)
                continue

            if verdict != "TRIGGER":
                logger.debug("ConditionPollTrigger %r: sensor returned %r", self._name, verdict)
                continue

            self._last_trigger_at = datetime.now(UTC)
            task_id = self._make_task_id()
            task = AgentTask(
                task_id=task_id,
                title=f"condition:{self._name}",
                initiative_context=self._task_context,
                triggered_by=f"condition:{self._name}",
                output_mode=self._output_mode,
                persona=self._persona,
                priority=self._priority,
            )
            logger.info(
                "ConditionPollTrigger %r: TRIGGER — enqueuing task %s",
                self._name,
                task_id,
            )
            await enqueue(task)
