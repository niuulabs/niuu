"""Sleipnir-triggered checkpoint listener (NIU-537).

Subscribes to ``ravn.task.checkpoint_requested`` events published by Ting
(or any other Sleipnir producer) and triggers a named snapshot on the
running agent.

Event payload (JSON):
    {
        "task_id":  "...",   # required — identifies the agent to checkpoint
        "label":    "...",   # optional human-readable label
        "tags":     [...]    # optional tag list
    }

Usage (inside the daemon / drive-loop runner):

    listener = SleipnirCheckpointListener(
        amqp_url="amqp://guest:guest@localhost/",
        exchange="sleipnir",
        checkpoint_callbacks={task_id: async_fn},
    )
    asyncio.create_task(listener.listen())
    ...
    listener.stop()

``checkpoint_callbacks`` is a live dict mapping task_id → async callable
that accepts ``(label, tags)`` and triggers the snapshot.  The drive loop
registers/unregisters entries as agents start and stop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress

logger = logging.getLogger(__name__)

# Routing-key pattern for checkpoint requests.
CHECKPOINT_EVENT_ROUTING_KEY = "ravn.task.checkpoint_requested"

# Type alias: async fn(label, tags) → None
CheckpointCallback = Callable[[str, list[str]], Awaitable[None]]


class SleipnirCheckpointListener:
    """Subscribe to Sleipnir and dispatch checkpoint requests to running agents.

    Parameters
    ----------
    amqp_url:
        RabbitMQ connection URL.
    exchange:
        Topic exchange name (default: ``sleipnir``).
    checkpoint_callbacks:
        Mutable dict mapping task_id → async callback.  The drive loop
        populates this as agents start/stop.
    reconnect_delay_s:
        Seconds to wait before reconnecting after an AMQP error.
    """

    def __init__(
        self,
        amqp_url: str,
        exchange: str = "sleipnir",
        checkpoint_callbacks: dict[str, CheckpointCallback] | None = None,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        self._amqp_url = amqp_url
        self._exchange = exchange
        self._callbacks: dict[str, CheckpointCallback] = checkpoint_callbacks or {}
        self._reconnect_delay = reconnect_delay_s
        self._stop_event = asyncio.Event()

    def register(self, task_id: str, callback: CheckpointCallback) -> None:
        """Register a callback for *task_id*."""
        self._callbacks[task_id] = callback

    def unregister(self, task_id: str) -> None:
        """Remove the callback for *task_id*."""
        self._callbacks.pop(task_id, None)

    def stop(self) -> None:
        """Signal the listener to shut down."""
        self._stop_event.set()

    async def listen(self) -> None:
        """Subscribe to Sleipnir and dispatch checkpoint events until stopped."""
        while not self._stop_event.is_set():
            try:
                await self._run_consumer()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning(
                    "Sleipnir checkpoint listener error: %s — reconnecting in %.1fs",
                    exc,
                    self._reconnect_delay,
                )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._reconnect_delay,
                    )

    async def _run_consumer(self) -> None:
        """Connect to RabbitMQ and consume checkpoint request events."""
        try:
            import aio_pika  # type: ignore[import]
        except ImportError:
            logger.warning(
                "aio_pika not installed — Sleipnir checkpoint trigger disabled. "
                "Install with: pip install aio-pika"
            )
            await self._stop_event.wait()
            return

        connection = await aio_pika.connect_robust(
            self._amqp_url,
            timeout=10,
        )
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                self._exchange,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            queue = await channel.declare_queue(
                "",
                exclusive=True,
                auto_delete=True,
            )
            await queue.bind(exchange, routing_key=CHECKPOINT_EVENT_ROUTING_KEY)

            logger.info(
                "Sleipnir checkpoint listener ready (exchange=%r, key=%r)",
                self._exchange,
                CHECKPOINT_EVENT_ROUTING_KEY,
            )

            async with queue.iterator() as q_iter:
                async for message in q_iter:
                    if self._stop_event.is_set():
                        break
                    async with message.process():
                        await self._handle_message(message.body)

    async def _handle_message(self, body: bytes) -> None:
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid checkpoint event payload: %s", exc)
            return

        task_id = str(payload.get("task_id", "")).strip()
        if not task_id:
            logger.warning("Checkpoint event missing task_id — ignored")
            return

        label = str(payload.get("label", "")).strip()
        tags = [str(t) for t in payload.get("tags", [])]

        callback = self._callbacks.get(task_id)
        if callback is None:
            logger.debug("No running agent for task_id=%r — checkpoint event ignored", task_id)
            return

        logger.info("Checkpoint requested via Sleipnir for task_id=%r", task_id)
        try:
            await callback(label or "ting_requested", tags)
        except Exception as exc:
            logger.warning("Checkpoint callback for task_id=%r raised: %s", task_id, exc)
