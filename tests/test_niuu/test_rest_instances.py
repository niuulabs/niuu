"""Tests for shared instance registry REST endpoints."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.domain.agent_directory import AgentDirectoryEntry, AgentDirectoryPage
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceValidationError,
)


def _instance(
    instance_id: str,
    *,
    kind: InstanceKind = InstanceKind.VOLUNDR,
    visibility: InstanceVisibility = InstanceVisibility.TENANT,
    owner_id: str | None = None,
    tenant_id: str | None = "tenant-a",
    enabled: bool = True,
    is_default: bool = False,
    base_url: str = "https://registry.example.com",
    config: dict[str, Any] | None = None,
    tags: list[str] | None = None,
) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=kind,
        slug=f"{instance_id}-slug",
        name=f"Instance {instance_id}",
        base_url=base_url,
        visibility=visibility,
        owner_id=owner_id,
        tenant_id=tenant_id,
        enabled=enabled,
        is_default=is_default,
        config=config if config is not None else {"region": "ca-central-1"},
        created_at=now,
        updated_at=now,
        tags=list(tags or []),
    )


class StubInstanceService:
    def __init__(self) -> None:
        self.visible_instances: list[RegisteredInstance] = []
        self.single_instance: RegisteredInstance | None = None
        self.create_result: RegisteredInstance | Exception | None = None
        self.update_result: RegisteredInstance | Exception | None = None
        self.delete_error: Exception | None = None
        self.list_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.delete_calls: list[str] = []
        self.get_calls: list[str] = []

    async def list_visible(
        self,
        principal: Principal,
        *,
        kind: InstanceKind | None = None,
        enabled_only: bool = False,
    ) -> list[RegisteredInstance]:
        self.list_calls.append(
            {
                "principal": principal,
                "kind": kind,
                "enabled_only": enabled_only,
            }
        )
        return list(self.visible_instances)

    async def create_instance(self, principal: Principal, **kwargs: Any) -> RegisteredInstance:
        self.create_calls.append({"principal": principal, **kwargs})
        if isinstance(self.create_result, Exception):
            raise self.create_result
        assert self.create_result is not None
        return self.create_result

    async def update_instance(
        self,
        principal: Principal,
        instance_id: str,
        **kwargs: Any,
    ) -> RegisteredInstance:
        self.update_calls.append(
            {
                "principal": principal,
                "instance_id": instance_id,
                **kwargs,
            }
        )
        if isinstance(self.update_result, Exception):
            raise self.update_result
        assert self.update_result is not None
        return self.update_result

    async def delete_instance(self, principal: Principal, instance_id: str) -> None:
        self.delete_calls.append(instance_id)
        if self.delete_error is not None:
            raise self.delete_error

    async def get_visible(
        self,
        principal: Principal,
        instance_id: str,
    ) -> RegisteredInstance | None:
        self.get_calls.append(instance_id)
        return self.single_instance


def _agent_entry() -> AgentDirectoryEntry:
    return AgentDirectoryEntry(
        id="agent-aggregate-1",
        canonicalId="source:observatory-a:session-a",
        sourceAgentId="session-a",
        sourceInstanceId="observatory-a",
        clusterId="noatun",
        environmentId="environment-a",
        topologyNodeId="runtime:noatun:skuld:skuld:session-a",
        name="Builder",
        description="Builds software",
        kind="workflow-session",
        cardUrl="https://agents.example.test/.well-known/agent-card.json",
        cardVersion="1.0.0",
        cardHash="card-hash",
        skillIds=["code"],
        tags=["engineering"],
        observedStatus="healthy",
        ownerId="user-a",
        tenantId="tenant-a",
        visibility="user",
    )


class StubAgentDirectoryAggregation:
    def __init__(self) -> None:
        self.entry = _agent_entry()
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    async def list_agents(self, instances, principal, *, headers, filters):
        self.list_calls.append(
            {
                "instances": instances,
                "principal": principal,
                "headers": headers,
                "filters": filters,
            }
        )
        return AgentDirectoryPage(items=[self.entry], revision="aggregate-revision")

    async def get_agent(self, agent_id, instances, principal, *, headers):
        self.get_calls.append(
            {
                "agent_id": agent_id,
                "instances": instances,
                "principal": principal,
                "headers": headers,
            }
        )
        return self.entry if agent_id == self.entry.id else None


def _client(
    service: StubInstanceService,
    *,
    catalog: list[Any] | None = None,
    agent_directory: StubAgentDirectoryAggregation | None = None,
) -> TestClient:
    app = FastAPI()
    if catalog is not None:
        app.state.settings = SimpleNamespace(
            niuu=SimpleNamespace(catalog=catalog),
        )
    app.include_router(
        create_instances_router(  # type: ignore[arg-type]
            service,
            agent_directory=agent_directory,  # type: ignore[arg-type]
        )
    )
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {
        "authorization": "Bearer test-token",
        "x-auth-user-id": "user-a",
        "x-auth-email": "user-a@example.com",
        "x-auth-tenant": "tenant-a",
        "x-auth-roles": "member",
    }


def test_list_instances_serializes_aliases_and_forwards_filters() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance("volundr-1", is_default=True),
        _instance("ting-1", kind=InstanceKind.TING, enabled=False),
    ]
    client = _client(service)

    response = client.get(
        "/api/v1/niuu/instances?kind=ting&enabledOnly=true",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()[0]["baseUrl"] == "https://registry.example.com"
    assert response.json()[0]["isDefault"] is True
    assert service.list_calls[-1]["kind"] == InstanceKind.TING
    assert service.list_calls[-1]["enabled_only"] is True


def test_aggregate_agent_directory_uses_visible_observatories_and_forwards_filters() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance("observatory-a", kind=InstanceKind.OBSERVATORY),
    ]
    directory = StubAgentDirectoryAggregation()
    client = _client(service, agent_directory=directory)

    response = client.get(
        "/api/v1/niuu/observatory/agents"
        "?skill=code&tag=engineering&kind=workflow-session&status=healthy"
        "&environmentId=environment-a&cluster=noatun&instance=observatory-a",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["topologyNodeId"].endswith("session-a")
    assert service.list_calls[-1]["kind"] == InstanceKind.OBSERVATORY
    assert service.list_calls[-1]["enabled_only"] is True
    call = directory.list_calls[-1]
    assert call["principal"].user_id == "user-a"
    assert call["headers"]["authorization"] == "Bearer test-token"
    assert call["filters"].skills == ("code",)
    assert call["filters"].tags == ("engineering",)
    assert call["filters"].kinds == ("workflow-session",)
    assert call["filters"].statuses == ("healthy",)
    assert call["filters"].environment_ids == ("environment-a",)
    assert call["filters"].cluster_ids == ("noatun",)
    assert call["filters"].instance_ids == ("observatory-a",)


def test_aggregate_agent_detail_returns_generic_404() -> None:
    service = StubInstanceService()
    service.visible_instances = [_instance("observatory-a", kind=InstanceKind.OBSERVATORY)]
    directory = StubAgentDirectoryAggregation()
    client = _client(service, agent_directory=directory)

    found = client.get(
        "/api/v1/niuu/observatory/agents/agent-aggregate-1",
        headers=_headers(),
    )
    missing = client.get(
        "/api/v1/niuu/observatory/agents/inaccessible",
        headers=_headers(),
    )

    assert found.status_code == 200
    assert found.json()["sourceInstanceId"] == "observatory-a"
    assert missing.status_code == 404
    assert missing.json() == {"detail": "Agent not found"}


def test_get_instance_catalog_reads_catalog_from_settings() -> None:
    client = _client(
        StubInstanceService(),
        catalog=[
            SimpleNamespace(
                kind=InstanceKind.VOLUNDR,
                label="Volundr",
                rune="ᚲ",
                summary="sessions",
                detail="spawns remote pods",
                registerable=True,
                filterable=True,
            ),
            SimpleNamespace(
                kind=InstanceKind.OBSERVATORY,
                label="",
                rune="ᛞ",
                summary="telemetry",
                detail="shared control plane",
                registerable=False,
                filterable=True,
            ),
        ],
    )

    response = client.get("/api/v1/niuu/instances/catalog")

    assert response.status_code == 200
    assert response.json() == [
        {
            "kind": "volundr",
            "label": "Volundr",
            "rune": "ᚲ",
            "summary": "sessions",
            "detail": "spawns remote pods",
            "registerable": True,
            "filterable": True,
        },
        {
            "kind": "observatory",
            "label": "Observatory",
            "rune": "ᛞ",
            "summary": "telemetry",
            "detail": "shared control plane",
            "registerable": False,
            "filterable": True,
        },
    ]


def test_create_instance_passes_payload_to_service_and_maps_access_errors() -> None:
    service = StubInstanceService()
    service.create_result = _instance(
        "created",
        visibility=InstanceVisibility.USER,
        owner_id="user-a",
    )
    client = _client(service)

    response = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "kind": "volundr",
            "slug": "created",
            "name": "Created",
            "baseUrl": "https://created.example.com",
            "visibility": "user",
            "isDefault": True,
            "ownerId": "user-a",
            "config": {"region": "ca-central-1"},
        },
    )

    assert response.status_code == 201
    assert service.create_calls[-1]["kind"] == InstanceKind.VOLUNDR
    assert service.create_calls[-1]["visibility"] == InstanceVisibility.USER
    assert service.create_calls[-1]["is_default"] is True
    assert service.create_calls[-1]["owner_id"] == "user-a"

    service.create_result = InstanceValidationError("forbidden")
    forbidden = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "slug": "bad",
            "name": "Bad",
            "baseUrl": "https://bad.example.com",
        },
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"] == "forbidden"


def test_update_instance_maps_lookup_and_access_errors() -> None:
    service = StubInstanceService()
    service.update_result = _instance(
        "updated",
        visibility=InstanceVisibility.SYSTEM,
        tenant_id=None,
    )
    client = _client(service)

    response = client.patch(
        "/api/v1/niuu/instances/updated",
        headers=_headers(),
        json={"visibility": "system", "baseUrl": "https://updated.example.com"},
    )

    assert response.status_code == 200
    assert service.update_calls[-1]["visibility"] == InstanceVisibility.SYSTEM
    assert service.update_calls[-1]["base_url"] == "https://updated.example.com"

    service.update_result = LookupError("missing")
    missing = client.patch("/api/v1/niuu/instances/missing", headers=_headers(), json={"name": "x"})
    assert missing.status_code == 404

    service.update_result = InstanceAccessError("nope")
    forbidden = client.patch(
        "/api/v1/niuu/instances/blocked",
        headers=_headers(),
        json={"name": "x"},
    )
    assert forbidden.status_code == 403


def test_delete_instance_maps_access_error() -> None:
    service = StubInstanceService()
    client = _client(service)

    response = client.delete("/api/v1/niuu/instances/instance-1", headers=_headers())

    assert response.status_code == 204
    assert service.delete_calls == ["instance-1"]

    service.delete_error = InstanceAccessError("blocked")
    forbidden = client.delete("/api/v1/niuu/instances/instance-2", headers=_headers())
    assert forbidden.status_code == 403


@respx.mock
def test_test_instance_probes_health_and_returns_404_when_missing() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("instance-1", base_url="https://health.example.com")
    client = _client(service)
    route = respx.get("https://health.example.com/health").mock(return_value=Response(200))

    response = client.post("/api/v1/niuu/instances/instance-1/test", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "statusCode": 200,
        "message": "Instance instance-1 is reachable",
    }
    assert route.called

    service.single_instance = None
    missing = client.post("/api/v1/niuu/instances/missing/test", headers=_headers())
    assert missing.status_code == 404


@respx.mock
def test_list_instance_sessions_forwards_headers_and_status_filter() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("instance-1", base_url="https://volundr.example.com")
    client = _client(service)
    route = respx.get("https://volundr.example.com/api/v1/forge/sessions?status=running").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "sess-1",
                    "name": "Session 1",
                    "status": "running",
                    "model": "gpt-5",
                    "owner_id": "user-a",
                    "tenant_id": "tenant-a",
                    "archived_at": None,
                }
            ],
        )
    )

    response = client.get(
        "/api/v1/niuu/instances/instance-1/sessions?status=running",
        headers=_headers(),
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "sess-1",
            "name": "Session 1",
            "status": "running",
            "model": "gpt-5",
            "ownerId": "user-a",
            "tenantId": "tenant-a",
            "archivedAt": None,
        }
    ]
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert route.calls.last.request.headers["x-auth-tenant"] == "tenant-a"

    respx.get("https://volundr.example.com/api/v1/forge/sessions").mock(
        side_effect=RuntimeError("boom")
    )
    failed = client.get("/api/v1/niuu/instances/instance-1/sessions", headers=_headers())
    assert failed.status_code == 502


def test_list_instance_sessions_rejects_invalid_remote_base_urls() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("instance-1", base_url="ftp://volundr.example.com")
    client = _client(service)

    response = client.get("/api/v1/niuu/instances/instance-1/sessions", headers=_headers())

    assert response.status_code == 502
    assert "http or https" in response.json()["detail"]


def test_list_volundr_targets_requests_enabled_visible_volundr_instances() -> None:
    service = StubInstanceService()
    service.visible_instances = [_instance("volundr-1", enabled=True)]
    client = _client(service)

    response = client.get("/api/v1/niuu/targets/volundr", headers=_headers())

    assert response.status_code == 200
    assert response.json()[0]["id"] == "volundr-1"
    assert service.list_calls[-1]["kind"] == InstanceKind.VOLUNDR
    assert service.list_calls[-1]["enabled_only"] is True


@respx.mock
def test_observatory_snapshot_builds_registry_backed_topology() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance(
            "volundr-1",
            kind=InstanceKind.VOLUNDR,
            base_url="https://volundr.example.com",
            is_default=True,
        ),
        _instance(
            "bifrost-1",
            kind=InstanceKind.BIFROST,
            base_url="https://bifrost.example.com",
        ),
        _instance(
            "mimir-1",
            kind=InstanceKind.MIMIR,
            base_url="https://mimir.example.com",
        ),
    ]
    client = _client(service)
    respx.get("https://volundr.example.com/health").mock(return_value=Response(200))
    respx.get("https://bifrost.example.com/health").mock(return_value=Response(503))
    respx.get("https://mimir.example.com/health").mock(return_value=Response(200))

    response = client.get("/api/v1/niuu/observatory/snapshot", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert payload["layoutHints"]["mode"] == "pack"
    assert any(node["id"] == "cluster-unknown" for node in payload["nodes"])
    assert not any(node["id"].startswith("realm-") for node in payload["nodes"])
    assert any(node.get("svcType") == "volundr" for node in payload["nodes"])
    assert any(node.get("svcType") == "bifrost" for node in payload["nodes"])
    assert not any(node["id"] == "mimir-well" for node in payload["nodes"])
    assert any(
        node.get("layoutHints", {}).get("packGroup") == "volundr" for node in payload["nodes"]
    )
    assert not any(node["id"].startswith("run-") for node in payload["nodes"])
    assert any(
        edge.get("relationType") == "uses"
        and edge.get("label") == "uses"
        and edge.get("evidence", {}).get("adapter") == "rest_instances"
        for edge in payload["edges"]
    )
    assert any(event["service"] == "volundr" for event in payload["events"])
    assert any(event["service"] == "bifrost" for event in payload["events"])
    assert any(event["service"] == "mimir" for event in payload["events"])


@respx.mock
def test_observatory_snapshot_includes_wardens_from_registered_ravn() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance(
            "ravn-1",
            kind=InstanceKind.RAVN,
            base_url="https://ravn.example.com/api/v1/ravn",
        ),
        _instance(
            "mimir-1",
            kind=InstanceKind.MIMIR,
            base_url="https://mimir.example.com",
        ),
    ]
    client = _client(service)
    respx.get("https://ravn.example.com/api/v1/ravn/health").mock(return_value=Response(200))
    respx.get("https://mimir.example.com/health").mock(return_value=Response(200))
    respx.get("https://ravn.example.com/api/v1/ravn/wardens").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "mimir-shared-warden",
                    "name": "Mimir Shared Warden",
                    "persona": "mimir-warden",
                    "deployment": "kubernetes",
                    "mimir": {
                        "mount_names": ["shared"],
                        "write_mount": "shared",
                    },
                    "schedules": {
                        "dream_cycle_cron_expression": "*/15 * * * *",
                    },
                    "runtime": {"state": "active"},
                    "supervisor": {
                        "observation": {
                            "status": "running",
                            "source": "kubernetes",
                        }
                    },
                }
            ],
        )
    )

    response = client.get("/api/v1/niuu/observatory/snapshot", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert any(node["id"] == "warden:mimir-shared-warden" for node in payload["nodes"])
    assert any(edge["id"] == "edge:warden:mimir-shared-warden:mimir" for edge in payload["edges"])


@respx.mock
def test_observatory_snapshot_uses_deployment_cluster_labels() -> None:
    service = StubInstanceService()
    service.visible_instances = [
        _instance(
            "ravn-1",
            kind=InstanceKind.RAVN,
            base_url="https://ravn.example.com/api/v1/ravn",
            config={
                "labels": {
                    "niuu.world/cluster": "ymir",
                    "niuu.world/namespace": "volundr",
                }
            },
        ),
        _instance(
            "mimir-1",
            kind=InstanceKind.MIMIR,
            base_url="https://mimir.example.com",
            config={"environment": "ymir", "namespace": "volundr"},
        ),
    ]
    client = _client(service)
    respx.get("https://ravn.example.com/api/v1/ravn/health").mock(return_value=Response(200))
    respx.get("https://mimir.example.com/health").mock(return_value=Response(200))
    respx.get("https://ravn.example.com/api/v1/ravn/wardens").mock(
        return_value=Response(
            200,
            json=[
                {
                    "id": "mimir-shared-warden",
                    "name": "Mimir Shared Warden",
                    "runtime": {"state": "active"},
                }
            ],
        )
    )

    response = client.get("/api/v1/niuu/observatory/snapshot", headers=_headers())

    assert response.status_code == 200
    payload = response.json()
    assert any(
        node["id"] == "cluster-ymir" and node["label"] == "ymir" and node["namespace"] == "volundr"
        for node in payload["nodes"]
    )
    assert any(
        node["id"] == "instance:ravn:ravn-1-slug"
        and node["parentId"] == "cluster-ymir"
        and node["clusterName"] == "ymir"
        and node["namespace"] == "volundr"
        for node in payload["nodes"]
    )
    assert any(
        node["id"] == "warden:mimir-shared-warden"
        and node["parentId"] == "cluster-ymir"
        and node["clusterName"] == "ymir"
        and node["namespace"] == "volundr"
        for node in payload["nodes"]
    )


# ── Topology fragment push inbox ─────────────────────────────────────────────

#: Scope enforcement reads claims without verifying the signature (Envoy
#: verifies upstream), so this key only needs to satisfy PyJWT's minimum.
_TEST_SIGNING_KEY = "test-signing-key-of-sufficient-length-for-hs256"


def _inbox_client(inbox: Any) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_instances_router(  # type: ignore[arg-type]
            StubInstanceService(),
            fragment_inbox=inbox,
        )
    )
    return TestClient(app)


def _push_inbox(ttl_seconds: float = 180.0) -> Any:
    from niuu.adapters.memory_observatory_fragments import (
        InMemoryObservatoryFragmentRepository,
    )
    from niuu.domain.services.observatory_fragments import ObservatoryFragmentInboxService

    return ObservatoryFragmentInboxService(
        InMemoryObservatoryFragmentRepository(),
        ttl_seconds=ttl_seconds,
    )


def _fragment_payload(source_id: str = "spark-1") -> dict[str, Any]:
    return {
        "nodes": [
            {
                "id": "ravn-ivaldi",
                "typeId": "ravn_long",
                "label": "ivaldi",
                "hostId": "saehrimnir",
                "realmId": "sparks",
            }
        ],
        "meta": {"sourceId": source_id, "sourceKind": "resident", "hostId": "saehrimnir"},
    }


def test_publishing_a_fragment_reports_the_sources_health() -> None:
    client = _inbox_client(_push_inbox())

    response = client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(),
        headers=_headers(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sourceId"] == "spark-1"
    assert body["status"] == "healthy"
    assert body["transport"] == "push"
    assert body["nodeCount"] == 1


def test_republishing_replaces_rather_than_accumulating() -> None:
    inbox = _push_inbox()
    client = _inbox_client(inbox)

    for _ in range(3):
        client.put(
            "/api/v1/niuu/observatory/fragments/spark-1",
            json=_fragment_payload(),
            headers=_headers(),
        )

    assert len(asyncio.run(inbox.current())) == 1


def test_a_fragment_cannot_claim_a_different_source_than_its_path() -> None:
    """Otherwise a source could overwrite, or masquerade as, another."""
    client = _inbox_client(_push_inbox())

    response = client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(source_id="spark-2"),
        headers=_headers(),
    )

    assert response.status_code == 400
    assert "spark-2" in response.json()["detail"]


def test_publishing_without_a_configured_inbox_says_so() -> None:
    app = FastAPI()
    app.include_router(create_instances_router(StubInstanceService()))  # type: ignore[arg-type]
    client = TestClient(app)

    response = client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(),
        headers=_headers(),
    )

    assert response.status_code == 503


def test_a_build_token_without_the_push_scope_is_refused() -> None:
    """Scoped credentials are fail-closed: a token that may launch workflows
    must not also be able to rewrite the topology."""
    import jwt

    token = jwt.encode(
        {"token_use": "valkyrie_build", "scopes": ["ting:workflow:launch"]},
        _TEST_SIGNING_KEY,
        algorithm="HS256",
    )
    client = _inbox_client(_push_inbox())

    response = client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(),
        headers={**_headers(), "authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert "observatory:topology:push" in response.json()["detail"]


def test_a_build_token_carrying_the_push_scope_is_admitted() -> None:
    import jwt

    token = jwt.encode(
        {"token_use": "valkyrie_build", "scopes": ["observatory:topology:push"]},
        _TEST_SIGNING_KEY,
        algorithm="HS256",
    )
    client = _inbox_client(_push_inbox())

    response = client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(),
        headers={**_headers(), "authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200


def test_forgetting_a_source_removes_it() -> None:
    inbox = _push_inbox()
    client = _inbox_client(inbox)
    client.put(
        "/api/v1/niuu/observatory/fragments/spark-1",
        json=_fragment_payload(),
        headers=_headers(),
    )

    response = client.delete(
        "/api/v1/niuu/observatory/fragments/spark-1",
        headers=_headers(),
    )

    assert response.status_code == 204
    assert asyncio.run(inbox.current()) == []


def test_forgetting_an_unknown_source_is_a_404() -> None:
    client = _inbox_client(_push_inbox())

    response = client.delete(
        "/api/v1/niuu/observatory/fragments/never-seen",
        headers=_headers(),
    )

    assert response.status_code == 404
