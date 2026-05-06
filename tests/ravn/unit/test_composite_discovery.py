"""Unit tests for CompositeDiscoveryAdapter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ravn.adapters.discovery.composite import CompositeDiscoveryAdapter
from ravn.domain.models import RavnCandidate, RavnIdentity, RavnPeer


def _peer(peer_id: str = "peer-1") -> RavnPeer:
    return RavnPeer(
        peer_id=peer_id,
        realm_id="test-realm",
        persona="local",
        capabilities=[],
        permission_mode="default",
        version="0.1",
    )


def _candidate(peer_id: str = "cand-1") -> RavnCandidate:
    return RavnCandidate(
        peer_id=peer_id,
        realm_id_hash="abc123",
        host="127.0.0.1",
        rep_address=None,
        pub_address=None,
        handshake_port=None,
    )


def _identity() -> RavnIdentity:
    return RavnIdentity(
        peer_id="self",
        realm_id="test-realm",
        persona="local",
        capabilities=[],
        permission_mode="default",
        version="0.1",
    )


def _mock_backend() -> MagicMock:
    backend = MagicMock()
    backend.start = AsyncMock()
    backend.stop = AsyncMock()
    backend.announce = AsyncMock()
    backend.scan = AsyncMock(return_value=[])
    backend.watch = AsyncMock()
    backend.handshake = AsyncMock(return_value=None)
    backend.peers = MagicMock(return_value={})
    backend.own_identity = AsyncMock(return_value=_identity())
    return backend


class TestCompositeDiscoveryStart:
    @pytest.mark.asyncio
    async def test_start_starts_all_backends(self) -> None:
        b1 = _mock_backend()
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        await adapter.start()
        b1.start.assert_awaited_once()
        b2.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_stop_stops_all_backends(self) -> None:
        b1 = _mock_backend()
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        await adapter.stop()
        b1.stop.assert_awaited_once()
        b2.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_announce_calls_all_backends(self) -> None:
        b1 = _mock_backend()
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        await adapter.announce()
        b1.announce.assert_awaited_once()
        b2.announce.assert_awaited_once()


class TestCompositeDiscoveryScan:
    @pytest.mark.asyncio
    async def test_scan_merges_candidates(self) -> None:
        b1 = _mock_backend()
        b1.scan = AsyncMock(return_value=[_candidate("a"), _candidate("b")])
        b2 = _mock_backend()
        b2.scan = AsyncMock(return_value=[_candidate("b"), _candidate("c")])
        adapter = CompositeDiscoveryAdapter([b1, b2])
        results = await adapter.scan()
        ids = {c.peer_id for c in results}
        assert ids == {"a", "b", "c"}

    @pytest.mark.asyncio
    async def test_scan_skips_non_list_results(self) -> None:
        """Exception results from asyncio.gather are skipped (covers line 80->79 branch)."""
        b1 = _mock_backend()
        b1.scan = AsyncMock(side_effect=RuntimeError("boom"))
        b2 = _mock_backend()
        b2.scan = AsyncMock(return_value=[_candidate("x")])
        adapter = CompositeDiscoveryAdapter([b1, b2])
        results = await adapter.scan()
        assert len(results) == 1
        assert results[0].peer_id == "x"


class TestCompositeDiscoveryHandshake:
    @pytest.mark.asyncio
    async def test_handshake_returns_first_success(self) -> None:
        peer = _peer("ok")
        b1 = _mock_backend()
        b1.handshake = AsyncMock(return_value=peer)
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        result = await adapter.handshake(_candidate())
        assert result is peer
        b2.handshake.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handshake_skips_failures_and_returns_none(self) -> None:
        """Exception in handshake is caught, returns None (covers lines 92-99)."""
        b1 = _mock_backend()
        b1.handshake = AsyncMock(side_effect=RuntimeError("no"))
        b2 = _mock_backend()
        b2.handshake = AsyncMock(return_value=None)
        adapter = CompositeDiscoveryAdapter([b1, b2])
        result = await adapter.handshake(_candidate())
        assert result is None

    @pytest.mark.asyncio
    async def test_handshake_all_fail_returns_none(self) -> None:
        b1 = _mock_backend()
        b1.handshake = AsyncMock(side_effect=RuntimeError("fail"))
        adapter = CompositeDiscoveryAdapter([b1])
        result = await adapter.handshake(_candidate())
        assert result is None


class TestCompositeDiscoveryPeers:
    def test_peers_merges_all_backends(self) -> None:
        peer_a = _peer("a")
        peer_b = _peer("b")
        b1 = _mock_backend()
        b1.peers = MagicMock(return_value={"a": peer_a})
        b2 = _mock_backend()
        b2.peers = MagicMock(return_value={"b": peer_b})
        adapter = CompositeDiscoveryAdapter([b1, b2])
        merged = adapter.peers()
        assert "a" in merged
        assert "b" in merged

    def test_peers_skips_failing_backend(self) -> None:
        """Exception in peers() is caught, other backends still contribute (lines 107-108)."""
        peer_b = _peer("b")
        b1 = _mock_backend()
        b1.peers = MagicMock(side_effect=RuntimeError("broken"))
        b2 = _mock_backend()
        b2.peers = MagicMock(return_value={"b": peer_b})
        adapter = CompositeDiscoveryAdapter([b1, b2])
        merged = adapter.peers()
        assert "b" in merged


class TestCompositeDiscoveryOwnIdentity:
    @pytest.mark.asyncio
    async def test_own_identity_returns_first_success(self) -> None:
        identity = _identity()
        b1 = _mock_backend()
        b1.own_identity = AsyncMock(return_value=identity)
        adapter = CompositeDiscoveryAdapter([b1])
        result = await adapter.own_identity()
        assert result is identity

    @pytest.mark.asyncio
    async def test_own_identity_skips_failures(self) -> None:
        """Exception in own_identity() is caught, tries next (lines 116-117)."""
        identity = _identity()
        b1 = _mock_backend()
        b1.own_identity = AsyncMock(side_effect=RuntimeError("no"))
        b2 = _mock_backend()
        b2.own_identity = AsyncMock(return_value=identity)
        adapter = CompositeDiscoveryAdapter([b1, b2])
        result = await adapter.own_identity()
        assert result is identity

    @pytest.mark.asyncio
    async def test_own_identity_raises_when_all_fail(self) -> None:
        b1 = _mock_backend()
        b1.own_identity = AsyncMock(side_effect=RuntimeError("fail"))
        adapter = CompositeDiscoveryAdapter([b1])
        with pytest.raises(RuntimeError, match="no backend"):
            await adapter.own_identity()


class TestCompositeDiscoveryCallbacks:
    @pytest.mark.asyncio
    async def test_watch_registers_callbacks(self) -> None:
        adapter = CompositeDiscoveryAdapter([])
        join_cb = MagicMock()
        leave_cb = MagicMock()
        await adapter.watch(join_cb, leave_cb)
        assert join_cb in adapter._on_join
        assert leave_cb in adapter._on_leave

    def test_join_callback_fires_once_per_peer(self) -> None:
        """First backend to report join propagates (covers lines 128-134)."""
        b1 = _mock_backend()
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        fired: list[RavnPeer] = []
        adapter._on_join.append(lambda p: fired.append(p))

        peer = _peer("x")
        cb1 = adapter._make_join_callback(b1)
        cb2 = adapter._make_join_callback(b2)
        cb1(peer)
        cb2(peer)  # second backend join — should NOT fire again
        assert len(fired) == 1

    def test_join_callback_exception_skipped(self) -> None:
        """Exception in join callback is silently skipped (covers lines 133-134)."""
        b1 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1])
        adapter._on_join.append(MagicMock(side_effect=RuntimeError("cb boom")))
        cb = adapter._make_join_callback(b1)
        # Must not raise
        cb(_peer("y"))

    def test_leave_callback_fires_when_last_backend_drops(self) -> None:
        """Leave fires when count reaches zero (covers lines 139-150)."""
        b1 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1])
        fired: list[RavnPeer] = []
        adapter._on_leave.append(lambda p: fired.append(p))

        peer = _peer("z")
        join_cb = adapter._make_join_callback(b1)
        leave_cb = adapter._make_leave_callback(b1)
        join_cb(peer)
        leave_cb(peer)
        assert len(fired) == 1

    def test_leave_callback_exception_skipped(self) -> None:
        """Exception in leave callback is silently skipped (covers lines 147-148)."""
        b1 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1])
        adapter._on_leave.append(MagicMock(side_effect=RuntimeError("leave boom")))
        peer = _peer("w")
        join_cb = adapter._make_join_callback(b1)
        leave_cb = adapter._make_leave_callback(b1)
        join_cb(peer)
        # Must not raise
        leave_cb(peer)

    def test_leave_decrements_count_for_multiple_backends(self) -> None:
        """Peer still present in other backends decrements count (line 149-150)."""
        b1 = _mock_backend()
        b2 = _mock_backend()
        adapter = CompositeDiscoveryAdapter([b1, b2])
        fired: list[RavnPeer] = []
        adapter._on_leave.append(lambda p: fired.append(p))

        peer = _peer("multi")
        join_cb1 = adapter._make_join_callback(b1)
        join_cb2 = adapter._make_join_callback(b2)
        leave_cb1 = adapter._make_leave_callback(b1)
        leave_cb2 = adapter._make_leave_callback(b2)

        join_cb1(peer)
        join_cb2(peer)
        leave_cb1(peer)  # count goes from 2 to 1 — no leave fire
        assert len(fired) == 0
        leave_cb2(peer)  # count goes to 0 — leave fires
        assert len(fired) == 1
