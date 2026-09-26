"""Tests for shared instance registry REST endpoints."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from identity.adapters.authorization import AllowAllAuthorizationAdapter
from identity.adapters.identity import AllowAllIdentityAdapter
from niuu.adapters.inbound.rest_instances import create_instances_router
from niuu.config import InstanceRegistryConfig
from niuu.domain.agent_directory import AgentDirectoryEntry, AgentDirectoryPage
from niuu.domain.models import (
    InstanceHealthStatus,
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.instance_health import InstanceHealthCheckResult
from niuu.domain.services.instances import (
    InstanceAccessError,
    InstanceTransportSecurityError,
    InstanceValidationError,
)
from niuu.ports.instance_probe import InstanceProbeResult


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


class StubHealthChecker:
    """Duck-typed stand-in for InstanceHealthChecker: no real network calls."""

    def __init__(
        self,
        *,
        ok: bool = True,
        message: str = "reachable",
        crash: Exception | None = None,
    ) -> None:
        self.ok = ok
        self.message = message
        self.crash = crash
        self.checked_instance_ids: list[str] = []

    async def check_instance(self, instance: RegisteredInstance) -> InstanceHealthCheckResult:
        self.checked_instance_ids.append(instance.id)
        if self.crash is not None:
            raise self.crash
        checked_at = datetime.now(UTC)
        health = InstanceHealthStatus.OK if self.ok else InstanceHealthStatus.UNREACHABLE
        return InstanceHealthCheckResult(
            probe=InstanceProbeResult(ok=self.ok, status_code=200, message=self.message),
            health=health,
            checked_at=checked_at,
            last_seen_at=checked_at if self.ok else instance.last_seen_at,
            last_error=None if self.ok else self.message,
        )


def _client(
    service: StubInstanceService,
    *,
    catalog: list[Any] | None = None,
    agent_directory: StubAgentDirectoryAggregation | None = None,
    health_checker: StubHealthChecker | None = None,
) -> TestClient:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    if catalog is not None:
        app.state.settings = SimpleNamespace(
            niuu=SimpleNamespace(catalog=catalog),
        )
    app.include_router(
        create_instances_router(  # type: ignore[arg-type]
            service,
            health_checker=health_checker or StubHealthChecker(),  # type: ignore[arg-type]
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


def test_default_instance_catalog_labels_ting() -> None:
    client = _client(StubInstanceService(), catalog=InstanceRegistryConfig().catalog)

    response = client.get("/api/v1/niuu/instances/catalog")

    assert response.status_code == 200
    entry = next(item for item in response.json() if item["kind"] == "ting")
    assert entry["label"] == "Ting"
    assert entry["registerable"] is True
    assert entry["filterable"] is True


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
    assert response.json()["health"] == "ok"
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


def test_create_instance_maps_transport_security_error_to_422() -> None:
    """An http:// remote instance without config.allow_plaintext is a 422
    with the remedy in the message — not the generic 403 used for
    authorization/validation errors — so the CLI/web can tell "malformed
    request" apart from "not permitted"."""
    service = StubInstanceService()
    service.create_result = InstanceTransportSecurityError(
        "http://insecure.example.com must use https:// for a remote Guild "
        "instance; set config.allow_plaintext: true only when the network "
        "path is already encrypted or otherwise trusted"
    )
    client = _client(service)

    response = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "slug": "insecure",
            "name": "Insecure",
            "baseUrl": "http://insecure.example.com",
        },
    )

    assert response.status_code == 422
    assert "allow_plaintext" in response.json()["detail"]


def test_update_instance_maps_transport_security_error_to_422() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("existing")
    service.update_result = InstanceTransportSecurityError(
        "http://insecure.example.com must use https:// for a remote Guild instance"
    )
    client = _client(service)

    response = client.patch(
        "/api/v1/niuu/instances/existing",
        headers=_headers(),
        json={"baseUrl": "http://insecure.example.com"},
    )

    assert response.status_code == 422


def test_create_instance_registers_even_when_the_initial_probe_fails() -> None:
    """Registering an instance that happens to be offline (e.g. a Spark not
    yet powered on) is a legitimate operator action — a failed register-time
    probe must not reject the registration. It must, however, be recorded
    as unreachable immediately rather than defaulting to a healthy-looking
    state (see .claude/rules/no-fallbacks.md)."""
    service = StubInstanceService()
    service.create_result = _instance("created", visibility=InstanceVisibility.USER, owner_id="u")
    checker = StubHealthChecker(ok=False, message="Connection refused")
    client = _client(service, health_checker=checker)

    response = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "slug": "created",
            "name": "Created",
            "baseUrl": "https://created.example.com",
            "visibility": "user",
            "ownerId": "u",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["health"] == "unreachable"
    assert body["lastError"] == "Connection refused"
    # A never-reachable instance was still checked (lastCheckedAt is set),
    # but never seen (lastSeenAt stays absent) — the two must not conflate.
    assert body["lastCheckedAt"] is not None
    assert body["lastSeenAt"] is None
    assert checker.checked_instance_ids == ["created"]


def test_create_instance_never_500s_when_the_post_save_health_check_crashes() -> None:
    """The instance registration already succeeded and is durably saved by
    the time the immediate follow-up health check runs — a crash there (e.g.
    a database hiccup recording health) must not turn a successful create
    into a 500. The periodic loop picks it up on the next sweep."""
    service = StubInstanceService()
    created = _instance("created", visibility=InstanceVisibility.USER, owner_id="u")
    service.create_result = created
    checker = StubHealthChecker(crash=RuntimeError("health db unavailable"))
    client = _client(service, health_checker=checker)

    response = client.post(
        "/api/v1/niuu/instances",
        headers=_headers(),
        json={
            "slug": "created",
            "name": "Created",
            "baseUrl": "https://created.example.com",
            "visibility": "user",
            "ownerId": "u",
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == "created"
    assert checker.checked_instance_ids == ["created"]


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


def test_update_instance_reprobes_when_base_url_changes() -> None:
    """A metadata edit no longer touches the persisted health row (ON
    CONFLICT excludes it — record_health owns it), so after re-pointing an
    instance at a different endpoint the old health data describes a target
    that no longer applies. Re-probe immediately instead of leaving it stale
    for up to a full niuu.health.interval_seconds."""
    service = StubInstanceService()
    service.single_instance = _instance("updated", base_url="https://old.example.com")
    service.update_result = _instance("updated", base_url="https://new.example.com")
    checker = StubHealthChecker(ok=False, message="Connection refused")
    client = _client(service, health_checker=checker)

    response = client.patch(
        "/api/v1/niuu/instances/updated",
        headers=_headers(),
        json={"baseUrl": "https://new.example.com"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["health"] == "unreachable"
    assert body["lastError"] == "Connection refused"
    assert checker.checked_instance_ids == ["updated"]


def test_update_instance_does_not_reprobe_when_the_endpoint_is_unchanged() -> None:
    service = StubInstanceService()
    service.single_instance = _instance("updated", base_url="https://same.example.com")
    service.update_result = _instance("updated", base_url="https://same.example.com")
    checker = StubHealthChecker(ok=True)
    client = _client(service, health_checker=checker)

    response = client.patch(
        "/api/v1/niuu/instances/updated",
        headers=_headers(),
        json={"name": "Renamed only"},
    )

    assert response.status_code == 200
    assert checker.checked_instance_ids == []


def test_delete_instance_maps_access_error() -> None:
    service = StubInstanceService()
    client = _client(service)

    response = client.delete("/api/v1/niuu/instances/instance-1", headers=_headers())

    assert response.status_code == 204
    assert service.delete_calls == ["instance-1"]

    service.delete_error = InstanceAccessError("blocked")
    forbidden = client.delete("/api/v1/niuu/instances/instance-2", headers=_headers())
    assert forbidden.status_code == 403


def test_test_instance_delegates_to_the_health_checker_and_returns_404_when_missing() -> None:
    """The endpoint no longer probes directly — it delegates to (and persists
    through) the shared InstanceHealthChecker, the same one the periodic loop
    and register-time check use. Probing itself is covered by
    test_http_instance_probe.py."""
    service = StubInstanceService()
    instance = _instance("instance-1", base_url="https://health.example.com")
    service.single_instance = instance
    checker = StubHealthChecker(ok=True, message="Instance instance-1 is reachable")
    client = _client(service, health_checker=checker)

    response = client.post("/api/v1/niuu/instances/instance-1/test", headers=_headers())

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "statusCode": 200,
        "message": "Instance instance-1 is reachable",
    }
    assert checker.checked_instance_ids == ["instance-1"]

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
    # A remote Guild instance never sees a client-supplied x-auth-* header.
    assert "x-auth-tenant" not in route.calls.last.request.headers
    assert "x-auth-user-id" not in route.calls.last.request.headers

    respx.get("https://volundr.example.com/api/v1/forge/sessions").mock(
        side_effect=RuntimeError("boom")
    )
    failed = client.get("/api/v1/niuu/instances/instance-1/sessions", headers=_headers())
    assert failed.status_code == 502


def test_list_instance_sessions_returns_502_on_a_tls_pin_mismatch(monkeypatch) -> None:
    """_load_remote_sessions's own guild_transport enforcement
    (build_guild_httpx_client raising GuildTransportError) maps to a 502 —
    the upstream is never contacted at all when the pin fails."""
    monkeypatch.setattr(
        "niuu.adapters.outbound.guild_transport.fetch_leaf_certificate_der",
        lambda *a, **k: b"not the pinned certificate",
    )
    pinned_fingerprint = hashlib.sha256(b"the actual expected certificate").hexdigest()
    service = StubInstanceService()
    service.single_instance = _instance(
        "instance-1",
        base_url="https://volundr.example.com",
        config={"tls_fingerprint": pinned_fingerprint},
    )
    client = _client(service)

    response = client.get(
        "/api/v1/niuu/instances/instance-1/sessions",
        headers=_headers(),
    )

    assert response.status_code == 502
    assert "does not match" in response.json()["detail"]


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


# ── Topology fragment push inbox ─────────────────────────────────────────────

#: Scope enforcement reads claims without verifying the signature (Envoy
#: verifies upstream), so this key only needs to satisfy PyJWT's minimum.
_TEST_SIGNING_KEY = "test-signing-key-of-sufficient-length-for-hs256"


def _inbox_client(inbox: Any) -> TestClient:
    app = FastAPI()
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_instances_router(  # type: ignore[arg-type]
            StubInstanceService(),
            health_checker=StubHealthChecker(),  # type: ignore[arg-type]
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
        authorization=AllowAllAuthorizationAdapter(),
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

    assert (
        len(
            asyncio.run(
                inbox.current(principal=Principal("publisher", "", "tenant", ["volundr:developer"]))
            )
        )
        == 1
    )


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
    app.state.identity = AllowAllIdentityAdapter(user_repository=AsyncMock())
    app.include_router(
        create_instances_router(  # type: ignore[arg-type]
            StubInstanceService(),
            health_checker=StubHealthChecker(),  # type: ignore[arg-type]
        )
    )
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
    assert (
        asyncio.run(
            inbox.current(principal=Principal("publisher", "", "tenant", ["volundr:developer"]))
        )
        == []
    )


def test_forgetting_an_unknown_source_is_a_404() -> None:
    client = _inbox_client(_push_inbox())

    response = client.delete(
        "/api/v1/niuu/observatory/fragments/never-seen",
        headers=_headers(),
    )

    assert response.status_code == 404
