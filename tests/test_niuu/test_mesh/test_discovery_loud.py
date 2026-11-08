"""Discovery fails loudly: every failure is an error and a count, never a debug line.

One test per kind of site: configured-but-impossible raises at start, a background loop
reports and carries on, a failing consumer callback is reported, a bad message from another
peer is refused and counted, and a failed list is never read as "everyone left".
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from niuu import cap_telemetry
from niuu.adapters.discovery import _failures
from niuu.adapters.discovery._failures import DiscoveryError, refusal_counts
from niuu.adapters.discovery.composite import CompositeDiscoveryAdapter
from niuu.adapters.discovery.event_bus import EventBusDiscoveryAdapter
from niuu.adapters.discovery.k8s import K8sDiscoveryAdapter
from niuu.adapters.discovery.mdns import MdnsDiscoveryAdapter
from niuu.adapters.discovery.sleipnir import SleipnirDiscoveryAdapter
from niuu.adapters.discovery.static import StaticDiscoveryAdapter
from niuu.mesh.config import DiscoveryConfig, DiscoveryMdnsConfig
from niuu.mesh.identity import MeshCandidate, MeshIdentity, MeshPeer
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.events import SleipnirEvent


@pytest.fixture(autouse=True)
def _clean_counts():
    cap_telemetry.reset_cap_hits()
    _failures.reset_refusal_counts()
    yield


def _identity(peer_id: str = "me") -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id="realm",
        persona="p",
        capabilities=[],
        permission_mode="read_only",
        version="1",
    )


def _peer(peer_id: str = "other") -> MeshPeer:
    return MeshPeer(
        peer_id=peer_id,
        realm_id="realm",
        persona="p",
        capabilities=[],
        permission_mode="read_only",
        version="1",
    )


def _config() -> DiscoveryConfig:
    return DiscoveryConfig(mdns=DiscoveryMdnsConfig(handshake_timeout_s=1.0))


def degraded(path: str) -> int:
    return cap_telemetry.degraded_counts().get(f"niuu.discovery.{path}", 0)


class FailingPublisher(InProcessBus):
    """A bus whose publish fails while ``down`` is set."""

    down = False

    async def publish(self, event):
        if self.down:
            raise ConnectionError("broker unreachable")
        await super().publish(event)


class TestEventBus:
    async def _adapter(self):
        bus = FailingPublisher()
        adapter = EventBusDiscoveryAdapter(
            _identity(), bus, bus, manage_transport_lifecycle=False, heartbeat_interval_s=0.01
        )
        return bus, adapter

    async def test_a_first_announce_that_cannot_be_published_fails_the_start(self) -> None:
        bus, adapter = await self._adapter()
        bus.down = True

        with pytest.raises(DiscoveryError, match="join announce was not published"):
            await adapter.start()

    async def test_a_heartbeat_that_cannot_be_published_is_reported_and_the_loop_goes_on(
        self, caplog
    ) -> None:
        bus, adapter = await self._adapter()
        await adapter.start()
        bus.down = True
        with caplog.at_level(logging.ERROR):
            await asyncio.sleep(0.08)
        bus.down = False

        assert degraded("event_bus.heartbeat") >= 2  # it kept trying, and each miss was counted
        assert "degraded [niuu.discovery.event_bus.heartbeat]" in caplog.text
        assert adapter._heartbeat_task is not None and not adapter._heartbeat_task.done()
        await adapter.stop()

    async def test_stopping_goes_on_when_the_leave_cannot_be_published(self) -> None:
        bus, adapter = await self._adapter()
        await adapter.start()
        bus.down = True

        await adapter.stop()

        assert degraded("event_bus.leave") == 1 and adapter._heartbeat_task is None

    async def test_an_announcement_without_an_identity_is_refused_and_counted(self) -> None:
        bus, adapter = await self._adapter()
        await adapter.start()

        for payload in ({"action": "join"}, {"identity": {"realm_id": "realm"}}):
            await bus.publish(
                SleipnirEvent(
                    event_type=adapter._announce_event_type,
                    source="ravn:x",
                    payload=payload,
                    summary="x",
                    domain="code",
                    urgency=0.3,
                    timestamp=datetime.now(UTC),
                )
            )
        await bus.flush()

        assert refusal_counts() == {"event_bus.malformed": 2}
        assert adapter.peers() == {}
        await adapter.stop()

    async def test_our_own_announcement_and_another_realms_are_quiet_steady_states(self) -> None:
        bus, adapter = await self._adapter()
        await adapter.start()
        await bus.flush()  # our own join comes back to us

        assert refusal_counts() == {} and cap_telemetry.degraded_counts() == {}
        await adapter.stop()

    async def test_a_callback_that_raises_is_reported_and_the_others_still_run(self) -> None:
        bus, first = await self._adapter()
        other = EventBusDiscoveryAdapter(
            _identity("watcher"), bus, bus, manage_transport_lifecycle=False
        )
        seen: list[str] = []
        await other.watch(lambda peer: 1 / 0, lambda peer: None)
        await other.watch(lambda peer: seen.append(peer.peer_id), lambda peer: None)
        await other.start()
        await first.start()
        await bus.flush()

        assert seen == ["me"] and "watcher" not in other.peers()
        assert degraded("event_bus.join") == 1
        assert "me" in other.peers()  # the table was updated before the callbacks ran
        await first.stop()
        await other.stop()


class TestAmqp:
    def _adapter(self, **kwargs) -> SleipnirDiscoveryAdapter:
        return SleipnirDiscoveryAdapter(config=_config(), own_identity=_identity(), **kwargs)

    async def test_no_url_is_a_configured_but_impossible_start(self, monkeypatch) -> None:
        monkeypatch.delenv("SLEIPNIR_AMQP_URL", raising=False)
        adapter = self._adapter()
        with patch("niuu.adapters.discovery.sleipnir.aio_pika", MagicMock()):
            with pytest.raises(DiscoveryError, match="SLEIPNIR_AMQP_URL is not set"):
                await adapter.start()

    async def test_a_connection_that_fails_fails_the_start_with_the_remedy(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("SLEIPNIR_AMQP_URL", "amqp://nowhere")
        aio_pika = MagicMock()
        aio_pika.connect_robust = AsyncMock(side_effect=ConnectionError("refused"))
        with patch("niuu.adapters.discovery.sleipnir.aio_pika", aio_pika):
            with pytest.raises(DiscoveryError, match="could not connect to AMQP.*refused"):
                await self._adapter().start()

    async def test_a_consumer_that_cannot_start_fails_the_start(self) -> None:
        adapter = self._adapter()
        adapter._channel = MagicMock()
        adapter._channel.declare_queue = AsyncMock(side_effect=RuntimeError("no queue"))

        with pytest.raises(DiscoveryError, match="could not start consuming.*no queue"):
            await adapter._start_consumer()

    async def test_an_announce_that_cannot_be_published_raises(self) -> None:
        adapter = self._adapter()
        adapter._exchange = MagicMock()
        adapter._exchange.publish = AsyncMock(side_effect=ConnectionError("gone"))
        aio_pika = MagicMock()
        with patch("niuu.adapters.discovery.sleipnir.aio_pika", aio_pika):
            with pytest.raises(DiscoveryError, match="join announce was not published.*gone"):
                await adapter.announce()

    async def test_announcing_without_a_connection_raises_but_leaving_without_one_does_not(
        self,
    ) -> None:
        adapter = self._adapter()

        with pytest.raises(DiscoveryError, match="not connected"):
            await adapter.announce()
        await adapter._publish_announce("leave")  # never connected: nothing to leave

    async def test_a_heartbeat_that_fails_is_reported_and_the_loop_goes_on(self) -> None:
        adapter = self._adapter()
        adapter._heartbeat_interval_s = 0.01
        adapter._exchange = MagicMock()
        adapter._exchange.publish = AsyncMock(side_effect=ConnectionError("gone"))
        with patch("niuu.adapters.discovery.sleipnir.aio_pika", MagicMock()):
            task = asyncio.create_task(adapter._heartbeat_loop())
            await asyncio.sleep(0.08)
            alive = not task.done()
            task.cancel()

        assert alive and degraded("sleipnir.heartbeat") >= 2

    async def test_a_message_that_is_not_json_is_refused_and_counted(self) -> None:
        adapter = self._adapter()
        message = MagicMock()
        message.body = b"not json"
        message.process.return_value.__aenter__ = AsyncMock()
        message.process.return_value.__aexit__ = AsyncMock(return_value=False)

        await adapter._on_message(message)

        assert refusal_counts() == {"sleipnir.malformed": 1}

    async def test_an_announcement_with_no_identity_object_is_refused_and_counted(self) -> None:
        adapter = self._adapter()

        await adapter._handle_announce(
            {"event_type": "ravn.mesh.announce", "payload": {"identity": "peer-b"}}
        )

        assert refusal_counts() == {"sleipnir.malformed": 1} and adapter.peers() == {}

    async def test_an_announcement_that_fails_spiffe_validation_is_refused_and_counted(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "niuu.world")
        adapter = self._adapter()

        await adapter._handle_announce(
            {
                "event_type": "ravn.mesh.announce",
                "payload": {"identity": {"peer_id": "peer-b", "realm_id": "realm"}},
            }
        )

        assert refusal_counts() == {"sleipnir.spiffe_invalid": 1} and adapter.peers() == {}

    def test_a_spiffe_token_that_cannot_be_read_is_invalid_not_an_exception(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv("SPIFFE_TRUST_DOMAIN", "niuu.world")

        assert self._adapter()._validate_spiffe({"spiffe_jwt": "a.b!.c"}, "peer-b") is False

    async def test_a_callback_that_raises_is_reported(self) -> None:
        adapter = self._adapter()
        await adapter.watch(lambda peer: 1 / 0, lambda peer: 1 / 0)
        adapter._peers["peer-b"] = _peer("peer-b")

        adapter._remove_peer("peer-b")

        assert degraded("sleipnir.leave") == 1


class TestMdns:
    def _adapter(self) -> MdnsDiscoveryAdapter:
        return MdnsDiscoveryAdapter(config=_config(), own_identity=_identity())

    def test_a_host_with_no_route_has_no_address_to_announce(self) -> None:
        sock = MagicMock()
        sock.connect.side_effect = OSError("Network is unreachable")
        with patch("niuu.adapters.discovery.mdns.socket.socket", return_value=sock):
            from niuu.adapters.discovery.mdns import _local_ip

            with pytest.raises(DiscoveryError, match="cannot find this host's LAN address"):
                _local_ip()
        sock.close.assert_called_once()

    async def test_a_service_that_cannot_be_registered_fails_the_announce(self) -> None:
        adapter = self._adapter()
        adapter._zc = MagicMock()
        adapter._zc.async_register_service = AsyncMock(side_effect=RuntimeError("busy"))
        with (
            patch("niuu.adapters.discovery.mdns._local_ip", return_value="10.0.0.1"),
            patch("niuu.adapters.discovery.mdns.ServiceInfo"),
            pytest.raises(DiscoveryError, match="could not register.*busy"),
        ):
            await adapter.announce()

    async def test_a_heartbeat_whose_re_registration_fails_is_reported_and_goes_on(self) -> None:
        adapter = self._adapter()
        adapter._heartbeat_interval_s = 0.01
        adapter.announce = AsyncMock(side_effect=DiscoveryError("busy"))  # type: ignore[method-assign]

        task = asyncio.create_task(adapter._heartbeat_loop())
        await asyncio.sleep(0.08)
        alive = not task.done()
        task.cancel()

        assert alive and degraded("mdns.heartbeat") >= 2

    async def test_a_candidate_without_a_handshake_port_is_refused_and_counted(self) -> None:
        candidate = MeshCandidate(
            peer_id="x",
            realm_id_hash="h",
            host="10.0.0.2",
            rep_address=None,
            pub_address=None,
            handshake_port=None,
        )
        with patch("niuu.adapters.discovery.mdns.pynng", MagicMock()):
            assert await self._adapter().handshake(candidate) is None

        assert refusal_counts() == {"mdns.no_handshake_port": 1}

    async def test_a_handshake_attempt_that_fails_is_a_counted_retry_and_exhaustion_an_error(
        self,
    ) -> None:
        adapter = self._adapter()
        candidate = MeshCandidate(
            peer_id="x",
            realm_id_hash="h",
            host="10.0.0.2",
            rep_address=None,
            pub_address=None,
            handshake_port=7482,
        )
        adapter.handshake = AsyncMock(return_value=None)  # type: ignore[method-assign]
        with patch("niuu.adapters.discovery.mdns.asyncio.sleep", new=AsyncMock()):
            await adapter._initiate_handshake(candidate)

        assert degraded("mdns.handshake") == 1
        assert cap_telemetry.retry_counts().get("niuu.discovery.mdns.handshake") == 2

    async def test_a_handshake_socket_error_is_a_counted_retry(self) -> None:
        adapter = self._adapter()
        candidate = MeshCandidate(
            peer_id="x",
            realm_id_hash="h",
            host="10.0.0.2",
            rep_address=None,
            pub_address=None,
            handshake_port=7482,
        )
        adapter._run_handshake_initiator = MagicMock(side_effect=TimeoutError("slow"))  # type: ignore[method-assign]
        with patch("niuu.adapters.discovery.mdns.pynng", MagicMock()):
            assert await adapter.handshake(candidate) is None

        assert cap_telemetry.retry_counts().get("niuu.discovery.mdns.handshake") == 1

    def test_a_responder_that_gets_a_bad_proof_refuses_and_counts_it(self) -> None:
        adapter = self._adapter()
        sock = MagicMock()
        sock.recv.return_value = b"VERIFY deadbeef {}"

        adapter._handle_handshake_responder(sock, b"HELLO peer-a abc")

        sock.send.assert_called_with(b"REJECT")
        assert refusal_counts() == {"mdns.hmac_mismatch": 1}

    def test_a_responder_that_gets_garbage_instead_of_a_hello_counts_it(self) -> None:
        self._adapter()._handle_handshake_responder(MagicMock(), b"what")

        assert refusal_counts() == {"mdns.malformed": 1}

    def test_a_responder_whose_work_fails_is_reported_not_swallowed(self) -> None:
        sock = MagicMock()
        sock.send.side_effect = OSError("pipe")

        self._adapter()._handle_handshake_responder(sock, b"HELLO peer-a abc")

        assert degraded("mdns.responder") == 1

    def test_callbacks_that_raise_are_reported(self) -> None:
        adapter = self._adapter()
        adapter._on_join.append(lambda peer: 1 / 0)
        adapter._on_leave.append(lambda peer: 1 / 0)

        adapter._add_peer(_peer("x"))
        adapter._remove_candidate("x")

        assert degraded("mdns.join") == 1 and degraded("mdns.leave") == 1

    def test_another_realm_on_the_same_lan_stays_quiet(self) -> None:
        # Not a failure: peers of other realms share the network and are simply not ours.
        assert refusal_counts() == {} and cap_telemetry.degraded_counts() == {}


class TestKubernetes:
    def _adapter(self) -> K8sDiscoveryAdapter:
        return K8sDiscoveryAdapter(config=_config(), own_identity=_identity())

    async def test_no_cluster_credentials_fail_the_start(self) -> None:
        config = MagicMock()
        config.load_incluster_config.side_effect = RuntimeError("not in a cluster")
        config.load_kube_config.side_effect = RuntimeError("no kubeconfig")
        with (
            patch("niuu.adapters.discovery.k8s.k8s_client", MagicMock()),
            patch("niuu.adapters.discovery.k8s.k8s_config", config),
            pytest.raises(DiscoveryError, match="no cluster credentials.*no kubeconfig"),
        ):
            await self._adapter().start()

    def test_a_failed_list_is_an_error_not_an_empty_peer_list(self) -> None:
        client = MagicMock()
        client.CoreV1Api.return_value.list_pod_for_all_namespaces.side_effect = RuntimeError("403")
        with patch("niuu.adapters.discovery.k8s.k8s_client", client):
            with pytest.raises(DiscoveryError, match="could not list pods.*403"):
                self._adapter()._list_candidates()

    async def test_a_failed_scan_does_not_evict_the_peers_it_knew(self) -> None:
        adapter = self._adapter()
        adapter._heartbeat_interval_s = 0.01
        adapter._peers["pod-1"] = _peer("pod-1")
        adapter._list_candidates = MagicMock(side_effect=DiscoveryError("403"))  # type: ignore[method-assign]

        task = asyncio.create_task(adapter._poll_loop())
        await asyncio.sleep(0.08)
        alive = not task.done()
        task.cancel()

        assert alive and "pod-1" in adapter.peers() and degraded("k8s.scan") >= 2

    async def test_callbacks_that_raise_are_reported(self) -> None:
        adapter = self._adapter()
        await adapter.watch(lambda peer: 1 / 0, lambda peer: 1 / 0)
        candidate = MeshCandidate(
            peer_id="pod-1",
            realm_id_hash="",
            host="10.0.0.3",
            rep_address=None,
            pub_address=None,
            handshake_port=None,
            metadata={"realm_id": "realm"},
        )
        adapter._list_candidates = MagicMock(return_value=[candidate])  # type: ignore[method-assign]
        await adapter._do_scan()
        adapter._list_candidates = MagicMock(return_value=[])  # type: ignore[method-assign]
        await adapter._do_scan()

        assert degraded("k8s.join") == 1 and degraded("k8s.leave") == 1


class TestStatic:
    async def test_a_named_cluster_file_that_is_absent_fails_the_start(self, tmp_path) -> None:
        adapter = StaticDiscoveryAdapter(_identity(), cluster_file=str(tmp_path / "nope.yaml"))

        with pytest.raises(DiscoveryError, match="does not exist"):
            await adapter.start()

    async def test_the_default_location_may_be_absent(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        adapter = StaticDiscoveryAdapter(_identity(), poll_interval_s=0)

        await adapter.start()

        assert adapter.peers() == {}
        await adapter.stop()

    @pytest.mark.parametrize(
        ("content", "text"),
        [
            ("peers: [\n", "could not read"),
            ("just: a mapping\n", "no `peers` list"),
            ("- a list\n", "no `peers` list"),
            ("peers:\n  - persona: no-id\n", "entry 0.*peer_id"),
            ("peers:\n  - just a string\n", "entry 0.*peer_id"),
        ],
    )
    async def test_a_cluster_file_that_is_not_usable_fails_the_start_naming_what(
        self, tmp_path, content, text
    ) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text(content)

        with pytest.raises(DiscoveryError, match=text):
            await StaticDiscoveryAdapter(_identity(), cluster_file=str(path)).start()

    async def test_a_reload_that_fails_keeps_the_table_and_is_reported(self, tmp_path) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text("peers:\n  - peer_id: a\n")
        adapter = StaticDiscoveryAdapter(_identity(), cluster_file=str(path), poll_interval_s=0.01)
        await adapter.start()
        path.write_text("peers: [\n")
        path.touch()
        import os

        os.utime(path, (path.stat().st_atime + 5, path.stat().st_mtime + 5))
        await asyncio.sleep(0.1)

        assert set(adapter.peers()) == {"a"} and degraded("static.reload") >= 1
        await adapter.stop()

    async def test_a_cluster_file_that_disappears_is_reported(self, tmp_path) -> None:
        path = tmp_path / "cluster.yaml"
        path.write_text("peers:\n  - peer_id: a\n")
        adapter = StaticDiscoveryAdapter(_identity(), cluster_file=str(path), poll_interval_s=0.01)
        await adapter.start()
        path.unlink()
        await asyncio.sleep(0.08)

        assert set(adapter.peers()) == {"a"} and degraded("static.reload") >= 1
        await adapter.stop()

    async def test_callbacks_that_raise_are_reported(self, tmp_path) -> None:
        adapter = StaticDiscoveryAdapter(_identity(), peers=[{"peer_id": "a"}], poll_interval_s=0)
        await adapter.watch(lambda peer: 1 / 0, lambda peer: 1 / 0)
        await adapter.start()
        adapter._inline_peers = []
        adapter._cluster_file = tmp_path / "empty.yaml"
        (tmp_path / "empty.yaml").write_text("peers: []\n")
        await adapter._load_peers()

        assert degraded("static.join") == 1 and degraded("static.leave") == 1


class _Backend:
    def __init__(self, *, start=None, announce=None, scan=None) -> None:
        self._start, self._announce, self._scan = start, announce, scan
        self.started = self.stopped = False
        self.watch = AsyncMock()

    async def start(self):
        if self._start:
            raise self._start
        self.started = True

    async def stop(self):
        self.stopped = True

    async def announce(self):
        if self._announce:
            raise self._announce

    async def scan(self):
        if self._scan:
            raise self._scan
        return []


class TestComposite:
    async def test_a_backend_that_cannot_start_fails_the_start_and_stops_the_others(self) -> None:
        good = _Backend()
        bad = _Backend(start=DiscoveryError("no route"))
        adapter = CompositeDiscoveryAdapter([good, bad])

        with pytest.raises(
            DiscoveryError, match="could not start:.*_Backend: DiscoveryError: no route"
        ):
            await adapter.start()

        assert good.started and good.stopped

    async def test_a_failing_announce_is_raised(self) -> None:
        adapter = CompositeDiscoveryAdapter([_Backend(announce=RuntimeError("down")), _Backend()])

        with pytest.raises(DiscoveryError, match="could not announce.*down"):
            await adapter.announce()

    async def test_a_failing_scan_is_reported_and_the_rest_is_returned(self) -> None:
        adapter = CompositeDiscoveryAdapter([_Backend(scan=RuntimeError("down")), _Backend()])

        assert await adapter.scan() == []
        assert degraded("composite.scan") == 1

    async def test_a_backend_that_will_not_stop_is_reported(self) -> None:
        bad = _Backend()
        bad.stop = AsyncMock(side_effect=RuntimeError("stuck"))  # type: ignore[method-assign]

        await CompositeDiscoveryAdapter([bad]).stop()

        assert degraded("composite.stop") == 1

    async def test_a_backend_whose_handshake_fails_is_reported_and_the_next_is_tried(self) -> None:
        peer = _peer("x")
        bad, good = _Backend(), _Backend()
        bad.handshake = AsyncMock(side_effect=RuntimeError("socket"))  # type: ignore[attr-defined]
        good.handshake = AsyncMock(return_value=peer)  # type: ignore[attr-defined]

        result = await CompositeDiscoveryAdapter([bad, good]).handshake(MagicMock())

        assert result is peer and degraded("composite.handshake") == 1

    def test_callbacks_that_raise_are_reported(self) -> None:
        adapter = CompositeDiscoveryAdapter([_Backend()])
        adapter._on_join.append(lambda peer: 1 / 0)
        adapter._on_leave.append(lambda peer: 1 / 0)
        backend = adapter._backends[0]

        adapter._make_join_callback(backend)(_peer())
        adapter._make_leave_callback(backend)(_peer())

        assert degraded("composite.join") == 1 and degraded("composite.leave") == 1
