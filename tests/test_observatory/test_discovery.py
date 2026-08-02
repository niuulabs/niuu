"""Tests for adapter-backed Observatory discovery materialization."""

from __future__ import annotations

import asyncio

import pytest

from observatory.discovery import ObservatoryDiscoveryService
from observatory.entity_discovery import DiscoveredEntity, DiscoveryResult


class _SequenceAdapter:
    def __init__(self, *results: DiscoveryResult) -> None:
        self.results = list(results)
        self.calls = 0

    async def discover(self) -> DiscoveryResult:
        self.calls += 1
        if not self.results:
            return DiscoveryResult()
        if len(self.results) == 1:
            return self.results[0]
        return self.results.pop(0)


async def test_discovery_materializes_adapter_entities() -> None:
    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        discovery_adapter=_SequenceAdapter(
            DiscoveryResult(
                entities=[
                    DiscoveredEntity(
                        id="k8s:ymir:volundr:deployment:niuu-ravn",
                        kind="ravn_long",
                        name="niuu-ravn",
                        cluster="ymir",
                        namespace="volundr",
                        status="healthy",
                        source_kind="kubernetes:deployment",
                    )
                ],
                events=[
                    {
                        "id": "event-1",
                        "service": "observatory",
                        "message": "ok",
                    }
                ],
            )
        ),
        ttl_seconds=0,
    )

    topology = await service.get_topology_snapshot()
    events = await service.get_events()

    assert topology["layoutHints"] == {"mode": "pack", "scope": "world"}
    assert any(node["id"] == "cluster-ymir" for node in topology["nodes"])
    assert any(node["id"] == "namespace-ymir-volundr" for node in topology["nodes"])
    assert any(node["typeId"] == "ravn_long" for node in topology["nodes"])
    assert events[0]["id"] == "event-1"


async def test_discovery_without_adapter_reports_warning() -> None:
    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        ttl_seconds=0,
    )

    topology = await service.get_topology_snapshot()
    events = await service.get_events()

    assert topology["nodes"] == []
    assert events[0]["id"] == "observatory:discovery:not-configured"


async def test_discovery_uses_cache_and_returns_deep_copies() -> None:
    adapter = _SequenceAdapter(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="k8s:noatun:volundr:deployment:niuu-mimir",
                    kind="mimir",
                    name="niuu-mimir",
                    cluster="noatun",
                    namespace="volundr",
                )
            ]
        )
    )
    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        discovery_adapter=adapter,
        ttl_seconds=60,
    )

    topology = await service.get_topology_snapshot()
    topology["nodes"].append({"id": "mutated"})
    second = await service.get_topology_snapshot()

    assert adapter.calls == 1
    assert not any(node["id"] == "mutated" for node in second["nodes"])


@pytest.mark.asyncio
async def test_an_expired_cache_serves_stale_while_it_rebuilds() -> None:
    """The caller that lands on the expiry must not pay for the rebuild.

    Rebuilding lists a whole cluster and calls out to Bifrost, Ravn and Ting —
    ~7s on ymir. Blocking on it meant the richest source timed out of the
    aggregate every time its cache turned over, so its meshes and edges looked
    intermittent.
    """
    release = asyncio.Event()
    calls = 0

    class _BlockingAdapter:
        async def discover(self):  # noqa: ANN202
            nonlocal calls
            calls += 1
            if calls > 1:  # the first build must complete; later ones hang
                await release.wait()
            return DiscoveryResult(
                entities=[DiscoveredEntity(id=f"svc-{calls}", kind="service", name="svc")]
            )

    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        # The cache clock is truncated to whole seconds, so a sub-second TTL
        # never expires — the entry has to actually age past one.
        ttl_seconds=1.0,
        discovery_adapter=_BlockingAdapter(),
    )

    first = await service.get_topology_snapshot()
    assert calls == 1
    await asyncio.sleep(1.2)  # let the entry age past the TTL

    # The rebuild is wedged open, so this can only return by serving stale.
    stale = await asyncio.wait_for(service.get_topology_snapshot(), timeout=1.0)
    assert [n["id"] for n in stale["nodes"]] == [n["id"] for n in first["nodes"]]

    release.set()  # and the refresh really was running behind it
    await asyncio.sleep(0.1)
    assert calls == 2
