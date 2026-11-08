"""Registry discovery: the sessions of one host find each other through one file."""

from __future__ import annotations

import asyncio
import json

import pytest

from niuu.adapters.discovery._failures import DiscoveryError
from niuu.adapters.discovery.registry import RegistryDiscoveryAdapter, realm_hash
from niuu.mesh.discovery_builder import build_discovery_adapters
from niuu.mesh.identity import MeshIdentity


def _identity(
    peer_id: str, *, realm: str = "proof", pub: str | None = "tcp://127.0.0.1:7001", **kwargs
) -> MeshIdentity:
    return MeshIdentity(
        peer_id=peer_id,
        realm_id=realm,
        persona="coder",
        capabilities=["coding"],
        permission_mode="full_access",
        version="1",
        pub_address=pub,
        display_name=kwargs.pop("display_name", peer_id),
        **kwargs,
    )


def _adapter(path, identity, **kwargs) -> RegistryDiscoveryAdapter:
    return RegistryDiscoveryAdapter(identity, path=str(path), poll_interval_s=0.05, **kwargs)


async def _until(predicate, timeout_s: float = 3.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


async def test_two_sessions_on_one_host_see_each_other_with_their_address_and_facts(tmp_path):
    path = tmp_path / "mesh" / "peers.json"
    a = _adapter(
        path,
        _identity("a", pub="tcp://127.0.0.1:7001", role="reviewer", consumes_event_types=["x.y"]),
    )
    b = _adapter(path, _identity("b", pub="tcp://127.0.0.1:7002", display_name="beta"))
    await a.start()
    await b.start()
    try:
        await _until(lambda: "b" in a.peers() and "a" in b.peers())
        peer = a.peers()["b"]
        assert peer.pub_address == "tcp://127.0.0.1:7002"
        assert peer.display_name == "beta"
        assert peer.trust_level == "verified"
        assert b.peers()["a"].role == "reviewer"
        assert b.peers()["a"].consumes_event_types == ["x.y"]
        assert "a" not in a.peers()
    finally:
        await a.stop()
        await b.stop()


async def test_the_file_never_holds_the_raw_realm(tmp_path):
    path = tmp_path / "peers.json"
    a = _adapter(path, _identity("a", realm="secret-realm"))
    await a.start()
    try:
        text = path.read_text()
        assert "secret-realm" not in text
        assert realm_hash("secret-realm") in text
    finally:
        await a.stop()


async def test_another_realm_on_the_same_host_is_not_a_peer(tmp_path):
    path = tmp_path / "peers.json"
    a = _adapter(path, _identity("a", realm="flock-a"))
    other = _adapter(path, _identity("o", realm="flock-b", pub="tcp://127.0.0.1:7009"))
    await a.start()
    await other.start()
    try:
        await asyncio.sleep(0.3)
        assert a.peers() == {}
        assert other.peers() == {}
    finally:
        await a.stop()
        await other.stop()


async def test_join_and_leave_callbacks_fire_and_a_stopped_session_is_dropped(tmp_path):
    path = tmp_path / "peers.json"
    a = _adapter(path, _identity("a"))
    joined: list[str] = []
    left: list[str] = []
    await a.watch(lambda p: joined.append(p.peer_id), lambda p: left.append(p.peer_id))
    await a.start()
    b = _adapter(path, _identity("b", pub="tcp://127.0.0.1:7002"))
    await b.start()
    try:
        await _until(lambda: joined == ["b"])
        await b.stop()
        await _until(lambda: left == ["b"])
        assert a.peers() == {}
    finally:
        await a.stop()


async def test_a_dead_process_is_pruned_by_pid(tmp_path):
    path = tmp_path / "peers.json"
    path.write_text(
        json.dumps(
            {
                "services": [
                    {
                        "id": "ghost",
                        "pid": 2**22 + 12345,  # no such process
                        "socket": "tcp://127.0.0.1:7777",
                        "started": "2026-01-01T00:00:00+00:00",
                        "info": {"realm_hash": realm_hash("proof")},
                    }
                ]
            }
        )
    )
    a = _adapter(path, _identity("a"))
    await a.start()
    try:
        assert a.peers() == {}
    finally:
        await a.stop()


async def test_identity_changes_are_written_so_peers_see_the_activity(tmp_path):
    path = tmp_path / "peers.json"
    identity = _identity("a")
    a = _adapter(path, identity)
    b = _adapter(path, _identity("b", pub="tcp://127.0.0.1:7002"))
    await a.start()
    await b.start()
    try:
        await _until(lambda: "a" in b.peers())
        identity.activity_state = "active"
        identity.busy = True
        await _until(lambda: b.peers()["a"].activity_state == "active")
        assert b.peers()["a"].status == "busy"
    finally:
        await a.stop()
        await b.stop()


async def test_start_without_a_pub_address_is_an_error_naming_the_remedy(tmp_path):
    a = _adapter(tmp_path / "peers.json", _identity("a", pub=None))
    with pytest.raises(DiscoveryError, match="must bind before discovery starts"):
        await a.start()


async def test_an_unwritable_registry_is_an_error_not_an_empty_mesh(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    a = _adapter(blocker / "peers.json", _identity("a"))
    with pytest.raises(DiscoveryError, match="cannot write"):
        await a.start()


def test_a_path_is_required():
    with pytest.raises(DiscoveryError, match="needs `path`"):
        RegistryDiscoveryAdapter(_identity("a"), path="")


async def test_the_builder_knows_the_alias(tmp_path):
    adapter = build_discovery_adapters(
        adapters_config=[{"adapter": "registry", "path": str(tmp_path / "p.json")}],
        own_identity=_identity("a"),
    )
    assert isinstance(adapter, RegistryDiscoveryAdapter)


async def test_scan_and_handshake_answer_from_the_table(tmp_path):
    path = tmp_path / "peers.json"
    a = _adapter(path, _identity("a"))
    b = _adapter(path, _identity("b", pub="tcp://127.0.0.1:7002"))
    await a.start()
    await b.start()
    try:
        await _until(lambda: "b" in a.peers())
        candidates = await a.scan()
        assert [c.peer_id for c in candidates] == ["b"]
        assert candidates[0].pub_address == "tcp://127.0.0.1:7002"
        assert (await a.handshake(candidates[0])).peer_id == "b"
        assert (await a.own_identity()).peer_id == "a"
        await a.announce()
    finally:
        await a.stop()
        await b.stop()
