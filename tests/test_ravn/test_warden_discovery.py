"""Tests for Warden discovery adapters."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from ravn.adapters.warden_discovery.http_ravn import HttpRavnWardenDiscoveryAdapter
from ravn.adapters.warden_discovery.kubernetes import (
    KubernetesWardenDiscoveryAdapter,
    _objectify,
)
from ravn.adapters.warden_discovery.local_host import LocalHostWardenDiscoveryAdapter
from ravn.adapters.warden_discovery.spec import WardenSpecDiscoveryAdapter
from ravn.api import create_app
from ravn.config import WardenDiscoveryConfig
from ravn.warden.discovery import CompositeWardenDiscoveryAdapter
from ravn.warden.models import WardenSpec
from ravn.warden.store import WardenStore


@pytest.mark.asyncio
async def test_spec_and_local_host_adapters_read_warden_specs(tmp_path) -> None:
    store = WardenStore(tmp_path)
    store.save(WardenSpec(id="mimir-shared-warden", name="Mimir Shared Warden"))

    spec_adapter = WardenSpecDiscoveryAdapter(store=store)
    local_adapter = LocalHostWardenDiscoveryAdapter(store=store, host_name="workstation")

    spec_wardens = await spec_adapter.list_wardens()
    local_wardens = await local_adapter.list_wardens()

    assert spec_wardens[0].id == "mimir-shared-warden"
    assert local_wardens[0].supervisor.observation.source == "local-host"
    assert local_wardens[0].deployment_kwargs["host"] == "workstation"


@pytest.mark.asyncio
async def test_composite_discovery_uses_later_adapter_for_duplicate_ids() -> None:
    first = _StaticDiscovery([WardenSpec(id="shared", name="Persisted")])
    second = _StaticDiscovery([WardenSpec(id="shared", name="Observed")])

    wardens = await CompositeWardenDiscoveryAdapter([first, second]).list_wardens()

    assert [(warden.id, warden.name) for warden in wardens] == [("shared", "Observed")]


def test_warden_discovery_config_parses_adapter_json() -> None:
    config = WardenDiscoveryConfig(
        adapters_json="""
        [
          {
            "adapter": "ravn.adapters.warden_discovery.spec.WardenSpecDiscoveryAdapter"
          },
          {
            "adapter": "ravn.adapters.warden_discovery.kubernetes.KubernetesWardenDiscoveryAdapter",
            "namespace": "volundr"
          }
        ]
        """
    )

    assert len(config.adapters) == 2
    assert config.adapters[1].adapter_kwargs()["namespace"] == "volundr"


def test_ravn_api_lists_discovered_wardens_and_blocks_lifecycle(tmp_path) -> None:
    discovery = _StaticDiscovery([WardenSpec(id="mimir-shared-warden", name="Mimir Shared Warden")])
    client = TestClient(
        create_app(
            warden_store=WardenStore(tmp_path),
            warden_discovery=discovery,
        )
    )

    listed = client.get("/api/v1/ravn/wardens")
    detail = client.get("/api/v1/ravn/wardens/mimir-shared-warden")
    start = client.post("/api/v1/ravn/wardens/mimir-shared-warden/start")

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == "mimir-shared-warden"
    assert detail.status_code == 200
    assert start.status_code == 409


@pytest.mark.asyncio
async def test_http_ravn_adapter_reads_remote_wardens(monkeypatch) -> None:
    payload = [WardenSpec(id="remote", name="Remote Warden").model_dump(mode="json")]

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://ravn.example.com/api/v1/ravn/wardens"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)

    class MockAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(transport=transport)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    adapter = HttpRavnWardenDiscoveryAdapter(base_url="https://ravn.example.com")

    wardens = await adapter.list_wardens()

    assert wardens[0].id == "remote"


@pytest.mark.asyncio
async def test_kubernetes_adapter_converts_labeled_deployment_to_warden() -> None:
    deployment = _deployment(
        labels={
            "niuu.world/kind": "warden",
            "niuu.world/warden-id": "mimir-shared-warden",
            "niuu.world/warden-persona": "mimir-warden",
            "niuu.world/ravn-instance": "ravn-yggdrasil",
            "niuu.world/mimir-instance": "mimir-yggdrasil",
            "niuu.world/mimir-mount": "shared",
        },
        annotations={
            "niuu.world/warden-name": "Mimir Shared Warden",
            "niuu.world/dream-cycle-cron": "*/15 * * * *",
        },
    )
    adapter = KubernetesWardenDiscoveryAdapter(
        namespace="volundr",
        apps_api=SimpleNamespace(
            list_namespaced_deployment=lambda **kwargs: SimpleNamespace(items=[deployment])
        ),
    )

    wardens = await adapter.list_wardens()

    assert wardens[0].id == "mimir-shared-warden"
    assert wardens[0].deployment == "kubernetes"
    assert wardens[0].mimir.write_mount == "shared"
    assert wardens[0].schedules.dream_cycle_cron_expression == "*/15 * * * *"
    assert wardens[0].supervisor.observation.status == "running"


def test_kubernetes_adapter_converts_rest_deployment_payload() -> None:
    adapter = KubernetesWardenDiscoveryAdapter(namespace="volundr")
    deployment = _objectify(
        {
            "metadata": {
                "name": "mimir-shared-warden-agent",
                "namespace": "volundr",
            },
            "spec": {
                "template": {
                    "metadata": {
                        "labels": {
                            "niuu.world/kind": "warden",
                            "niuu.world/warden-id": "mimir-shared-warden",
                            "niuu.world/mimir-mount": "shared",
                        },
                        "annotations": {
                            "niuu.world/warden-name": "Mimir Shared Warden",
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "image": "ghcr.io/niuulabs/agent:dev",
                                "env": [
                                    {"name": "RAVN_PERSONA", "value": "mimir-warden"},
                                ],
                            }
                        ]
                    },
                }
            },
            "status": {
                "replicas": 1,
                "readyReplicas": 1,
                "availableReplicas": 1,
            },
        }
    )

    warden = adapter._deployment_to_warden(deployment)

    assert warden is not None
    assert warden.id == "mimir-shared-warden"
    assert warden.supervisor.observation.status == "running"
    assert warden.supervisor.observation.fields[3].value == "1"


class _StaticDiscovery:
    def __init__(self, wardens: list[WardenSpec]) -> None:
        self._wardens = wardens

    async def list_wardens(self) -> list[WardenSpec]:
        return self._wardens


def _deployment(
    *,
    labels: dict[str, str],
    annotations: dict[str, str],
) -> SimpleNamespace:
    container = SimpleNamespace(
        image="ghcr.io/niuulabs/niuu:dev",
        env=[
            SimpleNamespace(name="RAVN_PERSONA", value="mimir-warden"),
            SimpleNamespace(name="RAVN_LLM__MODEL", value="claude-sonnet-4-6"),
        ],
    )
    pod_template = SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, annotations=annotations),
        spec=SimpleNamespace(containers=[container]),
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name="mimir-shared-warden-agent",
            namespace="volundr",
            labels=labels,
            annotations=annotations,
        ),
        spec=SimpleNamespace(template=pod_template),
        status=SimpleNamespace(replicas=1, ready_replicas=1, available_replicas=1),
    )
