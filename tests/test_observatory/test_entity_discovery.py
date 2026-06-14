from __future__ import annotations

import json

import httpx
import pytest

from niuu.adapters.outbound.http_auth import NoAuthHeaderAdapter
from observatory.discovery import ObservatoryDiscoveryService
from observatory.entity_discovery import (
    CompositeDiscoveryAdapter,
    DiscoveredEntity,
    DiscoveryResult,
    HttpObservatoryDiscoveryAdapter,
    KubernetesDiscoveryAdapter,
    topology_from_discovery,
)


class _StaticAdapter:
    def __init__(self, result: DiscoveryResult) -> None:
        self.result = result

    async def discover(self) -> DiscoveryResult:
        return self.result


@pytest.mark.asyncio
async def test_kubernetes_discovery_projects_labels_to_topology(tmp_path, monkeypatch) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.params["labelSelector"] == "niuu.world/cluster"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "niuu-ravn",
                            "namespace": "volundr",
                            "uid": "uid-ravn",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "niuu.world/namespace": "volundr",
                                "app.kubernetes.io/component": "ravn-api",
                            },
                        },
                        "status": {
                            "replicas": 1,
                            "readyReplicas": 1,
                            "availableReplicas": 1,
                        },
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        include_kinds=["deployments"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    snapshot = topology_from_discovery(await adapter.discover())

    assert {node["id"] for node in snapshot["nodes"]} == {
        "cluster-ymir",
        "namespace-ymir-volundr",
        "k8s:ymir:volundr:deployment:niuu-ravn",
    }
    ravn = next(node for node in snapshot["nodes"] if node["id"].endswith("niuu-ravn"))
    assert ravn["typeId"] == "ravn_long"
    assert ravn["clusterName"] == "ymir"
    assert ravn["namespace"] == "volundr"


@pytest.mark.asyncio
async def test_observatory_discovery_uses_adapters_without_demo_nodes() -> None:
    service = ObservatoryDiscoveryService(
        guild_url="http://guild.test",
        auth=NoAuthHeaderAdapter(),
        discovery_adapter=CompositeDiscoveryAdapter(
            [
                _StaticAdapter(
                    DiscoveryResult(
                        entities=[
                            DiscoveredEntity(
                                id="k8s:noatun:volundr:deployment:niuu-mimir-shared",
                                kind="mimir",
                                name="niuu-mimir-shared",
                                cluster="noatun",
                                namespace="volundr",
                                status="healthy",
                                source_kind="kubernetes:deployment",
                            )
                        ]
                    )
                )
            ]
        ),
    )

    snapshot = await service.get_topology_snapshot()

    node_ids = {node["id"] for node in snapshot["nodes"]}
    assert "cluster-noatun" in node_ids
    assert "k8s:noatun:volundr:deployment:niuu-mimir-shared" in node_ids
    assert not any(node_id.startswith("realm-") for node_id in node_ids)
    assert "mimir-well" not in node_ids


@pytest.mark.asyncio
async def test_http_observatory_adapter_merges_remote_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/observatory/topology/snapshot"
        return httpx.Response(
            200,
            json={
                "nodes": [
                    {
                        "id": "cluster-valhalla",
                        "typeId": "cluster",
                        "label": "valhalla",
                        "clusterName": "valhalla",
                    }
                ],
                "edges": [],
                "events": [{"id": "event-1", "service": "observatory"}],
            },
        )

    adapter = HttpObservatoryDiscoveryAdapter(
        base_url="https://valhalla.example",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert [entity.id for entity in result.entities] == ["cluster-valhalla"]
    assert result.entities[0].cluster == "valhalla"
    assert json.loads(json.dumps(result.events))[0]["id"] == "event-1"
