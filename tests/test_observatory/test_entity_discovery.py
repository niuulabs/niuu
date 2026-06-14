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
    WardenSpecDiscoveryAdapter,
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
        "runtime:ymir:volundr:service:ravn-api",
    }
    ravn = next(node for node in snapshot["nodes"] if node["id"].endswith("ravn-api"))
    namespace = next(node for node in snapshot["nodes"] if node["id"] == "namespace-ymir-volundr")
    cluster = next(node for node in snapshot["nodes"] if node["id"] == "cluster-ymir")
    assert ravn["typeId"] == "service"
    assert "resourceKind" not in ravn
    assert [resource["kind"] for resource in ravn["resources"]] == ["deployment"]
    assert ravn["clusterName"] == "ymir"
    assert ravn["namespace"] == "volundr"
    assert ravn["layoutHints"]["packGroup"] == "service"
    assert namespace["typeId"] == "namespace"
    assert namespace["layoutHints"]["packGroup"] == "namespace"
    assert cluster["layoutHints"]["packGroup"] == "cluster"
    assert snapshot["edges"] == []


@pytest.mark.asyncio
async def test_kubernetes_discovery_collapses_resources_to_logical_entities(
    tmp_path, monkeypatch
) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    def item(kind: str, name: str, labels: dict[str, str]) -> dict[str, object]:
        return {
            "metadata": {
                "name": name,
                "namespace": "volundr",
                "uid": f"uid-{kind}-{name}",
                "labels": labels,
            },
            "status": {
                "phase": "Running",
                "replicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
            },
        }

    labels = {
        "niuu.world/cluster": "ymir",
        "app.kubernetes.io/name": "mimir-shared",
        "app.kubernetes.io/component": "knowledge-service",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deployments"):
            return httpx.Response(200, json={"items": [item("deployment", "niuu-mimir", labels)]})
        if request.url.path.endswith("/services"):
            return httpx.Response(200, json={"items": [item("service", "niuu-mimir", labels)]})
        if request.url.path.endswith("/pods"):
            return httpx.Response(
                200,
                json={"items": [item("pod", "niuu-mimir-abc123", labels)]},
            )
        return httpx.Response(200, json={"items": []})

    adapter = KubernetesDiscoveryAdapter(
        include_kinds=["deployments", "services", "pods", "ingresses"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    snapshot = topology_from_discovery(await adapter.discover())

    mimir_nodes = [node for node in snapshot["nodes"] if node["typeId"] == "mimir"]
    assert len(mimir_nodes) == 1
    assert mimir_nodes[0]["id"] == "runtime:ymir:volundr:mimir:mimir-shared"
    assert mimir_nodes[0]["label"] == "mimir-shared"
    assert sorted(resource["kind"] for resource in mimir_nodes[0]["resources"]) == [
        "deployment",
        "pod",
        "service",
    ]


@pytest.mark.asyncio
async def test_kubernetes_discovery_projects_declared_relationships(
    tmp_path, monkeypatch
) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    def item(name: str, labels: dict[str, str]) -> dict[str, object]:
        return {
            "metadata": {
                "name": name,
                "namespace": "volundr",
                "uid": f"uid-{name}",
                "labels": labels,
            },
            "status": {
                "replicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deployments"):
            return httpx.Response(
                200,
                json={
                    "items": [
                        item(
                            "niuu-guild",
                            {
                                "niuu.world/cluster": "ymir",
                                "app.kubernetes.io/name": "guild",
                                "app.kubernetes.io/component": "guild",
                                "observatory.niuu.world/uses": "service:ravn@ymir/volundr",
                            },
                        ),
                        item(
                            "niuu-ravn",
                            {
                                "niuu.world/cluster": "ymir",
                                "app.kubernetes.io/name": "ravn",
                                "app.kubernetes.io/component": "ravn-api",
                            },
                        ),
                    ]
                },
            )
        return httpx.Response(200, json={"items": []})

    adapter = KubernetesDiscoveryAdapter(
        include_kinds=["deployments"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    snapshot = topology_from_discovery(await adapter.discover())

    assert snapshot["edges"] == [
        {
            "id": "edge:uses:runtime-ymir-volundr-service-guild:service-ravn-ymir-volundr",
            "sourceId": "runtime:ymir:volundr:service:guild",
            "targetId": "runtime:ymir:volundr:service:ravn",
            "kind": "soft",
            "relationType": "uses",
            "label": "uses",
            "confidence": "declared",
            "evidence": {
                "adapter": "KubernetesDiscoveryAdapter",
                "field": "metadata.labels[observatory.niuu.world/uses]",
            },
        }
    ]


@pytest.mark.asyncio
async def test_kubernetes_discovery_supports_generic_tagged_objects(
    tmp_path, monkeypatch
) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/configmaps")
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "basement-printer",
                            "namespace": "devices",
                            "uid": "uid-printer",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "niuu.world/kind": "printer",
                                "niuu.world/entity-id": "basement-printer",
                                "niuu.world/display-name": "Basement Printer",
                            },
                        },
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        include_kinds=["configmaps"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    snapshot = topology_from_discovery(await adapter.discover())
    printer = next(node for node in snapshot["nodes"] if node["typeId"] == "printer")

    assert printer["id"] == "runtime:ymir:devices:printer:basement-printer"
    assert printer["label"] == "Basement Printer"
    assert printer["resources"] == [
        {
            "kind": "configmap",
            "name": "basement-printer",
            "uid": "uid-printer",
            "generation": None,
        }
    ]


@pytest.mark.asyncio
async def test_warden_spec_discovery_emits_semantic_relationships(tmp_path) -> None:
    from ravn.warden.models import WardenMimirBinding, WardenRuntime, WardenSpec
    from ravn.warden.store import WardenStore

    store = WardenStore(tmp_path)
    store.save(
        WardenSpec(
            id="mimir-shared-warden",
            name="Mimir Shared Warden",
            deployment_kwargs={"cluster": "ymir", "namespace": "volundr"},
            mimir=WardenMimirBinding(
                read_mount_names=["shared"],
                write_mount_names=["shared"],
            ),
            runtime=WardenRuntime(state="active"),
        )
    )

    result = await CompositeDiscoveryAdapter(
        [
            _StaticAdapter(
                DiscoveryResult(
                    entities=[
                        DiscoveredEntity(
                            id="runtime:ymir:volundr:service:ravn",
                            kind="service",
                            name="ravn",
                            cluster="ymir",
                            namespace="volundr",
                            status="healthy",
                        ),
                        DiscoveredEntity(
                            id="runtime:ymir:volundr:mimir:mimir-shared",
                            kind="mimir",
                            name="mimir-shared",
                            cluster="ymir",
                            namespace="volundr",
                            status="healthy",
                        ),
                    ]
                )
            ),
            WardenSpecDiscoveryAdapter(root=str(tmp_path)),
        ]
    ).discover()

    snapshot = topology_from_discovery(result)
    edges = {
        (edge["sourceId"], edge["targetId"], edge["relationType"])
        for edge in snapshot["edges"]
    }

    assert (
        "runtime:ymir:volundr:service:ravn",
        "runtime:ymir:volundr:warden:mimir-shared-warden",
        "manages",
    ) in edges
    assert (
        "runtime:ymir:volundr:warden:mimir-shared-warden",
        "runtime:ymir:volundr:mimir:mimir-shared",
        "reads",
    ) in edges
    assert (
        "runtime:ymir:volundr:warden:mimir-shared-warden",
        "runtime:ymir:volundr:mimir:mimir-shared",
        "writes",
    ) in edges


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
                                id="runtime:noatun:volundr:mimir:niuu-mimir-shared",
                                kind="mimir",
                                name="niuu-mimir-shared",
                                cluster="noatun",
                                namespace="volundr",
                                status="healthy",
                                source_kind="kubernetes",
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
    assert "runtime:noatun:volundr:mimir:niuu-mimir-shared" in node_ids
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


@pytest.mark.asyncio
async def test_topology_from_remote_cluster_does_not_emit_self_loop() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="cluster-noatun",
                    kind="cluster",
                    name="noatun",
                    cluster="noatun",
                    status="healthy",
                    source_kind="remote-observatory",
                )
            ],
            edges=[
                {
                    "id": "edge:cluster-noatun:cluster-noatun",
                    "sourceId": "cluster-noatun",
                    "targetId": "cluster-noatun",
                    "kind": "soft",
                }
            ],
        )
    )

    cluster = next(node for node in snapshot["nodes"] if node["id"] == "cluster-noatun")
    assert cluster["parentId"] is None
    assert not any(edge["sourceId"] == edge["targetId"] for edge in snapshot["edges"])
