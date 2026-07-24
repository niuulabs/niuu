"""NATS JetStream transport for domain-neutral Environment signal ingestion.

This adapter is TRANSPORT ONLY: it drains a durable JetStream pull consumer
and hands each raw message payload to :class:`GenericSignalAdapter`
normalization unchanged.  No field extraction, no domain schemas, no severity
heuristics — severity is honored only when the publisher declared one inside
its own payload (see ``GenericSignalAdapter.normalize_raw``).

The durable consumer is what makes signals survive resident restarts:
JetStream tracks the consumer position server-side, so a redeployed resident
resumes exactly where the previous instance stopped instead of losing the
messages published while it was down.

Configuration example (``signal_sources`` entry)::

    signal_sources:
      - id: upstream-events
        name: Upstream event stream
        kind: generic
        adapter: ravn.adapters.environment_signals_nats.NatsJetStreamSignalAdapter
        enabled: true
        kwargs:
          servers: ["tls://nats.example.svc:4222"]
          stream_name: upstream-events
          subject: upstream.events.>
          durable_name: resident-upstream-events
          deliver_policy: all
          tls_ca_file: /etc/nats-ca/ca.crt
          nkeys_seed_file: /etc/nats-nkey/nats.nk

The stream itself is expected to exist (GitOps-managed); a missing stream
fails loudly at bind time instead of being silently created with guessed
settings.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from time import monotonic
from typing import Any

from niuu.observability import get_observability
from ravn.adapters.environment_signals import GenericSignalAdapter
from ravn.domain.environment import Environment
from sleipnir.adapters.nats_transport import (
    DEFAULT_CONNECT_TIMEOUT_S,
    DEFAULT_MAX_RECONNECT_ATTEMPTS,
    build_connect_options,
    connect_nats,
    nats_available,
)

try:
    import nats.js.api as js_api
except ImportError:  # pragma: no cover - exercised in minimal installs
    js_api = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: Maximum messages pulled from JetStream in one collect pass.
DEFAULT_BATCH_SIZE = 64

#: Seconds one fetch waits for at least one message before returning empty.
DEFAULT_FETCH_TIMEOUT_S = 2.0

#: Supported JetStream deliver policies for the durable consumer.
DELIVER_POLICIES = ("all", "new", "last")


def _decode_payload(data: bytes) -> Any:
    """Decode one message body without imposing any schema.

    JSON passes through untouched (objects stay objects, scalars are wrapped
    by ``GenericSignalAdapter.normalize_raw``); anything that is not JSON is
    delivered as raw text so the resident still sees exactly what the source
    sent.
    """
    text = data.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _sanitize_durable(name: str) -> str:
    """Return a JetStream-safe durable name (no dots, wildcards, or spaces)."""
    safe = "".join(ch if ch.isalnum() or ch in ("_", "-") else "-" for ch in name)
    return safe.strip("-_") or "signals"


class NatsJetStreamSignalAdapter(GenericSignalAdapter):
    """Durable JetStream pull consumer feeding ``GenericSignalAdapter``.

    Each :meth:`collect` call fetches up to *batch_size* pending messages and
    returns decoded payloads for pass-through normalization. The surrounding
    runtime calls :meth:`commit` only after publishing and durably enqueueing
    resident work; failures call :meth:`rollback` so JetStream redelivers.
    """

    def __init__(
        self,
        *,
        environment: Environment,
        source_id: str,
        servers: list[str] | str,
        stream_name: str,
        subject: str,
        durable_name: str = "",
        batch_size: int = DEFAULT_BATCH_SIZE,
        fetch_timeout_s: float = DEFAULT_FETCH_TIMEOUT_S,
        deliver_policy: str = "new",
        jetstream_domain: str = "",
        connect_timeout_s: float = DEFAULT_CONNECT_TIMEOUT_S,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        proxy_url: str = "",
        tls_ca_file: str = "",
        tls_ca_pem: str = "",
        tls_cert_file: str = "",
        tls_key_file: str = "",
        tls_hostname: str = "",
        tls_handshake_first: bool = False,
        tls_legacy_ca: bool = False,
        tls_insecure_skip_verify: bool = False,
        user: str = "",
        password: str = "",
        token: str = "",
        nkeys_seed_file: str = "",
        nkeys_seed: str = "",
        client: Any | None = None,
    ) -> None:
        if not nats_available() or js_api is None:
            raise RuntimeError(
                "NatsJetStreamSignalAdapter requires nats-py. Install niuu with the nats extra."
            )
        if isinstance(servers, str):
            servers = [entry.strip() for entry in servers.split(",") if entry.strip()]
        if not servers:
            raise ValueError("NatsJetStreamSignalAdapter requires at least one NATS server URL")
        if not stream_name.strip():
            raise ValueError("NatsJetStreamSignalAdapter requires stream_name")
        if not subject.strip():
            raise ValueError("NatsJetStreamSignalAdapter requires subject")
        if deliver_policy not in DELIVER_POLICIES:
            raise ValueError(
                f"Unknown deliver_policy {deliver_policy!r}; use one of {DELIVER_POLICIES}"
            )
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        self._servers = list(servers)
        self._stream_name = stream_name.strip()
        self._subject = subject.strip()
        self._durable_name = durable_name.strip() or _sanitize_durable(f"signal-{source_id}")
        self._batch_size = batch_size
        self._fetch_timeout_s = fetch_timeout_s
        self._deliver_policy = deliver_policy
        self._jetstream_domain = jetstream_domain.strip()
        self._connect_timeout_s = connect_timeout_s
        self._max_reconnect_attempts = max_reconnect_attempts
        self._proxy_url = proxy_url
        self._connect_options = build_connect_options(
            tls_ca_file=tls_ca_file,
            tls_ca_pem=tls_ca_pem,
            tls_cert_file=tls_cert_file,
            tls_key_file=tls_key_file,
            tls_hostname=tls_hostname,
            tls_handshake_first=tls_handshake_first,
            tls_legacy_ca=tls_legacy_ca,
            tls_insecure_skip_verify=tls_insecure_skip_verify,
            user=user,
            password=password,
            token=token,
            nkeys_seed_file=nkeys_seed_file,
            nkeys_seed=nkeys_seed,
        )
        self._client = client
        self._psub: Any | None = None
        self._pending: list[Any] = []
        super().__init__(
            environment=environment,
            source_id=source_id,
            provider=self._fetch_batch,
        )

    async def _fetch_batch(self) -> list[Any]:
        if self._pending:
            raise RuntimeError("previous JetStream signal batch is still pending")
        telemetry = get_observability()
        attributes = self._messaging_attributes("receive")
        started = monotonic()
        with telemetry.span(f"receive {self._subject}", attributes=attributes) as span:
            psub = await self._ensure_subscription()
            try:
                msgs = await psub.fetch(self._batch_size, timeout=self._fetch_timeout_s)
            except TimeoutError:
                # nats.errors.TimeoutError subclasses the builtin on 3.11+; an
                # idle stream is the normal case, not an error.
                span.set_attribute("messaging.batch.message_count", 0)
                telemetry.count(
                    "ravn.signal.transport.polls",
                    attributes={**attributes, "ravn.signal.transport.outcome": "idle"},
                )
                return []
            except Exception:
                await self._reset_connection("fetch_failed")
                raise
            decoded = [_decode_payload(msg.data) for msg in msgs]
            self._pending = list(msgs)
            span.set_attribute("messaging.batch.message_count", len(msgs))
            telemetry.event(
                "ravn.signal.transport.received",
                attributes={
                    **attributes,
                    "messaging.batch.message_count": len(msgs),
                },
                content=decoded,
            )
            telemetry.count(
                "ravn.signal.transport.messages",
                value=len(msgs),
                attributes={**attributes, "ravn.signal.transport.outcome": "received"},
            )
            telemetry.duration(
                "ravn.signal.transport.receive.duration",
                monotonic() - started,
                attributes=attributes,
            )
            return decoded

    @property
    def requires_commit(self) -> bool:
        return True

    async def commit(self) -> None:
        telemetry = get_observability()
        attributes = self._messaging_attributes("ack")
        pending_count = len(self._pending)
        with telemetry.span("ack JetStream signals", attributes=attributes) as span:
            while self._pending:
                message = self._pending[0]
                await message.ack()
                self._pending.pop(0)
            span.set_attribute("messaging.batch.message_count", pending_count)
            telemetry.event(
                "ravn.signal.transport.committed",
                attributes={**attributes, "messaging.batch.message_count": pending_count},
            )
            telemetry.count(
                "ravn.signal.transport.messages",
                value=pending_count,
                attributes={**attributes, "ravn.signal.transport.outcome": "acked"},
            )

    async def rollback(self) -> None:
        pending, self._pending = self._pending, []
        telemetry = get_observability()
        attributes = self._messaging_attributes("nack")
        with telemetry.span("nack JetStream signals", attributes=attributes) as span:
            for message in pending:
                nak = getattr(message, "nak", None)
                if nak is not None:
                    await nak()
            span.set_attribute("messaging.batch.message_count", len(pending))
            telemetry.event(
                "ravn.signal.transport.rolled_back",
                attributes={**attributes, "messaging.batch.message_count": len(pending)},
            )
            telemetry.count(
                "ravn.signal.transport.messages",
                value=len(pending),
                attributes={**attributes, "ravn.signal.transport.outcome": "nacked"},
            )

    async def _ensure_subscription(self) -> Any:
        if self._psub is not None and not self._client_is_closed():
            return self._psub
        if self._psub is not None or self._client_is_closed():
            await self._reset_connection("connection_closed")
        telemetry = get_observability()
        attributes = self._messaging_attributes("create_consumer")
        with telemetry.span("bind JetStream consumer", attributes=attributes):
            if self._client is None:
                self._client = await connect_nats(
                    servers=self._servers,
                    connect_timeout=self._connect_timeout_s,
                    max_reconnect_attempts=self._max_reconnect_attempts,
                    proxy_url=self._proxy_url,
                    options=self._connect_options,
                )
            js_kwargs = {"domain": self._jetstream_domain} if self._jetstream_domain else {}
            js = self._client.jetstream(**js_kwargs)
            config = js_api.ConsumerConfig(
                deliver_policy=self._resolve_deliver_policy(),
                ack_policy=js_api.AckPolicy.EXPLICIT,
            )
            self._psub = await js.pull_subscribe(
                self._subject,
                durable=self._durable_name,
                stream=self._stream_name,
                config=config,
            )
            telemetry.event("ravn.signal.transport.bound", attributes=attributes)
        logger.info(
            "environment_signals: source=%s bound durable=%s stream=%s subject=%s",
            self.source_id,
            self._durable_name,
            self._stream_name,
            self._subject,
        )
        return self._psub

    def _client_is_closed(self) -> bool:
        if self._client is None:
            return False
        closed = getattr(self._client, "is_closed", False)
        return bool(closed() if callable(closed) else closed)

    async def _reset_connection(self, reason: str) -> None:
        client = self._client
        self._client = None
        self._psub = None
        if client is not None and not self._client_is_closed_value(client):
            close = getattr(client, "close", None)
            if callable(close):
                with suppress(Exception):
                    await close()
        get_observability().event(
            "ravn.signal.transport.connection_reset",
            attributes={
                **self._messaging_attributes("reconnect"),
                "ravn.signal.transport.reset_reason": reason,
            },
        )
        logger.warning(
            "environment_signals: source=%s reset NATS connection reason=%s; "
            "the next poll will reconnect",
            self.source_id,
            reason,
        )

    @staticmethod
    def _client_is_closed_value(client: Any) -> bool:
        closed = getattr(client, "is_closed", False)
        return bool(closed() if callable(closed) else closed)

    def _messaging_attributes(self, operation: str) -> dict[str, Any]:
        return {
            "messaging.system": "nats",
            "messaging.operation.name": operation,
            "messaging.destination.name": self._subject,
            "messaging.destination.subscription.name": self._durable_name,
            "ravn.signal.stream": self._stream_name,
            "ravn.signal.source": self.source_id,
        }

    def _resolve_deliver_policy(self) -> Any:
        match self._deliver_policy:
            case "all":
                return js_api.DeliverPolicy.ALL
            case "new":
                return js_api.DeliverPolicy.NEW
            case "last":
                return js_api.DeliverPolicy.LAST
            case _:  # pragma: no cover - rejected in __init__
                raise ValueError(f"Unknown deliver_policy {self._deliver_policy!r}")
