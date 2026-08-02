from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from observatory.discovery import ObservatoryDiscoveryService
from observatory.entity_discovery import (
    BifrostCatalogDiscoveryAdapter,
    CompositeDiscoveryAdapter,
    DiscoveredEntity,
    DiscoveryResult,
    FluxHelmReleaseSessionDiscoveryAdapter,
    KubernetesDiscoveryAdapter,
    LaevateinnGatewayDiscoveryAdapter,
    MimirDiscoveryAdapter,
    RavnResidentsDiscoveryAdapter,
    RavnValkyrieDiscoveryAdapter,
    StaticRelationshipDiscoveryAdapter,
    TingWorkDiscoveryAdapter,
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

    valkyrie = next(e for e in result.entities if e.kind == "valkyrie")
    assert valkyrie.id == "runtime:eitri:nats:valkyrie:valkyrie-eitri-k8s"
    assert valkyrie.name == "Bryn"
    assert valkyrie.cluster == "eitri"
    assert valkyrie.status == "healthy"
    assert valkyrie.metadata["ravnEnvironmentId"] == "env-k8s-eitri"
    assert valkyrie.metadata["environmentHealth"] == "watch"


@pytest.mark.asyncio
async def test_valkyries_are_connected_to_the_flock_they_report() -> None:
    """Membership was carried as metadata and drawn as nothing.

    Seven Valkyries all reporting `flock-k8s` produced seven unconnected dots,
    because this adapter emitted no edges at all and no flock node existed for
    the residents adapter's `member_of` edges to land on either.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "environments": [{"id": "env-k8s-eitri", "health": "watch"}],
                "valkyries": [
                    {
                        "id": f"valkyrie-{name}",
                        "name": name,
                        "environmentId": "env-k8s-eitri",
                        "status": "online",
                        "flockId": "flock-k8s",
                    }
                    for name in ("Bryn", "Eir", "Hildr")
                ],
            },
        )

    adapter = RavnValkyrieDiscoveryAdapter(
        base_url="https://ravn.example",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    flocks = [e for e in result.entities if e.kind == "flock"]
    assert len(flocks) == 1
    # One node for the mesh, not one per member and not one per cluster.
    assert flocks[0].id == "flock:flock-k8s"
    assert flocks[0].name == "K8S flock"
    assert not flocks[0].cluster

    members = [e for e in result.entities if e.kind == "valkyrie"]
    assert len(members) == 3
    assert {e["sourceId"] for e in result.edges} == {m.id for m in members}
    assert {e["targetId"] for e in result.edges} == {"flock:flock-k8s"}
    assert all(e["relationType"] == "member_of" for e in result.edges)


@pytest.mark.asyncio
async def test_a_valkyrie_without_a_flock_gets_no_dangling_edge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "environments": [{"id": "env-k8s-eitri"}],
                "valkyries": [
                    {"id": "solo", "name": "Solo", "environmentId": "env-k8s-eitri"},
                ],
            },
        )

    adapter = RavnValkyrieDiscoveryAdapter(
        base_url="https://ravn.example",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert not result.edges
    assert not [e for e in result.entities if e.kind == "flock"]


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


# ── Registry-driven entity types ─────────────────────────────────────────────
# The set of renderable types is the registry's job, not a constant in this
# module. A hardcoded set drifted from the registry and silently downgraded
# realms, models and runs to `service` for months.


def _entity(kind: str, name: str = "thing") -> DiscoveredEntity:
    return DiscoveredEntity(id=f"e-{kind}", kind=kind, name=name, cluster="ymir")


@pytest.mark.parametrize("kind", ["realm", "model", "run", "ravn_run"])
def test_seed_types_are_not_downgraded_to_service(kind: str) -> None:
    snapshot = topology_from_discovery(DiscoveryResult(entities=[_entity(kind)]))

    node = next(n for n in snapshot["nodes"] if n["id"] == f"e-{kind}")
    assert node["typeId"] == kind


def test_registry_types_override_the_seed() -> None:
    """An operator can register a new type without a code change."""
    snapshot = topology_from_discovery(
        DiscoveryResult(entities=[_entity("weathervane")]),
        known_type_ids={"weathervane"},
    )

    node = next(n for n in snapshot["nodes"] if n["id"] == "e-weathervane")
    assert node["typeId"] == "weathervane"


def test_unregistered_type_renders_as_service_but_says_so() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(entities=[_entity("weathervane")]),
        known_type_ids={"service"},
    )

    node = next(n for n in snapshot["nodes"] if n["id"] == "e-weathervane")
    assert node["typeId"] == "service"

    warnings = [e for e in snapshot["events"] if e.get("subject") == "registry"]
    assert len(warnings) == 1
    assert "weathervane" in warnings[0]["body"]


def test_no_registry_warning_when_every_type_is_known() -> None:
    snapshot = topology_from_discovery(DiscoveryResult(entities=[_entity("mimir")]))

    assert [e for e in snapshot["events"] if e.get("subject") == "registry"] == []


# ── Placement outside Kubernetes ─────────────────────────────────────────────
# Residents also run as bare-metal systemd units and as Docker containers on a
# workstation. Forcing every entity under a synthesised cluster/namespace is
# what made the graph Kubernetes-shaped.


def _node(snapshot: dict, node_id: str) -> dict:
    return next(n for n in snapshot["nodes"] if n["id"] == node_id)


def test_bare_metal_resident_is_placed_on_its_host_not_a_fake_cluster() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="ravn-ivaldi",
                    kind="ravn_long",
                    name="ivaldi",
                    realm="sparks",
                    host="saehrimnir",
                )
            ]
        )
    )

    assert _node(snapshot, "ravn-ivaldi")["parentId"] == "host-saehrimnir"
    assert not [n for n in snapshot["nodes"] if n["typeId"] == "cluster"]
    assert not [n for n in snapshot["nodes"] if n["typeId"] == "namespace"]


def test_a_host_outside_a_cluster_hangs_from_its_realm() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="ravn-ivaldi",
                    kind="ravn_long",
                    name="ivaldi",
                    realm="sparks",
                    host="saehrimnir",
                )
            ]
        )
    )

    assert _node(snapshot, "host-saehrimnir")["parentId"] == "realm-sparks"
    assert _node(snapshot, "realm-sparks")["parentId"] is None


def test_an_entity_with_no_placement_stays_top_level() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(entities=[DiscoveredEntity(id="lonely", kind="mimir", name="mímir")])
    )

    assert _node(snapshot, "lonely")["parentId"] is None
    assert len(snapshot["nodes"]) == 1


def test_a_cluster_is_nested_under_its_realm() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="svc", kind="service", name="api", realm="asgard", cluster="ymir"
                )
            ]
        )
    )

    assert _node(snapshot, "cluster-ymir")["parentId"] == "realm-asgard"


def test_a_clusters_realm_is_filled_in_by_a_later_entity() -> None:
    """Whichever entity mentions the cluster first may not know its realm."""
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(id="a", kind="service", name="a", cluster="ymir"),
                DiscoveredEntity(id="b", kind="service", name="b", realm="asgard", cluster="ymir"),
            ]
        )
    )

    assert _node(snapshot, "cluster-ymir")["parentId"] == "realm-asgard"


def test_namespace_still_wins_for_a_kubernetes_workload_on_a_named_host() -> None:
    """The host stays a sibling under the cluster; namespace is the containment
    the graph is drawn around."""
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="pod-1",
                    kind="service",
                    name="api",
                    cluster="ymir",
                    namespace="niuu",
                    host="node-1",
                )
            ]
        )
    )

    assert _node(snapshot, "pod-1")["parentId"] == "namespace-ymir-niuu"
    assert _node(snapshot, "host-ymir-node-1")["parentId"] == "cluster-ymir"


def test_host_ids_are_cluster_scoped_so_two_clusters_can_both_have_node_1() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(id="a", kind="service", name="a", cluster="ymir", host="node-1"),
                DiscoveredEntity(id="b", kind="service", name="b", cluster="noatun", host="node-1"),
            ]
        )
    )

    host_ids = {n["id"] for n in snapshot["nodes"] if n["typeId"] == "host"}
    assert host_ids == {"host-ymir-node-1", "host-noatun-node-1"}


def test_a_discovered_host_is_not_duplicated_by_a_synthesised_one() -> None:
    """An adapter that discovers the host itself owns that node."""
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="local:saehrimnir", kind="host", name="saehrimnir", realm="sparks"
                ),
                DiscoveredEntity(
                    id="ravn-ivaldi",
                    kind="ravn_long",
                    name="ivaldi",
                    realm="sparks",
                    host="saehrimnir",
                ),
            ]
        )
    )

    hosts = [n for n in snapshot["nodes"] if n["typeId"] == "host"]
    assert [h["id"] for h in hosts] == ["local:saehrimnir"]
    assert _node(snapshot, "ravn-ivaldi")["parentId"] == "local:saehrimnir"


def test_a_discovered_realm_is_not_duplicated_by_a_synthesised_one() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(id="realm:sparks", kind="realm", name="sparks"),
                DiscoveredEntity(id="ravn-ivaldi", kind="ravn_long", name="ivaldi", realm="sparks"),
            ]
        )
    )

    realms = [n for n in snapshot["nodes"] if n["typeId"] == "realm"]
    assert [r["id"] for r in realms] == ["realm:sparks"]
    assert _node(snapshot, "ravn-ivaldi")["parentId"] == "realm:sparks"


def test_placement_names_are_carried_on_the_node() -> None:
    snapshot = topology_from_discovery(
        DiscoveryResult(
            entities=[
                DiscoveredEntity(
                    id="ravn-ivaldi",
                    kind="ravn_long",
                    name="ivaldi",
                    realm="sparks",
                    host="saehrimnir",
                )
            ]
        )
    )

    node = _node(snapshot, "ravn-ivaldi")
    assert node["realm"] == "sparks"
    assert node["host"] == "saehrimnir"
    assert node["clusterName"] == ""
    assert node["namespace"] == ""


# ── Snapshot revision ────────────────────────────────────────────────────────
# The SSE stream deduped on `timestamp`, which topology_from_discovery
# re-stamps on every materialization, so it never matched and every tick
# resent the whole snapshot.


def test_revision_is_stable_across_materializations_of_the_same_graph() -> None:
    result = DiscoveryResult(entities=[_entity("mimir")])

    assert (
        topology_from_discovery(result)["revision"] == topology_from_discovery(result)["revision"]
    )


def test_revision_changes_when_the_graph_does() -> None:
    one = topology_from_discovery(DiscoveryResult(entities=[_entity("mimir")]))
    two = topology_from_discovery(DiscoveryResult(entities=[_entity("mimir"), _entity("bifrost")]))

    assert one["revision"] != two["revision"]


def test_revision_ignores_volatile_event_timestamps() -> None:
    """Adapters re-stamp their events every poll; that is not a graph change."""

    def snapshot(stamp: str) -> str:
        return topology_from_discovery(
            DiscoveryResult(
                entities=[_entity("mimir")],
                events=[{"id": "e-1", "type": "info", "timestamp": stamp}],
            )
        )["revision"]

    assert snapshot("2026-08-01T12:00:00Z") == snapshot("2026-08-01T12:00:30Z")


def test_revision_changes_when_a_new_event_appears() -> None:
    base = topology_from_discovery(DiscoveryResult(entities=[_entity("mimir")]))
    with_event = topology_from_discovery(
        DiscoveryResult(entities=[_entity("mimir")], events=[{"id": "e-1", "type": "info"}])
    )

    assert base["revision"] != with_event["revision"]


# ── Kubernetes hosts ─────────────────────────────────────────────────────────
# Hosts are what make the graph show where things actually run — which box,
# with which GPU — rather than an undifferentiated cluster blob.


def _service_account(tmp_path) -> str:
    root = tmp_path / "sa"
    root.mkdir()
    (root / "token").write_text("token", encoding="utf-8")
    return str(root)


def _node_item(
    name: str = "spark-1",
    *,
    gpu: str | None = None,
    ready: bool = True,
) -> dict:
    labels = {"node-role.kubernetes.io/control-plane": ""}
    capacity = {"cpu": "112", "memory": "263849876Ki"}
    if gpu:
        labels["nvidia.com/gpu.product"] = gpu
        capacity["nvidia.com/gpu"] = "4"
    return {
        "metadata": {"name": name, "uid": f"uid-{name}", "labels": labels},
        "status": {
            "capacity": capacity,
            "nodeInfo": {
                "osImage": "Ubuntu 24.04.1 LTS",
                "architecture": "arm64",
                "kernelVersion": "6.11.0",
                "kubeletVersion": "v1.31.4",
            },
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


@pytest.mark.asyncio
async def test_nodes_are_discovered_as_hosts_with_their_hardware(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pods":
            return httpx.Response(200, json={"items": []})
        assert request.url.path == "/api/v1/nodes"
        return httpx.Response(200, json={"items": [_node_item(gpu="NVIDIA-GB10")]})

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        realm="asgard",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    host = result.entities[0]
    assert host.kind == "host"
    assert host.name == "spark-1"
    assert host.cluster == "ymir"
    assert host.realm == "asgard"
    assert host.metadata["cores"] == 112
    assert host.metadata["ram"] == 251  # 263849876Ki rendered in whole GiB
    assert host.metadata["gpu"] == "NVIDIA-GB10"
    assert host.metadata["gpuCount"] == 4
    assert host.metadata["os"] == "Ubuntu 24.04.1 LTS"
    assert host.metadata["roles"] == ["control-plane"]


@pytest.mark.asyncio
async def test_a_node_without_a_gpu_reports_none(tmp_path, monkeypatch) -> None:
    """Absent is different from zero — the UI should not show a GPU chip."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"items": [_node_item()]})
        ),
    )

    result = await adapter.discover()

    assert "gpu" not in result.entities[0].metadata


@pytest.mark.asyncio
async def test_a_not_ready_node_is_failed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"items": [_node_item(ready=False)]})
        ),
    )

    result = await adapter.discover()

    assert result.entities[0].status == "failed"


@pytest.mark.asyncio
async def test_nodes_are_listed_without_the_workload_label_selector(tmp_path, monkeypatch) -> None:
    """Nodes carry none of our labels; filtering them would return nothing."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("labelSelector"))
        return httpx.Response(200, json={"items": []})

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        label_selector="niuu.world/cluster=ymir",
        include_kinds=["nodes", "pods"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    await adapter.discover()

    assert seen == [None, "niuu.world/cluster=ymir"]


@pytest.mark.asyncio
async def test_a_host_hangs_from_its_cluster_not_from_itself(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        realm="asgard",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"items": [_node_item()]})
        ),
    )

    snapshot = topology_from_discovery(await adapter.discover())

    host = next(n for n in snapshot["nodes"] if n["typeId"] == "host")
    assert host["parentId"] == "cluster-ymir"
    assert _node(snapshot, "cluster-ymir")["parentId"] == "realm-asgard"


@pytest.mark.asyncio
async def test_a_pod_records_the_host_it_landed_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "niuu-ravn-abc",
                            "namespace": "volundr",
                            "uid": "uid-pod",
                            "labels": {"niuu.world/cluster": "ymir"},
                        },
                        "spec": {"nodeName": "spark-1"},
                        "status": {"phase": "Running"},
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["pods"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert result.entities[0].host == "spark-1"


@pytest.mark.asyncio
async def test_an_operator_declared_type_is_taken_verbatim(tmp_path, monkeypatch) -> None:
    """Registering a type and labelling workloads with it must not need a code
    change — that is the whole point of the registry."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "weathervane",
                            "namespace": "volundr",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "niuu.world/kind": "weathervane",
                            },
                        },
                        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["deployments"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert result.entities[0].kind == "weathervane"


@pytest.mark.asyncio
async def test_a_generic_component_label_stays_a_guess(tmp_path, monkeypatch) -> None:
    """A third-party chart's component name must not invent an entity type."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "metadata": {
                            "name": "sidecar-thing",
                            "namespace": "volundr",
                            "labels": {
                                "niuu.world/cluster": "ymir",
                                "app.kubernetes.io/component": "some-vendor-sidecar",
                            },
                        },
                        "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
                    }
                ]
            },
        )

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["deployments"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert result.entities[0].kind == "service"


# ── Service adapters ─────────────────────────────────────────────────────────
# These are what fill the graph in beyond bare Kubernetes objects, and what
# replaced the Guild's fabricated Bifröst model children.


def _routed(routes: dict[str, object]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path not in routes:
            return httpx.Response(404, json={"detail": request.url.path})
        return httpx.Response(200, json=routes[request.url.path])

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_bifrost_discovers_the_real_catalogue_not_a_hardcoded_one() -> None:
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {
                        "id": "nemotron-super",
                        "name": "Nemotron Super",
                        "vendor": "nvidia",
                        "tier": "large",
                        "enabled": True,
                        "supports_tools": True,
                    },
                    {"id": "claude-opus-5", "name": "Opus 5", "vendor": "anthropic"},
                ],
                "/api/v1/bifrost/providers": [
                    {
                        "key": "saehrimnir",
                        "vendor": "nvidia",
                        "base_url": "http://vllm.volundr.svc.cluster.local",
                        "model_ids": ["nemotron-super"],
                    },
                    {
                        "key": "anthropic",
                        "vendor": "anthropic",
                        "base_url": "https://api.anthropic.com",
                        "model_ids": ["claude-opus-5"],
                    },
                ],
            }
        ),
    )

    result = await adapter.discover()

    models = {e.metadata["modelId"]: e for e in result.entities if e.kind == "model"}
    assert set(models) == {"nemotron-super", "claude-opus-5"}
    assert models["nemotron-super"].metadata["location"] == "internal"
    assert models["claude-opus-5"].metadata["location"] == "external"


_DEVICE_RECORD = {
    "id": "printer-01",
    "name": "Laevateinn",
    "machineName": "Laevateinn MSLA-8K",
    "firmwareVersion": "V1.0.0",
    "mainboardId": "1aeva7e100000001",
    "state": "connected",
    "status": {
        "TempOfBox": 28.0,
        "PrintInfo": {
            "Status": 8,
            "CurrentLayer": 208,
            "TotalLayer": 512,
            "Filename": "dental-arch.ctb",
        },
    },
    "attributes": {"Resolution": "7680x4320"},
}


def _gateway_adapter(record: dict[str, object], **kwargs: object):
    return LaevateinnGatewayDiscoveryAdapter(
        base_url="http://gateway-01.test",
        cluster="eitri",
        realm="svartalfheim",
        namespace="laevateinn",
        kind=kwargs.pop("kind", "printer"),
        transport=_routed({"/api/printers": [record]}),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_the_registry_type_comes_from_config_not_from_this_module() -> None:
    """Nothing here decides what a device *is*.

    A hardcoded kind would put a second, invisible type vocabulary next to the
    registry — the registry is the source of truth for types, and a new class
    of device should need a config line rather than a release.
    """
    result = await _gateway_adapter(_DEVICE_RECORD, kind="vaettir").discover()

    device = result.entities[0]
    assert device.kind == "vaettir"
    assert device.id == "vaettir:eitri:printer-01"


@pytest.mark.asyncio
async def test_a_gateway_with_no_configured_kind_refuses_to_guess() -> None:
    with pytest.raises(ValueError, match="kind"):
        LaevateinnGatewayDiscoveryAdapter(base_url="http://gateway-01.test", cluster="eitri")


@pytest.mark.asyncio
async def test_a_device_arrives_with_the_job_it_is_running() -> None:
    """A farm is only useful on the canvas if it says what it is doing."""
    result = await _gateway_adapter(_DEVICE_RECORD).discover()

    device = result.entities[0]
    assert device.kind == "printer"
    assert device.name == "Laevateinn"
    assert device.cluster == "eitri"
    assert device.namespace == "laevateinn"
    assert device.status == "healthy"
    assert device.metadata["job"]["Filename"] == "dental-arch.ctb"
    assert device.metadata["job"]["CurrentLayer"] == 208


@pytest.mark.asyncio
async def test_fields_are_carried_as_the_gateway_names_them() -> None:
    """Mapping them to a schema written here would mean a release per field."""
    record = json.loads(json.dumps(_DEVICE_RECORD))
    record["somethingAddedLater"] = "value"

    result = await _gateway_adapter(record).discover()

    metadata = result.entities[0].metadata
    assert metadata["machineName"] == "Laevateinn MSLA-8K"
    assert metadata["firmwareVersion"] == "V1.0.0"
    assert metadata["somethingAddedLater"] == "value"
    # The gateway's own nested detail stays out: hundreds of hardware keys
    # would bury the fields the graph is actually asked about.
    assert "attributes" not in metadata
    assert "status" not in metadata


@pytest.mark.asyncio
async def test_a_devices_state_is_reported_as_state_not_as_a_rehearsal() -> None:
    """Whether the thing answering is a rig or a bench is not estate state.

    The graph reports what a machine is doing. Carrying the gateway's simulator
    bookkeeping through put a caveat on the reading rather than the reading.
    """
    record = json.loads(json.dumps(_DEVICE_RECORD))
    record["isSimulator"] = True
    record["simulatorScenario"] = "failing_prints"

    result = await _gateway_adapter(record).discover()

    metadata = result.entities[0].metadata
    assert "isSimulator" not in metadata
    assert "simulatorScenario" not in metadata
    # What it is doing still comes through in full.
    assert metadata["job"]["Filename"] == "dental-arch.ctb"


@pytest.mark.asyncio
async def test_device_status_reflects_reachability_and_faults() -> None:
    async def status_for(**overrides: object) -> str:
        record = json.loads(json.dumps(_DEVICE_RECORD))
        record.update(overrides)
        result = await _gateway_adapter(record).discover()
        return result.entities[0].status

    assert await status_for() == "healthy"
    # A past error is not a present fault. `lastError` is never cleared, so
    # reading it as one reported a farm running normally as entirely degraded.
    assert await status_for(lastError={"ErrorCode": 8}) == "healthy"
    # A gateway that cannot reach the device outranks whatever it last said.
    assert await status_for(state="disconnected") == "failed"

    async def with_job_error(code: int) -> str:
        record = json.loads(json.dumps(_DEVICE_RECORD))
        record["status"]["PrintInfo"]["ErrorNumber"] = code
        result = await _gateway_adapter(record).discover()
        return result.entities[0].status

    # The running job's error number is the live signal.
    assert await with_job_error(8) == "degraded"
    assert await with_job_error(0) == "healthy"


@pytest.mark.asyncio
async def test_a_device_cannot_overwrite_its_own_node_identity() -> None:
    """Metadata extends a node; it must not be able to rewrite it.

    A Laevateinn record carries both `id` and `host` — the machine's own id
    and the address the gateway reaches it on. Splatted onto the node they
    replaced the node id and its placement, which is a corrupted graph rather
    than a cosmetic clash.
    """
    record = json.loads(json.dumps(_DEVICE_RECORD))
    record["host"] = "127.0.0.1"
    result = await _gateway_adapter(record).discover()

    topology = topology_from_discovery(
        result,
        known_type_ids={"printer", "realm", "cluster", "namespace"},
    )
    printer = next(n for n in topology["nodes"] if n["typeId"] == "printer")

    assert printer["id"] == "printer:eitri:printer-01"
    assert printer["namespace"] == "laevateinn"
    # The device's own address does not become its placement on a host.
    assert printer["host"] == ""
    # It is still readable, just not as a node field.
    assert printer["machineName"] == "Laevateinn MSLA-8K"


@pytest.mark.asyncio
async def test_an_unreachable_gateway_names_itself() -> None:
    adapter = LaevateinnGatewayDiscoveryAdapter(
        base_url="http://gateway-03.test",
        cluster="eitri",
        kind="printer",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )

    result = await adapter.discover()

    assert not result.entities
    assert result.events and "gateway-03.test" in result.events[0]["message"]


@pytest.mark.asyncio
async def test_hosted_models_sit_in_a_vendor_cloud_not_in_the_cluster() -> None:
    """A vendor's model is not in our cluster — the Bifrost calling it is.

    Parenting every model to the gateway drew hosted Claude and GPT inside the
    cluster rectangle, which reads as the estate running them.
    """
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        realm="asgard",
        namespace="volundr",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "local-llama", "name": "Llama", "vendor": "local"},
                    {"id": "claude-opus-5", "name": "Opus 5", "vendor": "anthropic"},
                    {"id": "claude-sonnet-5", "name": "Sonnet 5", "vendor": "anthropic"},
                    {"id": "gpt-5", "name": "GPT-5", "vendor": "openai"},
                ],
                "/api/v1/bifrost/providers": [
                    {
                        "key": "local",
                        "vendor": "local",
                        "base_url": "http://vllm.volundr.svc.cluster.local",
                        "model_ids": ["local-llama"],
                    },
                    {
                        "key": "anthropic",
                        "vendor": "anthropic",
                        "base_url": "https://api.anthropic.com",
                        "model_ids": ["claude-opus-5", "claude-sonnet-5"],
                    },
                    {
                        "key": "openai",
                        "vendor": "openai",
                        "base_url": "https://api.openai.com",
                        "model_ids": ["gpt-5"],
                    },
                ],
            }
        ),
    )

    result = await adapter.discover()
    by_model = {e.metadata["modelId"]: e for e in result.entities if e.kind == "model"}
    clouds = {e.id: e for e in result.entities if e.kind == "cloud"}

    # One cloud per vendor that serves something, named as the vendor writes it.
    assert {c.name for c in clouds.values()} == {"Anthropic", "OpenAI"}
    # A cloud has no placement in our estate at all.
    for cloud in clouds.values():
        assert (cloud.cluster, cloud.namespace, cloud.realm) == ("", "", "")
        assert not cloud.parent_id

    hosted = by_model["claude-opus-5"]
    assert hosted.parent_id in clouds
    assert (hosted.cluster, hosted.namespace) == ("", "")
    # Both Anthropic models land in the same cloud.
    assert by_model["claude-sonnet-5"].parent_id == hosted.parent_id
    assert by_model["gpt-5"].parent_id != hosted.parent_id

    # Self-hosted weights stay where they actually run.
    local = by_model["local-llama"]
    assert local.parent_id == "bifrost:ymir"
    assert local.cluster == "ymir"

    # The gateway still routes to the hosted model — moving it out of the
    # cluster must not sever the link that says who calls it.
    assert any(e["targetId"] == hosted.id and e["sourceId"] == "bifrost:ymir" for e in result.edges)


@pytest.mark.asyncio
async def test_our_own_gpus_behind_our_own_ingress_are_not_someone_elses() -> None:
    """Where the request travels is not the question; whose silicon answers is.

    valhalla serves Nemotron and Qwen from GPUs in valaskjalf, reached through
    our own public ingress rather than a cluster-local Service. Classifying by
    hostname suffix alone called both of them vendor-hosted, so the estate's
    own weights were drawn outside the estate — and, once hosted models moved
    into vendor clouds, into a cloud named after a provider key.
    """
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="valhalla",
        internal_domains=["niuu.world"],
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "nvidia/nemotron-3-super", "vendor": "valaskjalf-nemotron"},
                    {"id": "claude-opus-5", "vendor": "anthropic"},
                ],
                "/api/v1/bifrost/providers": [
                    {
                        "key": "valaskjalf-nemotron",
                        "vendor": "valaskjalf-nemotron",
                        "base_url": "https://nemotron-3-super-vllm.valaskjalf.asgard.niuu.world",
                        "model_ids": ["nvidia/nemotron-3-super"],
                    },
                    {
                        "key": "anthropic",
                        "vendor": "anthropic",
                        "base_url": "https://api.anthropic.com",
                        "model_ids": ["claude-opus-5"],
                    },
                ],
            }
        ),
    )

    result = await adapter.discover()
    by_model = {e.metadata["modelId"]: e for e in result.entities if e.kind == "model"}

    nemotron = by_model["nvidia/nemotron-3-super"]
    assert nemotron.metadata["location"] == "internal"
    assert nemotron.parent_id == "bifrost:valhalla"
    assert nemotron.cluster == "valhalla"
    # And no cloud was invented for it out of the provider key.
    assert {e.name for e in result.entities if e.kind == "cloud"} == {"Anthropic"}

    # A real vendor endpoint is still outside, so the rule has not gone soft.
    assert by_model["claude-opus-5"].metadata["location"] == "external"


@pytest.mark.asyncio
async def test_a_lookalike_domain_is_not_our_domain() -> None:
    """`niuu.world.evil.test` and `notniuu.world` must not read as ours."""
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        internal_domains=[".niuu.world "],  # config may be untidy
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "impostor", "vendor": "acme"},
                    {"id": "suffix-impostor", "vendor": "acme"},
                    {"id": "genuine", "vendor": "acme"},
                ],
                "/api/v1/bifrost/providers": [
                    {
                        "key": "a",
                        "base_url": "https://niuu.world.evil.test",
                        "model_ids": ["impostor"],
                    },
                    {
                        "key": "b",
                        "base_url": "https://notniuu.world",
                        "model_ids": ["suffix-impostor"],
                    },
                    {"key": "c", "base_url": "https://niuu.world", "model_ids": ["genuine"]},
                ],
            }
        ),
    )

    result = await adapter.discover()
    by_model = {e.metadata["modelId"]: e for e in result.entities if e.kind == "model"}

    assert by_model["impostor"].metadata["location"] == "external"
    assert by_model["suffix-impostor"].metadata["location"] == "external"
    # The zone apex itself is ours.
    assert by_model["genuine"].metadata["location"] == "internal"


@pytest.mark.asyncio
async def test_a_model_with_no_provider_falls_back_to_its_own_vendor() -> None:
    """valhalla lists nine models its provider config never mentions.

    With nothing but an absent provider to go on they were all reported
    `unknown`, so genuinely metered Claude and GPT calls were drawn inside the
    cluster. The model's own vendor is real configuration and answers it.
    """
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="valhalla",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "claude-opus-5", "vendor": "anthropic"},
                    {"id": "llama3.2:latest", "vendor": "local"},
                    {"id": "nameless", "vendor": ""},
                ],
                "/api/v1/bifrost/providers": [],
            }
        ),
    )

    result = await adapter.discover()
    by_model = {e.metadata["modelId"]: e for e in result.entities if e.kind == "model"}

    assert by_model["claude-opus-5"].metadata["location"] == "external"
    assert by_model["llama3.2:latest"].metadata["location"] == "internal"
    # Nothing to read: say so rather than assert a cost either way.
    assert by_model["nameless"].metadata["location"] == "unknown"
    assert by_model["nameless"].parent_id == "bifrost:valhalla"


@pytest.mark.asyncio
async def test_two_clusters_calling_one_vendor_share_its_cloud() -> None:
    """Two Bifrosts calling Anthropic are calling the same Anthropic."""
    routes = {
        "/api/v1/bifrost/models": [
            {"id": "claude-opus-5", "name": "Opus 5", "vendor": "anthropic"}
        ],
        "/api/v1/bifrost/providers": [
            {
                "key": "anthropic",
                "vendor": "anthropic",
                "base_url": "https://api.anthropic.com",
                "model_ids": ["claude-opus-5"],
            }
        ],
    }
    cloud_ids = set()
    for cluster in ("ymir", "valhalla"):
        adapter = BifrostCatalogDiscoveryAdapter(
            base_url="http://bifrost.test",
            cluster=cluster,
            transport=_routed(routes),
        )
        result = await adapter.discover()
        cloud_ids.update(e.id for e in result.entities if e.kind == "cloud")

    assert len(cloud_ids) == 1


@pytest.mark.asyncio
async def test_no_cloud_is_emitted_when_nothing_is_hosted_outside() -> None:
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [{"id": "local-llama", "vendor": "local"}],
                "/api/v1/bifrost/providers": [
                    {
                        "key": "local",
                        "vendor": "local",
                        "base_url": "http://vllm.volundr.svc.cluster.local",
                        "model_ids": ["local-llama"],
                    }
                ],
            }
        ),
    )

    result = await adapter.discover()

    assert not [e for e in result.entities if e.kind == "cloud"]


@pytest.mark.asyncio
async def test_bifrost_model_edges_are_observed_from_provider_config() -> None:
    """The old fabricated children were labelled `inferred`, which made a
    hardcoded guess look like a weak observation."""
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [{"id": "m-1", "name": "M1"}],
                "/api/v1/bifrost/providers": [
                    {"key": "p", "base_url": "https://api.example.test", "model_ids": ["m-1"]}
                ],
            }
        ),
    )

    result = await adapter.discover()

    assert result.edges[0]["confidence"] == "observed"
    # model_ids, not base_url: the catalogue API does not expose provider URLs.
    assert result.edges[0]["evidence"]["field"] == "model_ids"


@pytest.mark.asyncio
async def test_a_model_no_provider_serves_gets_no_edge() -> None:
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [{"id": "orphan", "name": "Orphan"}],
                "/api/v1/bifrost/providers": [],
            }
        ),
    )

    result = await adapter.discover()

    assert result.edges == []
    assert next(e for e in result.entities if e.kind == "model").metadata["location"] == "unknown"


@pytest.mark.asyncio
async def test_ravn_discovers_residents_including_non_cluster_ones() -> None:
    adapter = RavnResidentsDiscoveryAdapter(
        base_url="http://ravn.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/ravn/ravens": [
                    {
                        "id": "ivaldi",
                        "resident_name": "Ivaldi",
                        "persona_name": "Workshop Steward",
                        "status": "online",
                        "model": "nemotron",
                        "deployment": "local",
                        "location": "saehrimnir",
                        "flock_id": "workshop",
                    }
                ]
            }
        ),
    )

    result = await adapter.discover()

    resident = result.entities[0]
    assert resident.kind == "ravn_long"
    assert resident.name == "Ivaldi"
    assert resident.host == "saehrimnir"
    assert resident.status == "healthy"
    assert resident.metadata["deployment"] == "local"


@pytest.mark.asyncio
async def test_a_resident_in_a_flock_gets_a_membership_edge() -> None:
    adapter = RavnResidentsDiscoveryAdapter(
        base_url="http://ravn.test",
        cluster="ymir",
        transport=_routed(
            {"/api/v1/ravn/ravens": [{"id": "a", "resident_name": "A", "flock_id": "workshop"}]}
        ),
    )

    result = await adapter.discover()

    assert result.edges[0]["relationType"] == "member_of"
    assert result.edges[0]["targetId"] == "flock:workshop"


@pytest.mark.asyncio
async def test_a_resident_without_a_flock_gets_no_membership_edge() -> None:
    adapter = RavnResidentsDiscoveryAdapter(
        base_url="http://ravn.test",
        cluster="ymir",
        transport=_routed({"/api/v1/ravn/ravens": [{"id": "a", "resident_name": "A"}]}),
    )

    assert (await adapter.discover()).edges == []


@pytest.mark.asyncio
async def test_ting_reports_what_is_in_flight() -> None:
    adapter = TingWorkDiscoveryAdapter(
        base_url="http://ting.test",
        cluster="ymir",
        transport=_routed({"/api/v1/ting/runs/summary": {"running": 2, "completed": 9}}),
    )

    result = await adapter.discover()

    ting = result.entities[0]
    assert ting.kind == "ting"
    assert ting.status == "healthy"
    assert ting.metadata["activeRuns"] == 2
    assert ting.metadata["totalRuns"] == 11


@pytest.mark.asyncio
async def test_ting_with_nothing_running_is_idle_not_failed() -> None:
    adapter = TingWorkDiscoveryAdapter(
        base_url="http://ting.test",
        cluster="ymir",
        transport=_routed({"/api/v1/ting/runs/summary": {"completed": 3}}),
    )

    assert (await adapter.discover()).entities[0].status == "idle"


@pytest.mark.asyncio
async def test_mimir_reports_pages_and_mounts() -> None:
    adapter = MimirDiscoveryAdapter(
        base_url="http://mimir.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/mimir/stats": {
                    "page_count": 203,
                    "categories": ["runbooks"],
                    "healthy": True,
                },
                "/api/v1/mimir/mounts": [
                    {
                        "name": "shared",
                        "role": "primary",
                        "status": "ok",
                        "pages": 203,
                        "size_kb": 900,
                    },
                ],
            }
        ),
    )

    result = await adapter.discover()

    mimir = result.entities[0]
    assert mimir.kind == "mimir"
    assert mimir.status == "healthy"
    assert mimir.metadata["pages"] == 203
    assert mimir.metadata["mountCount"] == 1
    assert mimir.metadata["mounts"][0]["name"] == "shared"


@pytest.mark.asyncio
async def test_an_unhealthy_mimir_is_failed() -> None:
    adapter = MimirDiscoveryAdapter(
        base_url="http://mimir.test",
        cluster="ymir",
        transport=_routed(
            {"/api/v1/mimir/stats": {"page_count": 0, "healthy": False}, "/api/v1/mimir/mounts": []}
        ),
    )

    assert (await adapter.discover()).entities[0].status == "failed"


@pytest.mark.asyncio
async def test_an_unreachable_service_warns_instead_of_raising() -> None:
    """One dead service must not empty the graph the others contributed to."""
    adapter = MimirDiscoveryAdapter(
        base_url="http://mimir.test",
        cluster="ymir",
        transport=httpx.MockTransport(lambda _r: httpx.Response(503)),
    )

    result = await adapter.discover()

    assert result.entities == []
    assert result.events[0]["level"] == "warning"
    assert "mimir" in result.events[0]["subject"]


# ── Location classification ──────────────────────────────────────────────────
# This value tells an operator whether their traffic leaves the building, so a
# lookalike host must not be able to claim it stays inside.


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("http://localhost:8000", "internal"),
        ("http://127.0.0.1:8000", "internal"),
        ("http://vllm.volundr.svc.cluster.local", "internal"),
        ("http://box.internal", "internal"),
        ("https://api.anthropic.com", "external"),
        ("", "unknown"),
        # A substring test would have called all three of these internal.
        ("https://localhost.attacker.example/v1", "external"),
        ("https://api.vendor.test/v1?probe=.svc.cluster.local", "external"),
        ("https://not-localhost.example.com", "external"),
    ],
)
def test_model_location_matches_the_host_not_the_url(base_url: str, expected: str) -> None:
    from observatory.entity_discovery import _model_location

    assert _model_location(base_url) == expected


def test_node_roles_require_an_exact_domain_match() -> None:
    from observatory.entity_discovery import _node_roles

    roles = _node_roles(
        {
            "node-role.kubernetes.io/control-plane": "",
            "node-role.kubernetes.io/worker": "",
            # Neither of these is a real role key.
            "not-node-role.kubernetes.io/spoofed": "",
            "node-role.kubernetes.io": "",
        }
    )

    assert roles == ["control-plane", "worker"]


# ── What production actually returns ─────────────────────────────────────────
# Bifröst's catalogue API exposes no provider base URLs — every provider came
# back with base_url "" — so keying the routes_to edge off it produced none at
# all, and every model read "unknown" location.


@pytest.mark.asyncio
async def test_models_are_linked_to_their_provider_without_a_base_url() -> None:
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "nemotron", "name": "Nemotron"},
                    {"id": "opus", "name": "Opus"},
                ],
                "/api/v1/bifrost/providers": [
                    {"key": "local", "vendor": "nvidia", "base_url": "", "model_ids": ["nemotron"]},
                    {
                        "key": "anthropic",
                        "vendor": "anthropic",
                        "base_url": "",
                        "model_ids": ["opus"],
                    },
                ],
            }
        ),
    )

    result = await adapter.discover()

    assert len(result.edges) == 2
    assert {e["relationType"] for e in result.edges} == {"routes_to"}
    assert result.edges[0]["evidence"]["field"] == "model_ids"


@pytest.mark.asyncio
async def test_self_hosted_and_vendor_providers_are_told_apart_without_urls() -> None:
    """`local` means our own silicon; a named vendor means traffic leaves."""
    adapter = BifrostCatalogDiscoveryAdapter(
        base_url="http://bifrost.test",
        cluster="ymir",
        transport=_routed(
            {
                "/api/v1/bifrost/models": [
                    {"id": "nemotron", "name": "Nemotron"},
                    {"id": "opus", "name": "Opus"},
                ],
                "/api/v1/bifrost/providers": [
                    {"key": "local", "base_url": "", "model_ids": ["nemotron"]},
                    {"key": "anthropic", "base_url": "", "model_ids": ["opus"]},
                ],
            }
        ),
    )

    result = await adapter.discover()

    loc = {
        e.metadata["modelId"]: e.metadata["location"] for e in result.entities if e.kind == "model"
    }
    assert loc == {"nemotron": "internal", "opus": "external"}


def test_a_url_still_wins_over_provider_identity() -> None:
    from observatory.entity_discovery import _model_location

    # An explicit internal host beats a vendor-sounding key, and vice versa.
    assert (
        _model_location("http://vllm.volundr.svc.cluster.local", {"key": "anthropic"}) == "internal"
    )
    assert _model_location("https://api.anthropic.com", {"key": "local"}) == "external"
    assert _model_location("", None) == "unknown"


@pytest.mark.asyncio
async def test_a_timeout_names_the_fault_instead_of_only_the_url() -> None:
    """httpx.ReadTimeout stringifies to "", which produced a warning body of
    just the URL — it said nothing, twice."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("")

    adapter = MimirDiscoveryAdapter(
        base_url="http://mimir.test",
        cluster="ymir",
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert "ReadTimeout" in result.events[0]["body"]


def _pod_on(node_name: str) -> dict:
    return {"metadata": {"name": f"pod-{node_name}"}, "spec": {"nodeName": node_name}}


@pytest.mark.asyncio
async def test_a_host_carries_how_many_pods_it_runs(tmp_path, monkeypatch) -> None:
    """The census is unselected: a cluster's load is every pod, not only ours."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pods":
            # No label selector — counting only our own workloads would report
            # a fraction of the cluster and present it as the whole.
            assert "labelSelector" not in request.url.params
            return httpx.Response(
                200,
                json={"items": [_pod_on("spark-1"), _pod_on("spark-1"), _pod_on("other")]},
            )
        return httpx.Response(200, json={"items": [_node_item()]})

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert result.entities[0].metadata["pods"] == 2


@pytest.mark.asyncio
async def test_a_host_carries_no_pod_count_when_the_census_is_refused(tmp_path, monkeypatch):
    """403 means "not allowed to look", which is not the same as zero pods."""
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/pods":
            return httpx.Response(403, json={"message": "forbidden"})
        return httpx.Response(200, json={"items": [_node_item()]})

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    result = await adapter.discover()

    assert "pods" not in result.entities[0].metadata
    assert any("Forbidden listing pods" in str(event) for event in result.events)


@pytest.mark.asyncio
async def test_the_pod_census_is_skipped_when_no_host_was_discovered(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.test")
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"items": []})

    adapter = KubernetesDiscoveryAdapter(
        cluster="ymir",
        include_kinds=["nodes"],
        service_account_root=_service_account(tmp_path),
        transport=httpx.MockTransport(handler),
    )

    await adapter.discover()

    assert "/api/v1/pods" not in seen


@pytest.mark.asyncio
async def test_adapters_run_concurrently_not_end_to_end() -> None:
    """A fragment should take the slowest adapter, not the sum of them.

    Ymir runs five adapters, three of which call out to Bifrost, Ravn and
    Ting. Sequentially that came to ~8.5s and pushed the fragment past the
    Guild's timeout, which is why the richest source was the one that kept
    dropping out of the estate.
    """

    class _SlowAdapter:
        def __init__(self, entity_id: str) -> None:
            self.entity_id = entity_id

        async def discover(self) -> DiscoveryResult:
            await asyncio.sleep(0.15)
            return DiscoveryResult(
                entities=[DiscoveredEntity(id=self.entity_id, kind="service", name=self.entity_id)]
            )

    adapters = [_SlowAdapter(f"svc-{i}") for i in range(5)]
    composite = CompositeDiscoveryAdapter(adapters)  # type: ignore[arg-type]

    started = time.perf_counter()
    result = await composite.discover()
    elapsed = time.perf_counter() - started

    assert len(result.entities) == 5
    # Five 0.15s adapters: ~0.15s concurrently, ~0.75s end to end.
    assert elapsed < 0.5
