"""Shared RabbitMQ publish mixin used by SleipnirChannel, RabbitMQEventPublisher,
and TaskDispatchChannel.

All three adapters need the same lazy-connect / reconnect-backoff / invalidate
pattern for their publish connections.  This mixin centralises that logic so it
lives in exactly one place.

Usage
-----
1. Inherit ``RabbitMQPublishMixin`` (alongside the real base class).
2. Set the ``_log_prefix`` class variable to a short identifier used in log messages.
3. Call ``_init_publish_state()`` inside ``__init__`` *after* ``self._config`` is set.
4. Use ``_ensure_exchange()`` / ``_publish_to_exchange(routing_key, body)`` in your
   publish method.
5. Call ``_invalidate()`` on close or after a publish failure.
"""

from __future__ import annotations
from contextlib import suppress

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravn.config import SleipnirConfig

try:
    import aio_pika
except ImportError:  # pragma: no cover
    aio_pika = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class RabbitMQPublishMixin:
    """Mixin that provides lazy-connect RabbitMQ publish helpers.

    Subclasses must set ``_log_prefix`` and call ``_init_publish_state()`` in
    their ``__init__``.  They must also have ``self._config: SleipnirConfig``
    available before calling any method on the mixin.
    """

    _log_prefix: str = "rabbitmq"

    # Populated by _init_publish_state(); typed here for IDE / mypy.
    _connection: object | None
    _channel: object | None
    _exchange: object | None
    _connect_lock: asyncio.Lock
    _last_connect_attempt: float

    def _init_publish_state(self) -> None:
        """Initialise the connection-state attributes.  Call from ``__init__``."""
        self._connection = None
        self._channel = None
        self._exchange = None
        self._connect_lock = asyncio.Lock()
        self._last_connect_attempt = 0.0

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    async def _ensure_exchange(self) -> object | None:
        """Return the cached exchange, (re)connecting lazily if needed."""
        if self._exchange is not None:
            return self._exchange

        async with self._connect_lock:
            # Double-checked locking — another coroutine may have connected.
            if self._exchange is not None:
                return self._exchange

            now = asyncio.get_running_loop().time()
            config: SleipnirConfig = self._config  # type: ignore[attr-defined]
            if now - self._last_connect_attempt < config.reconnect_delay_s:
                return None

            self._last_connect_attempt = now
            return await self._connect()

    async def _connect(self) -> object | None:
        """Establish a RabbitMQ connection and declare the topic exchange."""
        if aio_pika is None:
            logger.debug("%s: aio_pika not installed, publishing disabled", self._log_prefix)
            return None

        config: SleipnirConfig = self._config  # type: ignore[attr-defined]
        amqp_url = os.environ.get(config.amqp_url_env, "")
        if not amqp_url:
            logger.debug(
                "%s: %s not set, publishing disabled",
                self._log_prefix,
                config.amqp_url_env,
            )
            return None

        try:
            connection = await aio_pika.connect_robust(amqp_url)  # type: ignore[union-attr]
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                config.exchange,
                aio_pika.ExchangeType.TOPIC,  # type: ignore[union-attr]
                durable=True,
            )
            self._connection = connection
            self._channel = channel
            self._exchange = exchange
            logger.debug(
                "%s: connected to %s, exchange=%s",
                self._log_prefix,
                amqp_url,
                config.exchange,
            )
            return exchange
        except Exception as exc:
            logger.debug("%s: connection failed (%s), will retry", self._log_prefix, exc)
            return None

    async def _invalidate(self) -> None:
        """Drop cached connection state so the next publish attempts to reconnect."""
        self._exchange = None
        self._channel = None
        if self._connection is not None:
            with suppress(Exception):
                await self._connection.close()  # type: ignore[union-attr]
        self._connection = None

    async def _publish_to_exchange(self, routing_key: str, body: bytes) -> None:
        """Publish *body* to *routing_key*.  Never raises — failures are logged."""
        exchange = await self._ensure_exchange()
        if exchange is None:
            logger.debug("%s: exchange unavailable, dropping %s", self._log_prefix, routing_key)
            return

        try:
            if aio_pika is None:
                return
            config: SleipnirConfig = self._config  # type: ignore[attr-defined]
            message = aio_pika.Message(  # type: ignore[union-attr]
                body=body,
                content_type="application/json",
            )
            await asyncio.wait_for(
                exchange.publish(message, routing_key=routing_key),  # type: ignore[attr-defined]
                timeout=config.publish_timeout_s,
            )
        except Exception as exc:
            logger.debug("%s: publish failed (%s), dropping %s", self._log_prefix, exc, routing_key)
            await self._invalidate()
