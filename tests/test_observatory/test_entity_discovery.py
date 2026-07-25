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
    FluxHelmReleaseSessionDiscoveryAdapter,
    HttpObservatoryDiscoveryAdapter,
    KubernetesDiscoveryAdapter,
    RavnValkyrieDiscoveryAdapter,
    StaticRelationshipDiscoveryAdapter,
    VolundrSessionsDiscoveryAdapter,
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
async def test_kubernetes_discovery_projects_valkyrie_type(tmp_path, monkeypatch) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["labelSelector"] == "niuu.world/cluster=ymir"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "valkyrie-ymir-k8s",
                            "namespace": "nats",
                            "uid": "uid-valkyrie",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "niuu.world/entity-id": "valkyrie-ymir-k8s",
                                "observatory.niuu.world/type": "valkyrie",
                                "app.kubernetes.io/name": "valkyrie",
                                "app.kubernetes.io/component": "resident-agent",
                            },
                        },
                        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        label_selector="niuu.world/cluster=ymir",
        include_kinds=["deployments"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    snapshot = topology_from_discovery(await adapter.discover())
    valkyrie = next(node for node in snapshot["nodes"] if node["typeId"] == "valkyrie")

    assert valkyrie["id"] == "runtime:ymir:nats:valkyrie:valkyrie-ymir-k8s"
    assert valkyrie["status"] == "healthy"


@pytest.mark.asyncio
async def test_kubernetes_discovery_projects_a2a_agent_annotations(tmp_path, monkeypatch) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "niuu-ting",
                            "namespace": "volundr",
                            "uid": "uid-ting",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "app.kubernetes.io/name": "ting",
                                "app.kubernetes.io/component": "saga-coordinator",
                            },
                            "annotations": {
                                "observatory.niuu.world/a2a-card-url": (
                                    "https://yggdrasil.example/.well-known/agent-card.json"
                                ),
                                "observatory.niuu.world/a2a-visibility": "system",
                            },
                        },
                        "status": {
                            "phase": "Running",
                        },
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["pods"],
        service_account_root=str(service_account),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert len(result.entities) == 1
    assert result.entities[0].endpoints == {
        "a2aCard": "https://yggdrasil.example/.well-known/agent-card.json"
    }
    assert result.entities[0].metadata["visibility"] == "system"


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
async def test_kubernetes_discovery_projects_declared_relationships(tmp_path, monkeypatch) -> None:
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
async def test_kubernetes_discovery_supports_generic_tagged_objects(tmp_path, monkeypatch) -> None:
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
        (edge["sourceId"], edge["targetId"], edge["relationType"]) for edge in snapshot["edges"]
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
async def test_ravn_valkyrie_adapter_projects_cross_cluster_dashboard() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/ravn/valkyrie/dashboard"
        return httpx.Response(
            200,
            json={
                "environments": [{"id": "env-k8s-eitri", "health": "watch"}],
                "valkyries": [
                    {
                        "id": "valkyrie-eitri-k8s",
                        "name": "Bryn",
                        "environmentId": "env-k8s-eitri",
                        "status": "online",
                        "persona": "k8s-valkyrie",
                        "specialty": "workshop operator",
                        "autonomyMode": "guarded",
                        "wakefulness": "watching",
                        "flockId": "flock-k8s",
                        "confidence": 0.82,
                    }
                ],
            },
        )

    adapter = RavnValkyrieDiscoveryAdapter(
        base_url="https://ravn.example",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert len(result.entities) == 1
    valkyrie = result.entities[0]
    assert valkyrie.id == "runtime:eitri:nats:valkyrie:valkyrie-eitri-k8s"
    assert valkyrie.kind == "valkyrie"
    assert valkyrie.name == "Bryn"
    assert valkyrie.cluster == "eitri"
    assert valkyrie.status == "healthy"
    assert valkyrie.metadata["ravnEnvironmentId"] == "env-k8s-eitri"
    assert valkyrie.metadata["environmentHealth"] == "watch"


@pytest.mark.asyncio
async def test_static_relationship_adapter_resolves_cross_cluster_refs() -> None:
    result = await CompositeDiscoveryAdapter(
        [
            _StaticAdapter(
                DiscoveryResult(
                    entities=[
                        DiscoveredEntity(
                            id="runtime:ymir:volundr:service:observatory",
                            kind="service",
                            name="observatory",
                            cluster="ymir",
                            namespace="volundr",
                            status="healthy",
                        ),
                        DiscoveredEntity(
                            id="runtime:noatun:volundr:service:observatory",
                            kind="service",
                            name="observatory",
                            cluster="noatun",
                            namespace="volundr",
                            status="healthy",
                        ),
                    ]
                )
            ),
            StaticRelationshipDiscoveryAdapter(
                relationships=[
                    {
                        "source": "service:observatory@ymir/volundr",
                        "target": "service:observatory@noatun/volundr",
                        "relation_type": "observes",
                    }
                ]
            ),
        ]
    ).discover()

    snapshot = topology_from_discovery(result)

    assert snapshot["edges"] == [
        {
            "id": (
                "edge:observes:service-observatory-ymir-volundr:service-observatory-noatun-volundr"
            ),
            "sourceId": "runtime:ymir:volundr:service:observatory",
            "targetId": "runtime:noatun:volundr:service:observatory",
            "kind": "dashed-anim",
            "relationType": "observes",
            "label": "observes",
            "confidence": "declared",
            "evidence": {
                "adapter": "StaticRelationshipDiscoveryAdapter",
                "field": "observatory.discovery.relationships",
            },
        }
    ]


@pytest.mark.asyncio
async def test_volundr_sessions_adapter_projects_running_sessions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/forge/sessions"
        assert request.url.params["status"] == "running"
        assert request.headers["x-auth-user-id"] == "observatory"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "0025dea5-74c6-451f-af4f-980210a07367",
                    "name": "openviking",
                    "status": "running",
                    "model": "codex",
                    "tokens_used": 2048,
                    "chat_endpoint": "wss://sessions.example/session",
                    "a2aCardUrl": "https://sessions.example/.well-known/agent-card.json",
                    "a2aEndpointUrl": "https://sessions.example/a2a",
                    "environmentId": "environment-a",
                    "a2aVisibility": "tenant",
                }
            ],
        )

    result = await CompositeDiscoveryAdapter(
        [
            _StaticAdapter(
                DiscoveryResult(
                    entities=[
                        DiscoveredEntity(
                            id="runtime:noatun:volundr:volundr:volundr",
                            kind="volundr",
                            name="volundr",
                            cluster="noatun",
                            namespace="volundr",
                            status="healthy",
                        )
                    ]
                )
            ),
            VolundrSessionsDiscoveryAdapter(
                base_url="https://noatun.example",
                cluster="noatun",
                headers={"x-auth-user-id": "observatory", "x-auth-roles": "admin"},
                transport=httpx.MockTransport(handler),
            ),
        ]
    ).discover()

    snapshot = topology_from_discovery(result)
    session = next(node for node in snapshot["nodes"] if node["typeId"] == "skuld")

    assert session["label"] == "openviking"
    assert session["parentId"] == "namespace-noatun-skuld"
    assert session["status"] == "healthy"
    assert session["tokens"] == 2048
    assert session["endpoints"] == {
        "chat": "wss://sessions.example/session",
        "a2a": "https://sessions.example/a2a",
        "a2aCard": "https://sessions.example/.well-known/agent-card.json",
    }
    assert session["environmentId"] == "environment-a"
    assert session["visibility"] == "tenant"
    assert session["agentKind"] == "workflow-session"
    assert snapshot["edges"] == [
        {
            "id": (
                "edge:manages:volundr-volundr-noatun-volundr:"
                "runtime-noatun-skuld-skuld-0025dea5-74c6-451f-af4f-980210a07367"
            ),
            "sourceId": "runtime:noatun:volundr:volundr:volundr",
            "targetId": "runtime:noatun:skuld:skuld:0025dea5-74c6-451f-af4f-980210a07367",
            "kind": "solid",
            "relationType": "manages",
            "label": "manages",
            "confidence": "observed",
            "evidence": {
                "adapter": "VolundrSessionsDiscoveryAdapter",
                "field": "GET /api/v1/forge/sessions",
            },
        }
    ]


@pytest.mark.asyncio
async def test_flux_helmrelease_session_adapter_projects_ready_dev_sessions(
    tmp_path, monkeypatch
) -> None:
    service_account = tmp_path / "sa"
    service_account.mkdir()
    (service_account / "token").write_text("token", encoding="utf-8")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")

    def release(name: str, tag: str, ready: bool = True) -> dict[str, object]:
        session_id = name.removeprefix("skuld-")
        return {
            "metadata": {"name": name, "namespace": "skuld", "uid": f"uid-{name}"},
            "spec": {
                "values": {
                    "image": {"tag": tag},
                    "session": {
                        "id": session_id,
                        "name": f"session-{session_id[:4]}",
                        "model": "gpt-5.5",
                        "a2aCardUrl": "https://agent.example/card.json",
                        "a2aEndpointUrl": "https://agent.example/a2a",
                        "environmentId": "production",
                        "a2aVisibility": "tenant",
                        "ownerId": "owner-1",
                        "tenantId": "tenant-1",
                    },
                }
            },
            "status": {
                "conditions": [
                    {
                        "type": "Ready",
                        "status": "True" if ready else "False",
                    }
                ]
            },
        }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token"
        assert request.url.path == "/apis/helm.toolkit.fluxcd.io/v2/namespaces/skuld/helmreleases"
        assert request.url.params["labelSelector"] == "app.kubernetes.io/managed-by=volundr"
        return httpx.Response(
            200,
            json={
                "items": [
                    release("skuld-0025dea5-74c6-451f-af4f-980210a07367", "dev"),
                    release("skuld-4eab4167-7bd2-4a6f-aabe-ba31fe40f98c", "old-branch"),
                    release("skuld-failed", "dev", ready=False),
                ]
            },
        )

    result = await CompositeDiscoveryAdapter(
        [
            _StaticAdapter(
                DiscoveryResult(
                    entities=[
                        DiscoveredEntity(
                            id="runtime:noatun:volundr:volundr:volundr",
                            kind="volundr",
                            name="volundr",
                            cluster="noatun",
                            namespace="volundr",
                            status="healthy",
                        )
                    ]
                )
            ),
            FluxHelmReleaseSessionDiscoveryAdapter(
                cluster="noatun",
                image_tags=["dev"],
                service_account_root=str(service_account),
                transport=httpx.MockTransport(handler),
            ),
        ]
    ).discover()

    snapshot = topology_from_discovery(result)

    session_nodes = [node for node in snapshot["nodes"] if node["typeId"] == "skuld"]
    assert [node["label"] for node in session_nodes] == ["session-0025"]
    assert session_nodes[0]["parentId"] == "namespace-noatun-skuld"
    assert session_nodes[0]["model"] == "gpt-5.5"
    entity = next(item for item in result.entities if item.kind == "skuld")
    assert entity.endpoints == {
        "a2a": "https://agent.example/a2a",
        "a2aCard": "https://agent.example/card.json",
    }
    assert entity.metadata["environmentId"] == "production"
    assert entity.metadata["visibility"] == "tenant"
    assert entity.metadata["ownerId"] == "owner-1"
    assert entity.metadata["tenantId"] == "tenant-1"
    assert len(snapshot["edges"]) == 1
    assert snapshot["edges"][0]["relationType"] == "manages"


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
