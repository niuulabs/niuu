"""Tests for NIU-538 flock discovery — DiscoveryPort, models, adapters."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from ravn.adapters.discovery._identity import (
    load_or_create_peer_id,
    load_or_create_realm_key,
    realm_id_from_key,
    realm_id_hash,
)
from ravn.adapters.discovery.composite import CompositeDiscoveryAdapter
from ravn.adapters.discovery.k8s import K8sDiscoveryAdapter
from ravn.adapters.discovery.mdns import MdnsDiscoveryAdapter, _hmac_hex
from ravn.adapters.discovery.sleipnir import SleipnirDiscoveryAdapter
from ravn.config import DiscoveryConfig, DiscoveryMdnsConfig, SleipnirConfig
from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer
from ravn.ports.discovery import DiscoveryPort

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_identity(
    peer_id: str = "peer-a",
    realm_id: str = "realm-xyz",
) -> RavnIdentity:
    return RavnIdentity(
        peer_id=peer_id,
        realm_id=realm_id,
        persona="test-agent",
        capabilities=["bash", "file"],
        permission_mode="workspace_write",
        version="0.1.0",
        rep_address="tcp://127.0.0.1:7481",
        pub_address="tcp://127.0.0.1:7480",
    )


def _make_peer(
    peer_id: str = "peer-b",
    trust_level: str = "verified",
    status: str = "idle",
) -> RavnPeer:
    now = datetime.now(UTC)
    return RavnPeer(
        peer_id=peer_id,
        realm_id="realm-xyz",
        persona="test-peer",
        capabilities=["bash"],
        permission_mode="workspace_write",
        version="0.1.0",
        rep_address="tcp://127.0.0.1:7491",
        pub_address="tcp://127.0.0.1:7490",
        trust_level=trust_level,  # type: ignore[arg-type]
        first_seen=now,
        last_seen=now,
        last_heartbeat=now,
        status=status,  # type: ignore[arg-type]
        task_count=0,
    )


def _make_candidate(peer_id: str = "peer-b", realm_id_hash_val: str = "abc123") -> RavnCandidate:
    return RavnCandidate(
        peer_id=peer_id,
        realm_id_hash=realm_id_hash_val,
        host="127.0.0.1",
        rep_address="tcp://127.0.0.1:7491",
        pub_address="tcp://127.0.0.1:7490",
        handshake_port=7492,
        metadata={},
    )


def _make_discovery_config(**kwargs: Any) -> DiscoveryConfig:
    return DiscoveryConfig(
        heartbeat_interval_s=kwargs.get("heartbeat_interval_s", 30.0),
        peer_ttl_s=kwargs.get("peer_ttl_s", 90.0),
        mdns=DiscoveryMdnsConfig(handshake_timeout_s=5.0),
    )


# ---------------------------------------------------------------------------
# Domain model tests
# ---------------------------------------------------------------------------


class TestRavnCandidate:
    def test_fields(self) -> None:
        c = _make_candidate("peer-x", "deadbeef")
        assert c.peer_id == "peer-x"
        assert c.realm_id_hash == "deadbeef"
        assert c.host == "127.0.0.1"
        assert c.handshake_port == 7492

    def test_metadata_default_empty(self) -> None:
        c = RavnCandidate(
            peer_id="p",
            realm_id_hash="h",
            host="h",
            rep_address=None,
            pub_address=None,
            handshake_port=None,
        )
        assert c.metadata == {}


class TestRavnIdentity:
    def test_optional_fields_default_none(self) -> None:
        ident = RavnIdentity(
            peer_id="p",
            realm_id="r",
            persona="ag",
            capabilities=[],
            permission_mode="read_only",
            version="1.0",
        )
        assert ident.rep_address is None
        assert ident.pub_address is None
        assert ident.spiffe_id is None
        assert ident.sleipnir_routing_key is None


class TestRavnPeer:
    def test_extends_identity(self) -> None:
        peer = _make_peer()
        assert peer.peer_id == "peer-b"
        assert peer.trust_level == "verified"
        assert peer.status == "idle"
        assert peer.task_count == 0

    def test_rep_pub_addresses(self) -> None:
        peer = _make_peer()
        assert peer.rep_address == "tcp://127.0.0.1:7491"
        assert peer.pub_address == "tcp://127.0.0.1:7490"

    def test_status_busy(self) -> None:
        peer = _make_peer(status="busy")
        assert peer.status == "busy"

    def test_capabilities_list(self) -> None:
        peer = _make_peer()
        assert "bash" in peer.capabilities


# ---------------------------------------------------------------------------
# DiscoveryPort protocol tests
# ---------------------------------------------------------------------------


class TestDiscoveryPortProtocol:
    def test_mdns_adapter_satisfies_protocol(self) -> None:
        config = _make_discovery_config()
        identity = _make_identity()
        adapter = MdnsDiscoveryAdapter(config=config, own_identity=identity)
        assert isinstance(adapter, DiscoveryPort)

    def test_sleipnir_adapter_satisfies_protocol(self) -> None:
        config = _make_discovery_config()
        identity = _make_identity()
        sc = SleipnirConfig()
        adapter = SleipnirDiscoveryAdapter(config=config, sleipnir_config=sc, own_identity=identity)
        assert isinstance(adapter, DiscoveryPort)

    def test_k8s_adapter_satisfies_protocol(self) -> None:
        config = _make_discovery_config()
        identity = _make_identity()
        adapter = K8sDiscoveryAdapter(config=config, own_identity=identity)
        assert isinstance(adapter, DiscoveryPort)

    def test_composite_adapter_satisfies_protocol(self) -> None:
        adapter = CompositeDiscoveryAdapter(backends=[])
        assert isinstance(adapter, DiscoveryPort)


# ---------------------------------------------------------------------------
# Identity persistence tests
# ---------------------------------------------------------------------------


class TestIdentityPersistence:
    def test_load_or_create_peer_id_creates_on_first_run(self, tmp_path: Path) -> None:
        with patch("ravn.adapters.discovery._identity._ravn_dir", return_value=tmp_path):
            peer_id = load_or_create_peer_id()
        assert uuid.UUID(peer_id)  # valid UUID
        assert (tmp_path / "peer_id").exists()

    def test_load_or_create_peer_id_stable_across_calls(self, tmp_path: Path) -> None:
        with patch("ravn.adapters.discovery._identity._ravn_dir", return_value=tmp_path):
            a = load_or_create_peer_id()
            b = load_or_create_peer_id()
        assert a == b

    def test_load_or_create_realm_key_creates_32_bytes(self, tmp_path: Path) -> None:
        with patch("ravn.adapters.discovery._identity._ravn_dir", return_value=tmp_path):
            key = load_or_create_realm_key()
        assert isinstance(key, bytes)
        assert len(key) == 32
        assert (tmp_path / "realm.key").exists()

    def test_load_or_create_realm_key_stable(self, tmp_path: Path) -> None:
        with patch("ravn.adapters.discovery._identity._ravn_dir", return_value=tmp_path):
            a = load_or_create_realm_key()
            b = load_or_create_realm_key()
        assert a == b

    def test_realm_id_hash_prefix_16(self) -> None:
        key = b"x" * 32
        h = realm_id_hash(key)
        assert len(h) == 16
        assert h == hashlib.sha256(key).hexdigest()[:16]

    def test_realm_id_from_key_hex(self) -> None:
        key = bytes.fromhex("deadbeef" * 8)
        rid = realm_id_from_key(key)
        assert rid == key.hex()


# ---------------------------------------------------------------------------
# HMAC helper tests
# ---------------------------------------------------------------------------


class TestHmacHelper:
    def test_hmac_hex_deterministic(self) -> None:
        key = b"secret"
        a = _hmac_hex(key, "ravn-handshake", "nonce_a", "peer_a", "peer_b")
        b = _hmac_hex(key, "ravn-handshake", "nonce_a", "peer_a", "peer_b")
        assert a == b

    def test_hmac_hex_different_parts_differ(self) -> None:
        key = b"secret"
        a = _hmac_hex(key, "ravn-handshake", "nonce_a", "peer_a", "peer_b")
        b = _hmac_hex(key, "ravn-handshake", "nonce_b", "peer_a", "peer_b")
        assert a != b

    def test_hmac_hex_different_key_differs(self) -> None:
        a = _hmac_hex(b"key_a", "ravn-handshake", "n", "a", "b")
        b = _hmac_hex(b"key_b", "ravn-handshake", "n", "a", "b")
        assert a != b

    def test_hmac_compare_digest_constant_time(self) -> None:
        key = b"secret"
        h = _hmac_hex(key, "ravn-handshake", "n", "a", "b")
        assert hmac.compare_digest(h, h)
        assert not hmac.compare_digest(h, "0" * len(h))


# ---------------------------------------------------------------------------
# MdnsDiscoveryAdapter tests (no real zeroconf/nng required)
# ---------------------------------------------------------------------------


class TestMdnsDiscoveryAdapter:
    def _make_adapter(self, realm_key: bytes | None = None) -> MdnsDiscoveryAdapter:
        config = _make_discovery_config()
        identity = _make_identity()
        adapter = MdnsDiscoveryAdapter(config=config, own_identity=identity)
        if realm_key is not None:
            adapter._realm_key = realm_key
        return adapter

    def test_peers_initially_empty(self) -> None:
        adapter = self._make_adapter()
        assert adapter.peers() == {}

    def test_peers_returns_dict_copy(self) -> None:
        adapter = self._make_adapter()
        peer = _make_peer("peer-x")
        adapter._peers["peer-x"] = peer
        result = adapter.peers()
        result["injected"] = peer  # mutate copy
        assert "injected" not in adapter._peers

    @pytest.mark.asyncio
    async def test_watch_registers_callbacks(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        leaves: list[RavnPeer] = []
        await adapter.watch(on_join=joins.append, on_leave=leaves.append)
        assert len(adapter._on_join) == 1
        assert len(adapter._on_leave) == 1

    def test_add_peer_fires_on_join(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        adapter._on_join.append(joins.append)
        peer = _make_peer("peer-x")
        adapter._add_peer(peer)
        assert len(joins) == 1
        assert joins[0].peer_id == "peer-x"

    def test_add_peer_not_duplicate_join(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        adapter._on_join.append(joins.append)
        peer = _make_peer("peer-x")
        adapter._add_peer(peer)
        adapter._add_peer(peer)  # second add — already in peers
        assert len(joins) == 1

    def test_remove_candidate_fires_on_leave(self) -> None:
        adapter = self._make_adapter()
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)
        peer = _make_peer("peer-x")
        adapter._peers["peer-x"] = peer
        adapter._remove_candidate("peer-x")
        assert len(leaves) == 1
        assert "peer-x" not in adapter._peers

    def test_remove_candidate_missing_peer_noop(self) -> None:
        adapter = self._make_adapter()
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)
        adapter._remove_candidate("nonexistent")
        assert leaves == []

    def test_evict_stale_peers(self) -> None:
        adapter = self._make_adapter()
        adapter._peer_ttl_s = 10.0
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)

        stale_peer = _make_peer("stale")
        stale_peer.last_heartbeat = datetime.now(UTC) - timedelta(seconds=20)
        fresh_peer = _make_peer("fresh")

        adapter._peers["stale"] = stale_peer
        adapter._peers["fresh"] = fresh_peer

        adapter._evict_stale_peers()

        assert "stale" not in adapter._peers
        assert "fresh" in adapter._peers
        assert len(leaves) == 1
        assert leaves[0].peer_id == "stale"

    def test_update_peer_heartbeat(self) -> None:
        adapter = self._make_adapter()
        peer = _make_peer("peer-x")
        adapter._peers["peer-x"] = peer
        adapter.update_peer_heartbeat("peer-x", status="busy", task_count=3)
        assert adapter._peers["peer-x"].status == "busy"
        assert adapter._peers["peer-x"].task_count == 3

    def test_update_peer_heartbeat_missing_peer_noop(self) -> None:
        adapter = self._make_adapter()
        adapter.update_peer_heartbeat("nonexistent")  # should not raise

    def test_realm_mismatch_silently_ignored(self) -> None:
        """Service with different realm hash must be ignored without error."""
        adapter = self._make_adapter(realm_key=b"realm_a" * 5)
        joins: list[RavnPeer] = []
        adapter._on_join.append(joins.append)

        # Candidate has realm hash derived from a different key
        other_key = b"realm_b" * 5
        other_hash = realm_id_hash(other_key)
        candidate = RavnCandidate(
            peer_id="foreign-peer",
            realm_id_hash=other_hash,
            host="127.0.0.1",
            rep_address=None,
            pub_address=None,
            handshake_port=7492,
        )
        # Should not add this peer to the verified table
        assert candidate.peer_id not in adapter._peers
        assert len(joins) == 0

    def test_build_txt_records_includes_required_fields(self) -> None:
        adapter = self._make_adapter()
        txt = adapter._build_txt_records()
        assert "realm_id" in txt
        assert "peer_id" in txt
        assert "persona" in txt
        assert "handshake_port" in txt
        # rep_addr and pub_addr when addresses are set
        assert "rep_addr" in txt
        assert "pub_addr" in txt

    def test_realm_id_hash_in_txt_is_16_hex_chars(self) -> None:
        adapter = self._make_adapter()
        txt = adapter._build_txt_records()
        assert len(txt["realm_id"]) == 16

    @pytest.mark.asyncio
    async def test_own_identity_returns_identity(self) -> None:
        adapter = self._make_adapter()
        identity = await adapter.own_identity()
        assert identity.peer_id == "peer-a"

    @pytest.mark.asyncio
    async def test_scan_returns_current_candidates(self) -> None:
        adapter = self._make_adapter()
        candidate = _make_candidate()
        adapter._candidates["peer-b"] = candidate
        result = await adapter.scan()
        assert len(result) == 1
        assert result[0].peer_id == "peer-b"

    @pytest.mark.asyncio
    async def test_handshake_returns_none_without_pynng(self) -> None:
        adapter = self._make_adapter()
        candidate = _make_candidate()
        with patch("ravn.adapters.discovery.mdns.pynng", None):
            result = await adapter.handshake(candidate)
        assert result is None

    @pytest.mark.asyncio
    async def test_handshake_returns_none_no_handshake_port(self) -> None:
        adapter = self._make_adapter()
        candidate = _make_candidate()
        candidate.handshake_port = None
        result = await adapter.handshake(candidate)
        assert result is None

    @pytest.mark.asyncio
    async def test_start_skips_without_zeroconf(self) -> None:
        adapter = self._make_adapter()
        with patch("ravn.adapters.discovery.mdns.AsyncZeroconf", None):
            await adapter.start()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_noop_when_not_started(self) -> None:
        adapter = self._make_adapter()
        await adapter.stop()  # no error

    def test_peer_from_identity_dict(self) -> None:
        adapter = self._make_adapter()
        raw = {
            "peer_id": "peer-z",
            "realm_id": "realm-xyz",
            "persona": "test",
            "capabilities": ["bash"],
            "permission_mode": "read_only",
            "version": "1.0",
            "rep_address": "tcp://10.0.0.1:7481",
            "pub_address": "tcp://10.0.0.1:7480",
        }
        peer = adapter._peer_from_identity_dict(raw, candidate=None)
        assert peer.peer_id == "peer-z"
        assert peer.trust_level == "verified"
        assert peer.rep_address == "tcp://10.0.0.1:7481"


# ---------------------------------------------------------------------------
# Handshake protocol unit tests (in-process, no real nng)
# ---------------------------------------------------------------------------


class TestHandshakeProtocol:
    """Test the HMAC challenge-response logic in isolation."""

    def _run_initiator(
        self,
        realm_key: bytes,
        own_id: str,
        peer_id: str,
        nonce_a: str,
        nonce_b: str,
        challenge_hmac: str,
        accept_identity: dict,
    ) -> RavnPeer | None:
        """Simulate what _run_handshake_initiator does, using a mock socket."""
        from ravn.adapters.discovery.mdns import _HANDSHAKE_PREFIX, _hmac_hex

        # Verify challenge HMAC
        expected = _hmac_hex(realm_key, _HANDSHAKE_PREFIX, nonce_a, own_id, peer_id)
        if not hmac.compare_digest(challenge_hmac, expected):
            return None

        config = _make_discovery_config()
        identity = _make_identity(peer_id=own_id)
        adapter = MdnsDiscoveryAdapter(config=config, own_identity=identity)
        adapter._realm_key = realm_key
        candidate = _make_candidate(peer_id=peer_id)
        return adapter._peer_from_identity_dict(accept_identity, candidate)

    def test_correct_realm_produces_peer(self) -> None:
        realm_key = b"shared-secret-key-32-bytes-long!"
        own_id, peer_id = "peer-a", "peer-b"
        nonce_a, nonce_b = "nonce_aaa", "nonce_bbb"
        from ravn.adapters.discovery.mdns import _HANDSHAKE_PREFIX, _hmac_hex

        challenge_hmac = _hmac_hex(realm_key, _HANDSHAKE_PREFIX, nonce_a, own_id, peer_id)
        accept_identity = {
            "peer_id": peer_id,
            "realm_id": "realm-xyz",
            "persona": "test",
            "capabilities": ["bash"],
            "permission_mode": "read_only",
            "version": "0.1.0",
        }
        peer = self._run_initiator(
            realm_key, own_id, peer_id, nonce_a, nonce_b, challenge_hmac, accept_identity
        )
        assert peer is not None
        assert peer.peer_id == peer_id

    def test_wrong_realm_key_rejected(self) -> None:
        realm_key_a = b"realm-key-aaa-32-bytes-padding!!"
        realm_key_b = b"realm-key-bbb-32-bytes-padding!!"
        own_id, peer_id = "peer-a", "peer-b"
        nonce_a, nonce_b = "nonce_aaa", "nonce_bbb"
        from ravn.adapters.discovery.mdns import _HANDSHAKE_PREFIX, _hmac_hex

        # B signs with its key, not the shared key
        wrong_hmac = _hmac_hex(realm_key_b, _HANDSHAKE_PREFIX, nonce_a, own_id, peer_id)
        accept_identity: dict = {}
        peer = self._run_initiator(
            realm_key_a, own_id, peer_id, nonce_a, nonce_b, wrong_hmac, accept_identity
        )
        assert peer is None


# ---------------------------------------------------------------------------
# SleipnirDiscoveryAdapter tests
# ---------------------------------------------------------------------------


class TestSleipnirDiscoveryAdapter:
    def _make_adapter(self) -> SleipnirDiscoveryAdapter:
        config = _make_discovery_config()
        sc = SleipnirConfig()
        identity = _make_identity()
        return SleipnirDiscoveryAdapter(config=config, sleipnir_config=sc, own_identity=identity)

    def test_peers_initially_empty(self) -> None:
        adapter = self._make_adapter()
        assert adapter.peers() == {}

    @pytest.mark.asyncio
    async def test_own_identity(self) -> None:
        adapter = self._make_adapter()
        ident = await adapter.own_identity()
        assert ident.peer_id == "peer-a"

    @pytest.mark.asyncio
    async def test_scan_returns_empty(self) -> None:
        adapter = self._make_adapter()
        result = await adapter.scan()
        assert result == []

    @pytest.mark.asyncio
    async def test_handshake_returns_none(self) -> None:
        adapter = self._make_adapter()
        candidate = _make_candidate()
        result = await adapter.handshake(candidate)
        assert result is None

    @pytest.mark.asyncio
    async def test_watch_registers_callbacks(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        await adapter.watch(on_join=joins.append, on_leave=lambda _: None)
        assert len(adapter._on_join) == 1

    @pytest.mark.asyncio
    async def test_handle_announce_join_adds_peer(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        adapter._on_join.append(joins.append)

        raw = {
            "event_type": "ravn.mesh.announce",
            "source": "ravn:peer-b",
            "payload": {
                "identity": {
                    "peer_id": "peer-b",
                    "realm_id": "realm-xyz",
                    "persona": "worker",
                    "capabilities": ["bash"],
                    "permission_mode": "read_only",
                    "version": "0.1.0",
                    "rep_address": "tcp://10.0.0.2:7481",
                    "pub_address": "tcp://10.0.0.2:7480",
                },
                "action": "join",
                "status": "idle",
                "task_count": 0,
            },
        }
        await adapter._handle_announce(raw)
        assert "peer-b" in adapter._peers
        assert adapter._peers["peer-b"].trust_level == "verified"
        assert len(joins) == 1

    @pytest.mark.asyncio
    async def test_handle_announce_own_peer_ignored(self) -> None:
        adapter = self._make_adapter()
        raw = {
            "event_type": "ravn.mesh.announce",
            "source": "ravn:peer-a",
            "payload": {
                "identity": {"peer_id": "peer-a"},
                "action": "join",
            },
        }
        await adapter._handle_announce(raw)
        assert "peer-a" not in adapter._peers

    @pytest.mark.asyncio
    async def test_handle_announce_leave_removes_peer(self) -> None:
        adapter = self._make_adapter()
        peer = _make_peer("peer-b")
        adapter._peers["peer-b"] = peer
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)

        raw = {
            "event_type": "ravn.mesh.announce",
            "source": "ravn:peer-b",
            "payload": {
                "identity": {"peer_id": "peer-b"},
                "action": "leave",
            },
        }
        await adapter._handle_announce(raw)
        assert "peer-b" not in adapter._peers
        assert len(leaves) == 1

    @pytest.mark.asyncio
    async def test_handle_announce_wrong_event_type_ignored(self) -> None:
        adapter = self._make_adapter()
        raw = {"event_type": "something.else"}
        await adapter._handle_announce(raw)  # should not raise

    @pytest.mark.asyncio
    async def test_handle_announce_heartbeat_updates_liveness(self) -> None:
        adapter = self._make_adapter()
        peer = _make_peer("peer-b")
        peer.task_count = 0
        adapter._peers["peer-b"] = peer

        raw = {
            "event_type": "ravn.mesh.announce",
            "source": "ravn:peer-b",
            "payload": {
                "identity": {"peer_id": "peer-b"},
                "action": "heartbeat",
                "status": "busy",
                "task_count": 3,
            },
        }
        await adapter._handle_announce(raw)
        assert adapter._peers["peer-b"].status == "busy"
        assert adapter._peers["peer-b"].task_count == 3

    def test_evict_stale_peers(self) -> None:
        adapter = self._make_adapter()
        adapter._peer_ttl_s = 5.0
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)

        stale = _make_peer("stale")
        stale.last_heartbeat = datetime.now(UTC) - timedelta(seconds=10)
        adapter._peers["stale"] = stale

        adapter._evict_stale_peers()
        assert "stale" not in adapter._peers
        assert len(leaves) == 1

    def test_validate_spiffe_no_trust_domain_passes(self) -> None:
        adapter = self._make_adapter()
        # Without SPIFFE_TRUST_DOMAIN env var set, validation passes
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SPIFFE_TRUST_DOMAIN", None)
            assert adapter._validate_spiffe({}, "peer-x") is True

    @pytest.mark.asyncio
    async def test_start_skips_without_aio_pika(self) -> None:
        adapter = self._make_adapter()
        with patch("ravn.adapters.discovery.sleipnir.aio_pika", None):
            await adapter.start()  # should not raise, just warn

    def test_peers_returns_copy(self) -> None:
        adapter = self._make_adapter()
        peer = _make_peer("p")
        adapter._peers["p"] = peer
        result = adapter.peers()
        result["injected"] = peer
        assert "injected" not in adapter._peers


# ---------------------------------------------------------------------------
# K8sDiscoveryAdapter tests
# ---------------------------------------------------------------------------


class TestK8sDiscoveryAdapter:
    def _make_adapter(self) -> K8sDiscoveryAdapter:
        config = _make_discovery_config()
        identity = _make_identity()
        return K8sDiscoveryAdapter(config=config, own_identity=identity)

    def test_peers_initially_empty(self) -> None:
        adapter = self._make_adapter()
        assert adapter.peers() == {}

    @pytest.mark.asyncio
    async def test_own_identity(self) -> None:
        adapter = self._make_adapter()
        ident = await adapter.own_identity()
        assert ident.peer_id == "peer-a"

    @pytest.mark.asyncio
    async def test_announce_noop(self) -> None:
        adapter = self._make_adapter()
        await adapter.announce()  # should not raise

    @pytest.mark.asyncio
    async def test_handshake_returns_none(self) -> None:
        adapter = self._make_adapter()
        candidate = _make_candidate()
        result = await adapter.handshake(candidate)
        assert result is None

    @pytest.mark.asyncio
    async def test_watch_registers_callbacks(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        await adapter.watch(on_join=joins.append, on_leave=lambda _: None)
        assert len(adapter._on_join) == 1

    def test_list_candidates_returns_empty_without_k8s(self) -> None:
        adapter = self._make_adapter()
        with patch("ravn.adapters.discovery.k8s.k8s_client", None):
            candidates = adapter._list_candidates()
        assert candidates == []

    def test_list_candidates_with_mock_k8s(self) -> None:
        adapter = self._make_adapter()
        mock_pod = MagicMock()
        mock_pod.metadata.annotations = {
            "ravn.niuu.world/peer-id": "peer-z",
            "ravn.niuu.world/persona": "worker",
            "ravn.niuu.world/capabilities": "bash,file",
            "ravn.niuu.world/permission-mode": "workspace_write",
        }
        mock_pod.status.pod_ip = "10.0.0.3"

        mock_v1 = MagicMock()
        mock_v1.list_pod_for_all_namespaces.return_value.items = [mock_pod]

        with patch("ravn.adapters.discovery.k8s.k8s_client") as mock_k8s:
            mock_k8s.CoreV1Api.return_value = mock_v1
            candidates = adapter._list_candidates()

        assert len(candidates) == 1
        assert candidates[0].peer_id == "peer-z"
        assert candidates[0].host == "10.0.0.3"

    def test_list_candidates_skips_own_peer(self) -> None:
        adapter = self._make_adapter()  # own peer_id = "peer-a"
        mock_pod = MagicMock()
        mock_pod.metadata.annotations = {
            "ravn.niuu.world/peer-id": "peer-a",  # own
        }
        mock_pod.status.pod_ip = "10.0.0.1"
        mock_v1 = MagicMock()
        mock_v1.list_pod_for_all_namespaces.return_value.items = [mock_pod]

        with patch("ravn.adapters.discovery.k8s.k8s_client") as mock_k8s:
            mock_k8s.CoreV1Api.return_value = mock_v1
            candidates = adapter._list_candidates()

        assert candidates == []

    @pytest.mark.asyncio
    async def test_do_scan_adds_peers(self) -> None:
        adapter = self._make_adapter()
        joins: list[RavnPeer] = []
        adapter._on_join.append(joins.append)

        mock_candidate = _make_candidate("peer-z")
        mock_candidate.metadata = {
            "persona": "worker",
            "capabilities": "bash",
            "permission_mode": "read_only",
            "realm_id": "realm-xyz",
        }

        with patch.object(adapter, "_list_candidates", return_value=[mock_candidate]):
            await adapter._do_scan()

        assert "peer-z" in adapter._peers
        assert len(joins) == 1

    @pytest.mark.asyncio
    async def test_do_scan_evicts_departed_pods(self) -> None:
        adapter = self._make_adapter()
        # Pre-existing peer
        peer = _make_peer("peer-old")
        adapter._peers["peer-old"] = peer
        leaves: list[RavnPeer] = []
        adapter._on_leave.append(leaves.append)

        # Scan returns no peers this time
        with patch.object(adapter, "_list_candidates", return_value=[]):
            await adapter._do_scan()

        assert "peer-old" not in adapter._peers
        assert len(leaves) == 1

    @pytest.mark.asyncio
    async def test_start_skips_without_k8s(self) -> None:
        adapter = self._make_adapter()
        with patch("ravn.adapters.discovery.k8s.k8s_client", None):
            await adapter.start()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_noop_when_not_started(self) -> None:
        adapter = self._make_adapter()
        await adapter.stop()  # no error


# ---------------------------------------------------------------------------
# CompositeDiscoveryAdapter tests
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal fake DiscoveryPort for composite tests."""

    def __init__(self, peer_table: dict | None = None) -> None:
        self._peers_table: dict[str, RavnPeer] = peer_table or {}
        self._on_join: list[Any] = []
        self._on_leave: list[Any] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def announce(self) -> None:
        pass

    async def scan(self) -> list[RavnCandidate]:
        return []

    async def watch(self, on_join: Any, on_leave: Any) -> None:
        self._on_join.append(on_join)
        self._on_leave.append(on_leave)

    async def handshake(self, candidate: RavnCandidate) -> RavnPeer | None:
        return None

    def peers(self) -> dict[str, RavnPeer]:
        return dict(self._peers_table)

    async def own_identity(self) -> RavnIdentity:
        return _make_identity()

    def fire_join(self, peer: RavnPeer) -> None:
        for cb in self._on_join:
            cb(peer)

    def fire_leave(self, peer: RavnPeer) -> None:
        for cb in self._on_leave:
            cb(peer)


class TestCompositeDiscoveryAdapter:
    @pytest.mark.asyncio
    async def test_start_starts_all_backends(self) -> None:
        b1, b2 = _FakeBackend(), _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        await comp.start()
        assert b1.started and b2.started

    @pytest.mark.asyncio
    async def test_stop_stops_all_backends(self) -> None:
        b1, b2 = _FakeBackend(), _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        await comp.start()
        await comp.stop()
        assert b1.stopped and b2.stopped

    @pytest.mark.asyncio
    async def test_peers_merges_backends(self) -> None:
        peer_a = _make_peer("peer-a")
        peer_b = _make_peer("peer-b")
        b1 = _FakeBackend({"peer-a": peer_a})
        b2 = _FakeBackend({"peer-b": peer_b})
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        merged = comp.peers()
        assert "peer-a" in merged
        assert "peer-b" in merged

    @pytest.mark.asyncio
    async def test_watch_fires_on_join(self) -> None:
        b1 = _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1])
        await comp.start()

        joins: list[RavnPeer] = []
        await comp.watch(on_join=joins.append, on_leave=lambda _: None)

        peer = _make_peer("peer-x")
        b1.fire_join(peer)

        assert len(joins) == 1
        assert joins[0].peer_id == "peer-x"

    @pytest.mark.asyncio
    async def test_watch_fires_on_leave(self) -> None:
        b1 = _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1])
        await comp.start()

        leaves: list[RavnPeer] = []
        await comp.watch(on_join=lambda _: None, on_leave=leaves.append)

        peer = _make_peer("peer-x")
        b1.fire_join(peer)  # join first
        b1.fire_leave(peer)

        assert len(leaves) == 1
        assert leaves[0].peer_id == "peer-x"

    @pytest.mark.asyncio
    async def test_join_fires_once_per_peer(self) -> None:
        """A peer seen by two backends should only fire on_join once."""
        b1, b2 = _FakeBackend(), _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        await comp.start()

        joins: list[RavnPeer] = []
        await comp.watch(on_join=joins.append, on_leave=lambda _: None)

        peer = _make_peer("peer-x")
        b1.fire_join(peer)
        b2.fire_join(peer)

        assert len(joins) == 1  # only fires once

    @pytest.mark.asyncio
    async def test_leave_fires_when_last_backend_drops(self) -> None:
        b1, b2 = _FakeBackend(), _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        await comp.start()

        leaves: list[RavnPeer] = []
        await comp.watch(on_join=lambda _: None, on_leave=leaves.append)

        peer = _make_peer("peer-x")
        b1.fire_join(peer)
        b2.fire_join(peer)
        b1.fire_leave(peer)  # one backend drops — NOT a leave yet
        assert len(leaves) == 0

        b2.fire_leave(peer)  # second backend drops — now leave fires
        assert len(leaves) == 1

    @pytest.mark.asyncio
    async def test_scan_deduplicates_by_peer_id(self) -> None:
        cand = _make_candidate("peer-x")

        class ScanBackend(_FakeBackend):
            async def scan(self) -> list[RavnCandidate]:
                return [cand]

        b1, b2 = ScanBackend(), ScanBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        results = await comp.scan()
        assert len(results) == 1
        assert results[0].peer_id == "peer-x"

    @pytest.mark.asyncio
    async def test_own_identity_from_first_backend(self) -> None:
        b1 = _FakeBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1])
        ident = await comp.own_identity()
        assert ident.peer_id == "peer-a"

    @pytest.mark.asyncio
    async def test_own_identity_raises_with_no_backends(self) -> None:
        comp = CompositeDiscoveryAdapter(backends=[])
        with pytest.raises(RuntimeError):
            await comp.own_identity()

    @pytest.mark.asyncio
    async def test_announce_calls_all_backends(self) -> None:
        announced: list[str] = []

        class AnnBackend(_FakeBackend):
            async def announce(self) -> None:
                announced.append("x")

        b1, b2 = AnnBackend(), AnnBackend()
        comp = CompositeDiscoveryAdapter(backends=[b1, b2])
        await comp.announce()
        assert len(announced) == 2


# ---------------------------------------------------------------------------
# Capability routing test (NIU-435 cascade use case)
# ---------------------------------------------------------------------------


class TestCapabilityRouting:
    def test_filter_idle_bash_capable_verified(self) -> None:
        """Verify the cascade can filter peers by capability + status + trust."""
        peers: dict[str, RavnPeer] = {
            "a": _make_peer("a", trust_level="verified", status="idle"),
            "b": _make_peer("b", trust_level="verified", status="busy"),
            "c": _make_peer("c", trust_level="unverified", status="idle"),
        }
        peers["a"].capabilities = ["bash", "file"]
        peers["b"].capabilities = ["bash"]
        peers["c"].capabilities = ["bash"]

        workers = [
            p
            for p in peers.values()
            if p.status == "idle" and "bash" in p.capabilities and p.trust_level == "verified"
        ]
        assert len(workers) == 1
        assert workers[0].peer_id == "a"


# ---------------------------------------------------------------------------
# Integration test: two in-process mDNS adapters (mock handshake)
# ---------------------------------------------------------------------------


class TestInProcessDiscovery:
    """Two MdnsDiscoveryAdapters sharing the same realm key — simulated discovery."""

    @pytest.mark.asyncio
    async def test_add_peer_and_watch_callback(self) -> None:
        """Direct peer table manipulation simulates a successful discovery cycle."""
        config = _make_discovery_config()
        realm_key = b"shared-secret-key-32-bytes-long!"
        identity_a = _make_identity("peer-a")
        identity_b = _make_identity("peer-b")

        adapter_a = MdnsDiscoveryAdapter(config=config, own_identity=identity_a)
        adapter_b = MdnsDiscoveryAdapter(config=config, own_identity=identity_b)
        adapter_a._realm_key = realm_key
        adapter_b._realm_key = realm_key

        joins_a: list[RavnPeer] = []
        await adapter_a.watch(on_join=joins_a.append, on_leave=lambda _: None)

        # Simulate B announcing and A successfully verifying
        peer_b = RavnPeer(
            peer_id="peer-b",
            realm_id=realm_id_from_key(realm_key),
            persona="test-agent",
            capabilities=["bash"],
            permission_mode="workspace_write",
            version="0.1.0",
            rep_address="tcp://127.0.0.1:7491",
            pub_address="tcp://127.0.0.1:7490",
            trust_level="verified",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
        )
        adapter_a._add_peer(peer_b)

        assert "peer-b" in adapter_a.peers()
        assert len(joins_a) == 1
        assert joins_a[0].peer_id == "peer-b"

    @pytest.mark.asyncio
    async def test_peers_dict_shape_for_mesh_adapter(self) -> None:
        """peers() returns dict[str, RavnPeer] with rep_address and pub_address."""
        config = _make_discovery_config()
        adapter = MdnsDiscoveryAdapter(config=config, own_identity=_make_identity())
        peer = _make_peer("peer-b")
        adapter._peers["peer-b"] = peer

        table = adapter.peers()
        assert isinstance(table, dict)
        assert "peer-b" in table
        found = table["peer-b"]
        assert found.rep_address == "tcp://127.0.0.1:7491"
        assert found.pub_address == "tcp://127.0.0.1:7490"
        assert found.status in ("idle", "busy")
        assert isinstance(found.task_count, int)
