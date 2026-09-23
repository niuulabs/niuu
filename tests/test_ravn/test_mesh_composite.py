"""Tests for CompositeMeshAdapter — the all-active multi-transport mesh.

The composite runs every configured transport at once, and a failure on any
of them is raised: a partial broadcast is not a published event, and a
request is never re-sent over another transport after a timeout or fault.
``PeerNotFoundError`` alone is routing ("not on this transport"), so the
tests also pin that the transports only raise it when the peer really is
absent — never to report a discovery failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import pytest

from ravn.adapters.mesh.composite import CompositeMeshAdapter
from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter
from ravn.adapters.mesh.webhook import WebhookMeshAdapter
from ravn.domain.events import RavnEvent
from ravn.ports.mesh import PeerNotFoundError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event() -> RavnEvent:
    return RavnEvent.thought(
        source="peer-a",
        text="hello mesh",
        correlation_id="cid-1",
        session_id="sess-1",
    )


class _Transport:
    """A MeshPort double that records calls and can be told to fail."""

    def __init__(self, name: str, *, reply: dict | None = None) -> None:
        self.name = name
        self.reply = reply if reply is not None else {"via": name}
        self.published: list[tuple[RavnEvent, str]] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.sent: list[str] = []
        self.started = False
        self.stopped = False
        self.rpc_handler: Callable[[dict], Awaitable[dict]] | None = None
        self.fail: dict[str, BaseException] = {}

    def _maybe_fail(self, operation: str) -> None:
        if operation in self.fail:
            raise self.fail[operation]

    async def publish(self, event: RavnEvent, topic: str) -> None:
        self._maybe_fail("publish")
        self.published.append((event, topic))

    async def subscribe(self, topic: str, handler: Callable[[RavnEvent], Awaitable[None]]) -> None:
        self._maybe_fail("subscribe")
        self.subscribed.append(topic)

    async def unsubscribe(self, topic: str) -> None:
        self._maybe_fail("unsubscribe")
        self.unsubscribed.append(topic)

    async def send(
        self,
        target_peer_id: str,
        message: dict,
        *,
        timeout_s: float | None = None,
    ) -> dict:
        self.sent.append(target_peer_id)
        self._maybe_fail("send")
        return self.reply

    async def start(self) -> None:
        self._maybe_fail("start")
        self.started = True

    async def stop(self) -> None:
        self._maybe_fail("stop")
        self.stopped = True

    def set_rpc_handler(self, handler: Callable[[dict], Awaitable[dict]]) -> None:
        self._maybe_fail("set_rpc_handler")
        self.rpc_handler = handler


class _AlphaTransport(_Transport):
    pass


class _BetaTransport(_Transport):
    pass


class _GammaTransport(_Transport):
    pass


def _composite(*transports: Any) -> CompositeMeshAdapter:
    return CompositeMeshAdapter(transports=list(transports), own_peer_id="peer-a")


async def _rpc_handler(message: dict) -> dict:
    return {"echo": message}


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_no_transports_is_rejected(self):
        with pytest.raises(ValueError, match="at least one transport"):
            CompositeMeshAdapter(transports=[], own_peer_id="peer-a")

    def test_transports_are_exposed_as_a_copy(self):
        alpha = _AlphaTransport("alpha")
        composite = _composite(alpha)

        composite.transports.clear()

        assert composite.transports == [alpha]


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


class TestPublish:
    async def test_broadcasts_on_every_transport(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        event = _event()

        await _composite(alpha, beta).publish(event, "code.changed")

        assert alpha.published == [(event, "code.changed")]
        assert beta.published == [(event, "code.changed")]

    async def test_one_failed_transport_raises_although_the_other_delivered(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        error = ConnectionError("nats connection closed")
        beta.fail["publish"] = error

        with pytest.raises(ExceptionGroup) as excinfo:
            await _composite(alpha, beta).publish(_event(), "code.changed")

        group = excinfo.value
        assert "publish to topic 'code.changed' failed on 1 of 2 transports" in str(group)
        assert "peer=peer-a" in str(group)
        assert group.exceptions == (error,)
        assert error.__notes__ == ["mesh transport: _BetaTransport"]
        assert len(alpha.published) == 1

    async def test_every_failed_transport_is_reported(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        alpha.fail["publish"] = OSError("socket closed")
        beta.fail["publish"] = ConnectionError("broker gone")

        with pytest.raises(ExceptionGroup, match="failed on 2 of 2 transports") as excinfo:
            await _composite(alpha, beta).publish(_event(), "code.changed")

        assert [type(e) for e in excinfo.value.exceptions] == [OSError, ConnectionError]

    async def test_a_cancelled_transport_publish_propagates_the_cancellation(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        beta.fail["publish"] = asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await _composite(alpha, beta).publish(_event(), "code.changed")


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------


class TestSubscribe:
    async def test_registers_on_every_transport(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")

        await _composite(alpha, beta).subscribe("code.requested", AsyncMock())

        assert alpha.subscribed == ["code.requested"]
        assert beta.subscribed == ["code.requested"]

    async def test_failure_raises_after_trying_every_transport(self):
        alpha, beta, gamma = _AlphaTransport("a"), _BetaTransport("b"), _GammaTransport("c")
        error = RuntimeError("subscription rejected")
        beta.fail["subscribe"] = error

        with pytest.raises(ExceptionGroup, match="subscribe to topic 'code.requested'") as exc:
            await _composite(alpha, beta, gamma).subscribe("code.requested", AsyncMock())

        assert exc.value.exceptions == (error,)
        assert error.__notes__ == ["mesh transport: _BetaTransport"]
        assert alpha.subscribed == ["code.requested"]
        assert gamma.subscribed == ["code.requested"]


class TestUnsubscribe:
    async def test_unsubscribes_on_every_transport(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")

        await _composite(alpha, beta).unsubscribe("code.requested")

        assert alpha.unsubscribed == ["code.requested"]
        assert beta.unsubscribed == ["code.requested"]

    async def test_failure_raises_after_trying_every_transport(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        error = RuntimeError("unsubscribe rejected")
        alpha.fail["unsubscribe"] = error

        with pytest.raises(ExceptionGroup, match="unsubscribe from topic 'code.requested'") as exc:
            await _composite(alpha, beta).unsubscribe("code.requested")

        assert exc.value.exceptions == (error,)
        assert beta.unsubscribed == ["code.requested"]


# ---------------------------------------------------------------------------
# send
# ---------------------------------------------------------------------------


class TestSend:
    async def test_uses_the_first_transport_that_knows_the_peer(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")

        reply = await _composite(alpha, beta).send("peer-b", {"type": "task_status"})

        assert reply == {"via": "alpha"}
        assert beta.sent == []

    async def test_peer_not_on_a_transport_routes_to_the_next(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        alpha.fail["send"] = PeerNotFoundError("peer-b")

        reply = await _composite(alpha, beta).send("peer-b", {"type": "task_status"})

        assert reply == {"via": "beta"}
        assert alpha.sent == ["peer-b"]

    async def test_peer_on_no_transport_raises_peer_not_found(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        alpha.fail["send"] = PeerNotFoundError("peer-b")
        beta.fail["send"] = PeerNotFoundError("peer-b")

        with pytest.raises(PeerNotFoundError) as excinfo:
            await _composite(alpha, beta).send("peer-b", {"type": "task_status"})

        assert excinfo.value.peer_id == "peer-b"

    async def test_timeout_is_raised_and_not_resent_elsewhere(self):
        """The peer may already be running the request; re-sending would run it twice."""
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        alpha.fail["send"] = TimeoutError("No reply from peer 'peer-b' within 10s")

        with pytest.raises(TimeoutError, match="No reply from peer 'peer-b'"):
            await _composite(alpha, beta).send("peer-b", {"type": "work_request"})

        assert beta.sent == []

    async def test_transport_fault_is_raised_and_not_resent_elsewhere(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        alpha.fail["send"] = ConnectionError("nats connection closed")

        with pytest.raises(ConnectionError, match="nats connection closed"):
            await _composite(alpha, beta).send("peer-b", {"type": "task_dispatch"})

        assert beta.sent == []


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


class TestStart:
    async def test_starts_every_transport_and_wires_the_rpc_handler(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        composite = _composite(alpha, beta)
        composite.set_rpc_handler(_rpc_handler)

        await composite.start()

        assert alpha.started and beta.started
        assert alpha.rpc_handler is _rpc_handler
        assert beta.rpc_handler is _rpc_handler

    async def test_failure_stops_the_started_transports_and_raises(self):
        alpha, beta, gamma = _AlphaTransport("a"), _BetaTransport("b"), _GammaTransport("c")
        error = OSError("address already in use")
        beta.fail["start"] = error

        with pytest.raises(OSError, match="address already in use") as excinfo:
            await _composite(alpha, beta, gamma).start()

        assert excinfo.value.__notes__ == ["mesh transport: _BetaTransport"]
        assert alpha.stopped is True
        assert gamma.started is False

    async def test_failed_rollback_is_raised_on_top_of_the_start_failure(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        beta.fail["start"] = OSError("address already in use")
        alpha.fail["stop"] = RuntimeError("socket already closed")

        with pytest.raises(ExceptionGroup, match="rollback stop failed on 1") as excinfo:
            await _composite(alpha, beta).start()

        assert isinstance(excinfo.value.__context__, OSError)


class TestStop:
    async def test_stops_every_transport(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")

        await _composite(alpha, beta).stop()

        assert alpha.stopped and beta.stopped

    async def test_failure_raises_after_stopping_the_rest(self):
        alpha, beta = _AlphaTransport("alpha"), _BetaTransport("beta")
        error = RuntimeError("socket already closed")
        alpha.fail["stop"] = error

        with pytest.raises(ExceptionGroup, match="stop failed on 1 of 2 transports") as excinfo:
            await _composite(alpha, beta).stop()

        assert excinfo.value.exceptions == (error,)
        assert beta.stopped is True


class TestSetRpcHandler:
    def test_skips_transports_without_rpc_support(self):
        class _PublishOnly:
            async def publish(self, event: RavnEvent, topic: str) -> None:
                return None

        alpha = _AlphaTransport("alpha")

        _composite(_PublishOnly(), alpha).set_rpc_handler(_rpc_handler)

        assert alpha.rpc_handler is _rpc_handler

    def test_failure_propagates(self):
        alpha = _AlphaTransport("alpha")
        alpha.fail["set_rpc_handler"] = RuntimeError("handler already bound")

        with pytest.raises(RuntimeError, match="handler already bound"):
            _composite(alpha).set_rpc_handler(_rpc_handler)


# ---------------------------------------------------------------------------
# Peer lookup: PeerNotFoundError must mean "not on this transport"
# ---------------------------------------------------------------------------


class _BrokenDiscovery:
    def peers(self) -> dict:
        raise ConnectionError("discovery backend unreachable")


class _Discovery:
    def __init__(self, peers: dict) -> None:
        self._peers = peers

    def peers(self) -> dict:
        return self._peers


def _sleipnir(discovery: Any) -> SleipnirMeshAdapter:
    return SleipnirMeshAdapter(
        publisher=AsyncMock(),
        subscriber=AsyncMock(),
        own_peer_id="peer-a",
        discovery=discovery,
    )


class TestPeerLookupFailuresAreNotRouting:
    async def test_sleipnir_discovery_failure_propagates(self):
        with pytest.raises(ConnectionError, match="discovery backend unreachable"):
            await _sleipnir(_BrokenDiscovery()).send("peer-b", {"type": "task_status"})

    async def test_sleipnir_unknown_peer_is_peer_not_found(self):
        with pytest.raises(PeerNotFoundError):
            await _sleipnir(_Discovery({})).send("peer-b", {"type": "task_status"})

    async def test_composite_does_not_route_around_a_discovery_failure(self):
        fallback = _BetaTransport("beta")

        with pytest.raises(ConnectionError, match="discovery backend unreachable"):
            await _composite(_sleipnir(_BrokenDiscovery()), fallback).send(
                "peer-b", {"type": "task_status"}
            )

        assert fallback.sent == []

    async def test_webhook_discovery_failure_propagates(self):
        adapter = WebhookMeshAdapter(own_peer_id="peer-a", discovery=_BrokenDiscovery())
        adapter._session = object()  # type: ignore[assignment]  # started; no HTTP is sent

        with pytest.raises(ConnectionError, match="discovery backend unreachable"):
            await adapter.send("peer-b", {"type": "task_status"})

    async def test_webhook_without_discovery_knows_no_peer(self):
        adapter = WebhookMeshAdapter(own_peer_id="peer-a", discovery=None)
        adapter._session = object()  # type: ignore[assignment]  # started; no HTTP is sent

        with pytest.raises(PeerNotFoundError):
            await adapter.send("peer-b", {"type": "task_status"})
