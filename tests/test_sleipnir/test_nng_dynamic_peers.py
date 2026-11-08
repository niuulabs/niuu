"""The nng transport announces the address it really bound, and follows its peers.

Real pynng sockets on loopback ports the OS chose; nothing else is needed.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from sleipnir.adapters.nng_transport import NngPublisher, NngTransport
from tests.test_sleipnir.conftest import make_event

pytest.importorskip("pynng", reason="pynng not installed; skipping nng tests")

LOOPBACK = "tcp://127.0.0.1:0"
EVENT_TYPE = "ravn.tool.complete"


async def _wait_for(predicate, timeout_s: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


class TestAdvertisedAddress:
    async def test_port_zero_is_announced_as_the_port_that_was_bound(self) -> None:
        publisher = NngPublisher(LOOPBACK)
        await publisher.bind()
        try:
            assert publisher.bound_address.startswith("tcp://127.0.0.1:")
            assert not publisher.bound_address.endswith(":0")
            assert publisher.advertised_address == publisher.bound_address
        finally:
            await publisher.stop()

    async def test_a_fixed_port_is_announced_as_it_was_asked_for(self) -> None:
        """pynng's ``port`` attribute is byte-swapped; the announced port must not be."""
        probe = NngPublisher(LOOPBACK)
        await probe.bind()
        port = probe.bound_address.rsplit(":", 1)[1]
        await probe.stop()

        publisher = NngPublisher(f"tcp://127.0.0.1:{port}")
        await publisher.bind()
        try:
            assert publisher.bound_address == f"tcp://127.0.0.1:{port}"
        finally:
            await publisher.stop()

    async def test_bind_twice_is_the_same_socket(self) -> None:
        publisher = NngPublisher(LOOPBACK)
        await publisher.bind()
        first = publisher.bound_address
        await publisher.bind()
        try:
            assert publisher.bound_address == first
        finally:
            await publisher.stop()

    async def test_a_wildcard_bind_announces_the_advertise_host(self) -> None:
        publisher = NngPublisher("tcp://0.0.0.0:0", advertise_host="10.1.2.3")
        await publisher.bind()
        try:
            port = publisher.bound_address.rsplit(":", 1)[1]
            assert publisher.advertised_address == f"tcp://10.1.2.3:{port}"
        finally:
            await publisher.stop()

    async def test_a_wildcard_bind_without_an_advertise_host_is_refused_loudly(self) -> None:
        publisher = NngPublisher("tcp://0.0.0.0:0")
        await publisher.bind()
        try:
            with pytest.raises(ValueError, match="mesh.nng.advertise_host"):
                _ = publisher.advertised_address
        finally:
            await publisher.stop()

    async def test_the_transport_exposes_the_address_before_it_starts(self) -> None:
        transport = NngTransport(LOOPBACK)
        await transport.bind()
        try:
            assert not transport.pub_address.endswith(":0")
        finally:
            await transport._publisher.stop()


class TestFollowingPeers:
    async def test_a_peer_found_by_the_provider_is_dialed_and_heard(self) -> None:
        peers: list[str] = []
        listener = NngTransport(LOOPBACK, peer_addresses=lambda: list(peers))
        speaker = NngTransport(LOOPBACK)
        await speaker.start()
        await listener.start()
        received: list[str] = []

        async def handler(event) -> None:
            received.append(event.event_id)

        try:
            await listener.subscribe([EVENT_TYPE], handler)
            peers.append(speaker.pub_address)
            await listener.refresh_peers()
            event = make_event(event_type=EVENT_TYPE)
            await _wait_for(lambda: speaker.pub_address in listener._subscriber._dialers)
            for _ in range(50):
                await speaker.publish(event)
                if received:
                    break
                await asyncio.sleep(0.05)
            assert received
        finally:
            await listener.stop()
            await speaker.stop()

    async def test_a_peer_that_left_is_hung_up_on(self) -> None:
        peers: list[str] = []
        listener = NngTransport(LOOPBACK, peer_addresses=lambda: list(peers))
        speaker = NngTransport(LOOPBACK)
        await speaker.start()
        await listener.start()
        try:
            peers.append(speaker.pub_address)
            await listener.refresh_peers()
            assert speaker.pub_address in listener._subscriber._dialers

            peers.clear()
            await listener.refresh_peers()

            assert speaker.pub_address not in listener._subscriber._dialers
            assert speaker.pub_address not in listener._subscriber._dialed_addresses
        finally:
            await listener.stop()
            await speaker.stop()

    async def test_its_own_publisher_is_always_dialed(self) -> None:
        transport = NngTransport(LOOPBACK, peer_addresses=lambda: [])
        await transport.start()
        try:
            assert transport.pub_address in transport._subscriber._dialers
        finally:
            await transport.stop()

    async def test_a_failing_provider_keeps_the_peers_it_had_and_says_so(self, caplog) -> None:
        state = {"fail": False}
        speaker = NngTransport(LOOPBACK)

        def provider() -> list[str]:
            if state["fail"]:
                raise RuntimeError("discovery is down")
            return [speaker.pub_address]

        listener = NngTransport(LOOPBACK, peer_addresses=provider)
        await speaker.start()
        await listener.start()
        try:
            await listener.refresh_peers()
            assert speaker.pub_address in listener._subscriber._dialers
            state["fail"] = True
            with caplog.at_level(logging.ERROR, logger="sleipnir.adapters.nng_transport"):
                await listener.refresh_peers()
            assert speaker.pub_address in listener._subscriber._dialers
            assert any("peer address provider failed" in r.message for r in caplog.records)
        finally:
            await listener.stop()
            await speaker.stop()
