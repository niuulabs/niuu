"""Tests for adapter-backed Observatory discovery materialization."""

from __future__ import annotations

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
