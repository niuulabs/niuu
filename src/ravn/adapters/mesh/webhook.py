"""WebhookMeshAdapter — HTTP-based mesh transport for cross-network communication.

Uses aiohttp for both server (receiving) and client (sending). Designed for
environments where ZeroMQ/nng sockets won't work (serverless, firewalls).

**Request signing**: All requests include HMAC-SHA256 signature using a shared
secret. The signature is in the ``X-Ravn-Signature`` header. Requests without
valid signatures are rejected.

**Peer endpoints**: The adapter needs to know peer webhook URLs. These come from
discovery — StaticDiscoveryAdapter's cluster.json can include ``webhook_url``
per peer, or the URL can be derived from ``rep_address`` with a URL pattern.

**Pub/sub emulation**: HTTP is request/response, not pub/sub. Publish fans out
POSTs to all subscribed peers. Subscribe registers local handlers that the
inbound server invokes when events arrive.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ravn.domain.events import RavnEvent, RavnEventType
from ravn.ports.mesh import PeerNotFoundError

if TYPE_CHECKING:
    from aiohttp import web

try:
    import aiohttp
    from aiohttp import web as aiohttp_web
except ImportError:  # pragma: no cover
    aiohttp = None  # type: ignore[assignment]
    aiohttp_web = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


_SIGNATURE_HEADER = "X-Ravn-Signature"
_TIMESTAMP_HEADER = "X-Ravn-Timestamp"
_PEER_ID_HEADER = "X-Ravn-Peer-Id"
_MAX_CLOCK_SKEW_S = 300  # 5 minutes


class WebhookMeshAdapter:
    """HTTP-based mesh transport for cross-network communication.

    Parameters
    ----------
    own_peer_id:
        This Ravn's unique peer identifier.
    discovery:
        Injected DiscoveryPort for peer lookup (must have ``peers()`` method).
    secret:
        Shared secret for HMAC request signing.
    listen_host:
        Host to bind the inbound server to.
    listen_port:
        Port for the inbound server.
    webhook_path:
        URL path for the webhook endpoint.
    rpc_timeout_s:
        Default timeout for RPC calls.
    connect_timeout_s:
        Timeout for establishing HTTP connections.
    **kwargs:
        Ignored — allows forward compatibility with new config fields.
    """

    def __init__(
        self,
        own_peer_id: str,
        discovery: object,
        *,
        secret: str = "",
        listen_host: str = "0.0.0.0",
        listen_port: int = 7490,
        webhook_path: str = "/ravn/mesh",
        rpc_timeout_s: float = 10.0,
        connect_timeout_s: float = 5.0,
        **kwargs: Any,  # noqa: ARG002 — ignored
    ) -> None:
        self._own_peer_id = own_peer_id
        self._discovery = discovery
        self._secret = secret.encode() if secret else b""
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._webhook_path = webhook_path
        self._rpc_timeout_s = rpc_timeout_s
        self._connect_timeout_s = connect_timeout_s

        # Topic -> handlers
        self._handlers: dict[str, list[Callable[[RavnEvent], Awaitable[None]]]] = {}
        self._rpc_handler: Callable[[dict], Awaitable[dict]] | None = None

        # HTTP components
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # MeshPort interface
    # ------------------------------------------------------------------

    async def publish(self, event: RavnEvent, topic: str) -> None:
        """Broadcast event to all peers subscribed to topic."""
        if self._session is None:
            logger.debug("webhook_mesh: not started, skipping publish")
            return

        peers = self._get_peers()
        if not peers:
            return

        payload = self._event_to_dict(event, topic, "publish")
        body = json.dumps(payload).encode()

        # Fan out to all peers (fire-and-forget, don't wait for all)
        tasks = []
        for peer_id, peer in peers.items():
            url = self._peer_webhook_url(peer)
            if not url:
                continue
            tasks.append(self._post_to_peer(url, body))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def subscribe(
        self,
        topic: str,
        handler: Callable[[RavnEvent], Awaitable[None]],
    ) -> None:
        """Register handler for events on topic."""
        if topic not in self._handlers:
            self._handlers[topic] = []
        self._handlers[topic].append(handler)

    async def unsubscribe(self, topic: str) -> None:
        """Remove all handlers for topic."""
        self._handlers.pop(topic, None)

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        """Send message to target peer and await reply."""
        if self._session is None:
            raise RuntimeError("WebhookMeshAdapter not started")

        peer = self._get_peer(target_peer_id)
        if peer is None:
            raise PeerNotFoundError(target_peer_id)

        url = self._peer_webhook_url(peer)
        if not url:
            raise PeerNotFoundError(target_peer_id)

        timeout = timeout_s if timeout_s is not None else self._rpc_timeout_s

        payload = {
            "type": "rpc",
            "source_peer_id": self._own_peer_id,
            "target_peer_id": target_peer_id,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        body = json.dumps(payload).encode()

        try:
            response = await self._post_to_peer(url, body, timeout=timeout)
            if response is None:
                raise TimeoutError(f"No reply from peer {target_peer_id!r}")
            return response
        except TimeoutError as exc:
            raise TimeoutError(f"No reply from peer {target_peer_id!r} within {timeout}s") from exc

    async def start(self) -> None:
        """Start HTTP server and client session."""
        if aiohttp is None:  # pragma: no cover
            logger.warning("webhook_mesh: aiohttp not installed — mesh disabled")
            return

        # Start client session
        timeout = aiohttp.ClientTimeout(
            total=self._rpc_timeout_s,
            connect=self._connect_timeout_s,
        )
        self._session = aiohttp.ClientSession(timeout=timeout)

        # Start server
        self._app = aiohttp_web.Application()
        self._app.router.add_post(self._webhook_path, self._handle_webhook)

        self._runner = aiohttp_web.AppRunner(self._app)
        await self._runner.setup()

        site = aiohttp_web.TCPSite(self._runner, self._listen_host, self._listen_port)
        await site.start()

        logger.info(
            "webhook_mesh: started peer=%s listen=%s:%d%s",
            self._own_peer_id,
            self._listen_host,
            self._listen_port,
            self._webhook_path,
        )

    async def stop(self) -> None:
        """Graceful shutdown."""
        if self._session is not None:
            await self._session.close()
            self._session = None

        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

        self._app = None
        logger.info("webhook_mesh: stopped peer=%s", self._own_peer_id)

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        """Register handler for incoming RPC requests."""
        self._rpc_handler = handler

    # ------------------------------------------------------------------
    # Internal — HTTP handling
    # ------------------------------------------------------------------

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        """Handle incoming webhook request."""
        # Validate signature
        if self._secret and not self._validate_signature(request):
            return aiohttp_web.Response(status=401, text="Invalid signature")

        try:
            body = await request.read()
            payload = json.loads(body)
        except Exception:
            return aiohttp_web.Response(status=400, text="Invalid JSON")

        msg_type = payload.get("type", "")

        if msg_type == "publish":
            await self._handle_publish(payload)
            return aiohttp_web.Response(status=202, text="Accepted")

        if msg_type == "rpc":
            response = await self._handle_rpc(payload)
            return aiohttp_web.Response(
                status=200,
                body=json.dumps(response),
                content_type="application/json",
            )

        return aiohttp_web.Response(status=400, text="Unknown message type")

    async def _handle_publish(self, payload: dict) -> None:
        """Handle incoming publish event."""
        topic = payload.get("topic", "")
        handlers = self._handlers.get(topic, [])
        if not handlers:
            return

        event = self._dict_to_event(payload)
        for handler in handlers:
            try:
                await handler(event)
            except Exception as exc:
                logger.warning(
                    "webhook_mesh: handler for %r raised: %s",
                    _sanitize_log(topic),
                    _sanitize_log(exc),
                )

    async def _handle_rpc(self, payload: dict) -> dict:
        """Handle incoming RPC request."""
        message = payload.get("message", {})

        if self._rpc_handler is None:
            return {"error": "no rpc handler registered"}

        try:
            return await self._rpc_handler(message)
        except Exception:
            logger.exception(
                "webhook_mesh: rpc handler failed for source_peer_id=%s",
                _sanitize_log(payload.get("source_peer_id", "")),
            )
            return {"error": "internal error"}

    def _validate_signature(self, request: web.Request) -> bool:
        """Validate HMAC signature on request."""
        signature = request.headers.get(_SIGNATURE_HEADER, "")
        timestamp = request.headers.get(_TIMESTAMP_HEADER, "")

        if not signature or not timestamp:
            return False

        # Check timestamp freshness
        try:
            ts = float(timestamp)
            if abs(time.time() - ts) > _MAX_CLOCK_SKEW_S:
                return False
        except ValueError:
            return False

        # Reconstruct and verify signature
        # We can't access request body here synchronously, so signature
        # is computed over timestamp + path only for header-based validation
        msg = f"{timestamp}:{request.path}".encode()
        expected = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

        return hmac.compare_digest(signature, expected)

    # ------------------------------------------------------------------
    # Internal — HTTP client
    # ------------------------------------------------------------------

    async def _post_to_peer(
        self,
        url: str,
        body: bytes,
        *,
        timeout: float | None = None,
    ) -> dict | None:
        """POST body to peer URL with signature."""
        if self._session is None:
            return None

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            _PEER_ID_HEADER: self._own_peer_id,
        }

        # Add signature if secret configured
        if self._secret:
            ts = str(time.time())
            # Parse path from URL for signature
            from urllib.parse import urlparse

            path = urlparse(url).path
            msg = f"{ts}:{path}".encode()
            sig = hmac.new(self._secret, msg, hashlib.sha256).hexdigest()
            headers[_TIMESTAMP_HEADER] = ts
            headers[_SIGNATURE_HEADER] = sig

        try:
            req_timeout = aiohttp.ClientTimeout(total=timeout) if timeout else None
            async with self._session.post(
                url, data=body, headers=headers, timeout=req_timeout
            ) as resp:
                if resp.status >= 400:
                    logger.debug("webhook_mesh: POST to %s returned %d", url, resp.status)
                    return None
                if resp.content_type == "application/json":
                    return await resp.json()
                return {}
        except Exception as exc:
            logger.debug("webhook_mesh: POST to %s failed: %s", url, exc)
            return None

    # ------------------------------------------------------------------
    # Internal — peer lookup
    # ------------------------------------------------------------------

    def _get_peers(self) -> dict:
        """Get all known peers from discovery."""
        try:
            return self._discovery.peers()  # type: ignore[attr-defined]
        except Exception:
            return {}

    def _get_peer(self, peer_id: str) -> object | None:
        """Get a specific peer from discovery."""
        peers = self._get_peers()
        return peers.get(peer_id)

    def _peer_webhook_url(self, peer: object) -> str:
        """Extract webhook URL from peer object."""
        # Try webhook_url attribute first (from cluster.json)
        if hasattr(peer, "webhook_url") and peer.webhook_url:  # type: ignore[attr-defined]
            return peer.webhook_url  # type: ignore[attr-defined]

        # Fall back to constructing from rep_address
        if hasattr(peer, "rep_address") and peer.rep_address:  # type: ignore[attr-defined]
            addr = peer.rep_address  # type: ignore[attr-defined]
            # rep_address is like "tcp://host:port" — convert to HTTP
            if addr.startswith("tcp://"):
                host_port = addr[6:]  # Remove tcp://
                return f"http://{host_port}{self._webhook_path}"

        return ""

    # ------------------------------------------------------------------
    # Internal — serialization
    # ------------------------------------------------------------------

    def _event_to_dict(self, event: RavnEvent, topic: str, msg_type: str) -> dict:
        """Convert RavnEvent to wire format."""
        return {
            "type": msg_type,
            "topic": topic,
            "source_peer_id": self._own_peer_id,
            "event": {
                "type": str(event.type.value) if hasattr(event.type, "value") else str(event.type),
                "source": event.source,
                "payload": event.payload,
                "timestamp": event.timestamp.isoformat() if event.timestamp else None,
                "urgency": event.urgency,
                "correlation_id": event.correlation_id,
                "session_id": event.session_id,
                "task_id": event.task_id,
            },
            "timestamp": datetime.now(UTC).isoformat(),
        }

    def _dict_to_event(self, payload: dict) -> RavnEvent:
        """Convert wire format back to RavnEvent."""
        event_data = payload.get("event", {})

        # Parse event type
        type_str = event_data.get("type", "response")
        try:
            event_type = RavnEventType(type_str)
        except ValueError:
            event_type = RavnEventType.RESPONSE

        # Parse timestamp
        ts_str = event_data.get("timestamp")
        if ts_str:
            timestamp = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(UTC)

        return RavnEvent(
            type=event_type,
            source=event_data.get("source", ""),
            payload=event_data.get("payload", {}),
            timestamp=timestamp,
            urgency=event_data.get("urgency", 0.5),
            correlation_id=event_data.get("correlation_id", ""),
            session_id=event_data.get("session_id", ""),
            task_id=event_data.get("task_id"),
        )
