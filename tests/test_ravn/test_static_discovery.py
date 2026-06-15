"""Tests for Ravn static discovery."""

from __future__ import annotations

from ravn.adapters.discovery.static import StaticDiscoveryAdapter
from ravn.domain.models import RavnIdentity


def _identity(peer_id: str = "self") -> RavnIdentity:
    return RavnIdentity(
        peer_id=peer_id,
        realm_id="realm",
        persona="self",
        capabilities=[],
        permission_mode="workspace_write",
        version="0.0.0",
    )


async def test_static_discovery_prefers_inline_peers(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("HOME", str(tmp_path))
    stale_cluster = tmp_path / ".ravn" / "cluster.yaml"
    stale_cluster.parent.mkdir()
    stale_cluster.write_text(
        "peers:\n"
        "  - peer_id: stale\n"
        "    persona: stale\n"
        "    pub_address: tcp://127.0.0.1:9000\n"
        "    rep_address: tcp://127.0.0.1:9001\n",
        encoding="utf-8",
    )

    adapter = StaticDiscoveryAdapter(
        _identity(),
        peers=[
            {
                "peer_id": "inline",
                "persona": "inline",
                "pub_address": "tcp://127.0.0.1:7482",
                "rep_address": "tcp://127.0.0.1:7483",
            }
        ],
        poll_interval_s=0,
    )

    await adapter.start()
    try:
        peers = adapter.peers()
    finally:
        await adapter.stop()

    assert set(peers) == {"inline"}
    assert peers["inline"].pub_address == "tcp://127.0.0.1:7482"
