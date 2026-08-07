"""Trigger port — interface for drive-loop trigger sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

from ravn.domain.models import AgentTask


class TriggerPort(ABC):
    """Abstract interface for a drive-loop trigger source.

    Each trigger runs indefinitely as an asyncio task, calling ``enqueue``
    whenever a new task should fire.  The drive loop registers triggers via
    ``DriveLoop.register_trigger()`` before calling ``DriveLoop.run()``.

    To implement a custom trigger::

        class MyTrigger(TriggerPort):
            def __init__(self, interval: int = 60) -> None:
                self._interval = interval

            @property
            def name(self) -> str:
                return "my_trigger"

            async def run(
                self, enqueue: Callable[[AgentTask], Awaitable[None]]
            ) -> None:
                while True:
                    await asyncio.sleep(self._interval)
                    await enqueue(AgentTask(initiative_context="run my task"))

    Register it in ``ravn.yaml``::

        initiative:
          trigger_adapters:
            - adapter: mypackage.triggers.MyTrigger
              kwargs:
                interval: 300
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable trigger name, e.g. ``cron:morning_check``."""
        raise NotImplementedError

    @abstractmethod
    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        """Run forever.  Call ``enqueue`` whenever a task should fire.

        ``enqueue`` returns whether the drive loop accepted the task.  It
        refuses when the queue is at capacity, and a trigger that ignores the
        refusal will keep producing work that is thrown away — pay attention to
        it before doing anything expensive for the next task.
        """
        raise NotImplementedError
