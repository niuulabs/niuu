"""Tests for Guild's multi-instance Agent Directory aggregation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from niuu.adapters.outbound.http_agent_directory import HttpAgentDirectoryClient
from niuu.domain.agent_directory import (
    AgentDirectoryEntry,
    AgentDirectoryFilters,
    AgentDirectoryPage,
    AgentDirectorySourceHealth,
    AgentInterface,
    AgentProvenance,
)
from niuu.domain.models import (
    InstanceKind,
    InstanceVisibility,
    Principal,
    RegisteredInstance,
)
from niuu.domain.services.agent_directory import AgentDirectoryAggregationService


def _instance(instance_id: str, cluster: str) -> RegisteredInstance:
    now = datetime.now(UTC)
    return RegisteredInstance(
        id=instance_id,
        kind=InstanceKind.OBSERVATORY,
        slug=instance_id,
        name=instance_id,
        base_url=f"https://{instance_id}.example.test/api/v1/observatory",
        visibility=InstanceVisibility.TENANT,
        owner_id=None,
        tenant_id="tenant-a",
        enabled=True,
        is_default=False,
        config={"cluster": cluster},
        created_at=now,
        updated_at=now,
    )


def _entry(
    source_instance_id: str,
    source_agent_id: str,
    *,
    name: str = "Builder",
    owner_id: str = "user-a",
    tenant_id: str = "tenant-a",
    card_hash: str = "card-hash",
    signature_verified: bool | None = None,
    signature_key_fingerprints: tuple[str, ...] = ("signer-fingerprint",),
) -> AgentDirectoryEntry:
    topology_node_id = f"runtime:{source_instance_id}:skuld:{source_agent_id}"
    provenance = AgentProvenance(
        sourceAgentId=source_agent_id,
        sourceInstanceId=source_instance_id,
        clusterId="local-cluster",
        topologyNodeId=topology_node_id,
    )
    return AgentDirectoryEntry(
        id=f"local-{source_agent_id}",
        canonicalId=f"local:{source_instance_id}:{source_agent_id}",
        sourceAgentId=source_agent_id,
        sourceInstanceId=source_instance_id,
        clusterId="local-cluster",
        environmentId="environment-a",
        topologyNodeId=topology_node_id,
        name=name,
        description="Builds software",
        kind="workflow-session",
        cardUrl=f"https://{source_instance_id}.example.test/cards/{source_agent_id}",
        cardVersion="1.0.0",
        cardHash=card_hash,
        signatureVerified=signature_verified,
        signatureKeyFingerprints=(list(signature_key_fingerprints) if signature_verified else []),
        skillIds=["code"],
        tags=["engineering"],
        defaultInputModes=["text/plain"],
        defaultOutputModes=["application/json"],
        supportedInterfaces=[
            AgentInterface(
                url="https://agent.example.test/a2a",
                protocolBinding="JSONRPC",
                protocolVersion="1.0",
            )
        ],
        observedStatus="healthy",
        ownerId=owner_id,
        tenantId=tenant_id,
        visibility="user",
        provenance=[provenance],
    )


class _StubClient:
    def __init__(self, results: dict[str, AgentDirectoryPage | Exception]) -> None:
        self.results = results
        self.calls: list[tuple[str, dict[str, str], AgentDirectoryFilters]] = []
        self.active = 0
        self.max_active = 0

    async def list_agents(
        self,
        instance: RegisteredInstance,
        *,
        headers: dict[str, str],
        filters: AgentDirectoryFilters,
    ) -> AgentDirectoryPage:
        self.calls.append((instance.id, dict(headers), filters))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        result = self.results[instance.id]
        if isinstance(result, Exception):
            raise result
        return result


def _page(instance_id: str, *entries: AgentDirectoryEntry) -> AgentDirectoryPage:
    return AgentDirectoryPage(
        items=list(entries),
        sources=[
            AgentDirectorySourceHealth(
                instanceId=instance_id,
                clusterId="local-cluster",
                status="healthy",
                revision=f"revision-{instance_id}",
            )
        ],
        revision=f"revision-{instance_id}",
    )


def _principal(user_id: str = "user-a", tenant_id: str = "tenant-a") -> Principal:
    return Principal(user_id=user_id, email="", tenant_id=tenant_id, roles=["member"])


@pytest.mark.asyncio
async def test_aggregate_preserves_colliding_names_and_registry_provenance() -> None:
    instances = [_instance("observatory-a", "cluster-a"), _instance("observatory-b", "cluster-b")]
    client = _StubClient(
        {
            "observatory-a": _page(
                "local-a",
                _entry("local-a", "agent-a", name="Same Name", card_hash="same"),
            ),
            "observatory-b": _page(
                "local-b",
                _entry("local-b", "agent-b", name="Same Name", card_hash="same"),
            ),
        }
    )
    service = AgentDirectoryAggregationService(client=client, max_concurrency=1)  # type: ignore[arg-type]

    page = await service.list_agents(
        instances,
        _principal(),
        headers={"authorization": "Bearer caller"},
    )

    assert len(page.items) == 2
    assert {entry.source_instance_id for entry in page.items} == {
        "observatory-a",
        "observatory-b",
    }
    assert len({entry.id for entry in page.items}) == 2
    assert client.max_active == 1
    assert all(call[1]["authorization"] == "Bearer caller" for call in client.calls)


@pytest.mark.asyncio
async def test_aggregate_reconciles_only_verified_card_identity() -> None:
    instances = [_instance("observatory-a", "cluster-a"), _instance("observatory-b", "cluster-b")]
    client = _StubClient(
        {
            "observatory-a": _page(
                "local-a",
                _entry(
                    "local-a",
                    "agent-a",
                    card_hash="verified-hash",
                    signature_verified=True,
                ),
            ),
            "observatory-b": _page(
                "local-b",
                _entry(
                    "local-b",
                    "agent-b",
                    card_hash="verified-hash",
                    signature_verified=True,
                ),
            ),
        }
    )
    service = AgentDirectoryAggregationService(client=client, max_concurrency=2)  # type: ignore[arg-type]

    page = await service.list_agents(instances, _principal(), headers={})

    assert len(page.items) == 1
    assert page.items[0].canonical_id == "signed:verified-hash:keys:signer-fingerprint"
    assert {
        (source.source_instance_id, source.source_agent_id) for source in page.items[0].provenance
    } == {("observatory-a", "agent-a"), ("observatory-b", "agent-b")}


@pytest.mark.asyncio
async def test_aggregate_does_not_merge_same_card_signed_by_different_keys() -> None:
    instances = [_instance("observatory-a", "cluster-a"), _instance("observatory-b", "cluster-b")]
    client = _StubClient(
        {
            "observatory-a": _page(
                "local-a",
                _entry(
                    "local-a",
                    "agent-a",
                    card_hash="copied-card",
                    signature_verified=True,
                    signature_key_fingerprints=("signer-a",),
                ),
            ),
            "observatory-b": _page(
                "local-b",
                _entry(
                    "local-b",
                    "agent-b",
                    card_hash="copied-card",
                    signature_verified=True,
                    signature_key_fingerprints=("signer-b",),
                ),
            ),
        }
    )
    service = AgentDirectoryAggregationService(client=client, max_concurrency=2)  # type: ignore[arg-type]

    page = await service.list_agents(instances, _principal(), headers={})

    assert len(page.items) == 2
    assert len({entry.canonical_id for entry in page.items}) == 2


@pytest.mark.asyncio
async def test_aggregate_returns_healthy_results_with_failed_source_warning() -> None:
    instances = [_instance("observatory-a", "cluster-a"), _instance("observatory-b", "cluster-b")]
    client = _StubClient(
        {
            "observatory-a": _page("local-a", _entry("local-a", "agent-a")),
            "observatory-b": httpx.ReadTimeout("slow source"),
        }
    )
    service = AgentDirectoryAggregationService(client=client, max_concurrency=2)  # type: ignore[arg-type]

    page = await service.list_agents(instances, _principal(), headers={})

    assert [entry.source_agent_id for entry in page.items] == ["agent-a"]
    assert page.partial is True
    assert page.warnings[0].source_instance_id == "observatory-b"
    assert page.warnings[0].code == "observatory-unavailable"
    assert page.warnings[0].message == "Observatory Agent Directory request failed"
    assert any(source.status == "failed" for source in page.sources)


@pytest.mark.asyncio
async def test_aggregate_preserves_source_partial_health_without_warning() -> None:
    instance = _instance("observatory-a", "cluster-a")
    source_page = _page("observatory-a", _entry("local-a", "agent-a"))
    source_page.partial = True
    client = _StubClient({"observatory-a": source_page})
    service = AgentDirectoryAggregationService(client=client, max_concurrency=1)  # type: ignore[arg-type]

    page = await service.list_agents([instance], _principal(), headers={})

    assert page.partial is True
    assert page.sources[0].status == "degraded"


@pytest.mark.asyncio
async def test_aggregate_rechecks_visibility_filters_and_detail() -> None:
    instance = _instance("observatory-a", "cluster-a")
    client = _StubClient(
        {
            "observatory-a": _page(
                "local-a",
                _entry("local-a", "visible"),
                _entry("local-a", "hidden", owner_id="user-b"),
                _entry("local-a", "cross-tenant", tenant_id="tenant-b"),
            )
        }
    )
    service = AgentDirectoryAggregationService(client=client, max_concurrency=2)  # type: ignore[arg-type]
    filters = AgentDirectoryFilters(
        skills=("code",),
        tags=("engineering",),
        kinds=("workflow-session",),
        statuses=("healthy",),
        environment_ids=("environment-a",),
        cluster_ids=("local-cluster",),
        instance_ids=("observatory-a",),
    )

    page = await service.list_agents(
        [instance],
        _principal(),
        headers={},
        filters=filters,
    )
    detail = await service.get_agent(page.items[0].id, [instance], _principal(), headers={})

    assert [entry.source_agent_id for entry in page.items] == ["visible"]
    assert detail is not None
    assert detail.source_agent_id == "visible"
    assert client.calls[0][2].instance_ids == ()


@pytest.mark.asyncio
async def test_http_client_forwards_filters_and_auth() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_page("observatory-a").model_dump(by_alias=True))

    client = HttpAgentDirectoryClient(
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )
    filters = AgentDirectoryFilters(
        skills=("code",),
        tags=("engineering",),
        kinds=("workflow-session",),
        statuses=("healthy",),
        environment_ids=("environment-a",),
        cluster_ids=("cluster-a",),
        instance_ids=("observatory-a",),
    )

    await client.list_agents(
        _instance("observatory-a", "cluster-a"),
        headers={"authorization": "Bearer caller"},
        filters=filters,
    )

    request = requests[0]
    assert request.url.path == "/api/v1/observatory/agents"
    assert request.headers["authorization"] == "Bearer caller"
    assert request.url.params["skill"] == "code"
    assert request.url.params["environmentId"] == "environment-a"


@pytest.mark.asyncio
async def test_http_client_accepts_full_endpoint_base_without_double_appending() -> None:
    requests: list[httpx.Request] = []
    client = HttpAgentDirectoryClient(
        timeout_seconds=1.0,
        transport=httpx.MockTransport(
            lambda request: (
                requests.append(request)
                or httpx.Response(200, json=_page("observatory-a").model_dump(by_alias=True))
            )
        ),
    )
    instance = replace(
        _instance("observatory-a", "cluster-a"),
        base_url="https://observatory-a.example.test/api/v1/observatory/agents",
    )

    await client.list_agents(instance, headers={}, filters=AgentDirectoryFilters())

    assert requests[0].url.path == "/api/v1/observatory/agents"
