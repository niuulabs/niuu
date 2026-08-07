"""SleipnirEventTrigger — fires tasks when a RabbitMQ message arrives.

Subscribes to a routing-key pattern on the Sleipnir event backbone and
enqueues a task for each matching message.  The task's initiative_context
is rendered from a Jinja2 template with the message payload.

Requires NIU-438 (Sleipnir publishing) to be enabled — logs a warning and
does nothing if the ``aio_pika`` package is not installed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from ravn.domain.models import AgentTask, OutputMode
from ravn.ports.trigger import TriggerPort

logger = logging.getLogger(__name__)


class SleipnirEventTrigger(TriggerPort):
    """Trigger that fires tasks from RabbitMQ routing-key pattern matches."""

    def __init__(
        self,
        name: str,
        pattern: str,
        context_template: str,
        output_mode: OutputMode = OutputMode.SURFACE,
        persona: str | None = None,
        priority: int = 10,
        amqp_url: str = "amqp://guest:guest@localhost/",
        exchange: str = "sleipnir",
        retry_delay_seconds: float = 5.0,
    ) -> None:
        self._name = name
        self._pattern = pattern
        self._context_template = context_template
        self._output_mode = output_mode
        self._persona = persona
        self._priority = priority
        self._amqp_url = amqp_url
        self._exchange = exchange
        self._retry_delay_seconds = retry_delay_seconds
        self._counter = 0

    @property
    def name(self) -> str:
        return f"sleipnir_event:{self._name}"

    def _make_task_id(self) -> str:
        self._counter += 1
        hex_ts = hex(int(time.time() * 1000))[2:]
        return f"task_{hex_ts}_{self._counter:04d}"

    def _render_context(self, payload: dict) -> str:
        try:
            from jinja2 import Environment  # type: ignore[import-untyped]

            env = Environment(autoescape=True)
            return env.from_string(self._context_template).render(payload=payload)
        except ImportError:
            logger.warning("jinja2 not installed — using raw context_template")
            return self._context_template
        except Exception as exc:
            logger.warning("Failed to render context_template: %s", exc)
            return self._context_template

    async def run(self, enqueue: Callable[[AgentTask], Awaitable[bool]]) -> None:
        try:
            import aio_pika  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("aio_pika not installed — SleipnirEventTrigger %r disabled", self._name)
            return

        while True:
            try:
                await self._connect_and_consume(aio_pika, enqueue)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "SleipnirEventTrigger %r connection error: %s — retrying in %.0fs",
                    self._name,
                    exc,
                    self._retry_delay_seconds,
                )
                await asyncio.sleep(self._retry_delay_seconds)

    async def _connect_and_consume(
        self, aio_pika: object, enqueue: Callable[[AgentTask], Awaitable[bool]]
    ) -> None:
        connection = await aio_pika.connect_robust(self._amqp_url)  # type: ignore[attr-defined]
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                self._exchange,
                aio_pika.ExchangeType.TOPIC,
                durable=True,  # type: ignore[attr-defined]
            )
            queue = await channel.declare_queue("", exclusive=True)
            await queue.bind(exchange, routing_key=self._pattern)

            logger.info(
                "SleipnirEventTrigger %r listening on pattern %r",
                self._name,
                self._pattern,
            )

            async with queue.iterator() as q_iter:
                async for message in q_iter:
                    async with message.process():
                        await self._handle_message(message, enqueue)

    async def _handle_message(
        self,
        message: object,
        enqueue: Callable[[AgentTask], Awaitable[bool]],
    ) -> None:
        import json as _json

        try:
            payload = _json.loads(message.body)  # type: ignore[attr-defined]
        except Exception:
            payload = {"raw": str(message.body)}  # type: ignore[attr-defined]

        context = self._render_context(payload)
        task_id = self._make_task_id()
        task = AgentTask(
            task_id=task_id,
            title=f"{self._name}: {message.routing_key}",  # type: ignore[attr-defined]
            initiative_context=context,
            triggered_by=f"event:{message.routing_key}",  # type: ignore[attr-defined]
            output_mode=self._output_mode,
            persona=self._persona,
            priority=self._priority,
        )
        logger.info(
            "SleipnirEventTrigger %r received message (task_id=%s routing_key=%s)",
            self._name,
            task_id,
            message.routing_key,  # type: ignore[attr-defined]
        )
        await enqueue(task)
