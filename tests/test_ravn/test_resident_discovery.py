"""Tests for standalone-resident discovery adapters and composition."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx
from fastapi.testclient import TestClient

from ravn.adapters.kubernetes_deployments import _objectify
from ravn.adapters.resident_discovery.kubernetes import KubernetesResidentDiscoveryAdapter
from ravn.api import create_app
from ravn.config import ResidentDiscoveryConfig, Settings
from ravn.ports.resident_discovery import StandaloneResident
from ravn.resident_discovery import (
    CompositeResidentDiscoveryAdapter,
    build_resident_discovery,
)
from ravn.warden.store import WardenStore


def _resident(**overrides) -> StandaloneResident:
    fields = {
        "id": "resident-muninn",
        "resident_name": "Muninn",
        "persona_name": "product-steward",
        "status": "active",
    }
    fields.update(overrides)
    return StandaloneResident(**fields)


def _deployment(
    *,
    name: str = "resident-muninn",
    labels: dict[str, str] | None = None,
    annotations: dict[str, str] | None = None,
    replicas: int | None = 1,
    ready_replicas: int | None = 1,
) -> SimpleNamespace:
    labels = labels if labels is not None else {"niuu.world/kind": "resident"}
    annotations = annotations or {}
    pod_template = SimpleNamespace(
        metadata=SimpleNamespace(labels=labels, annotations=annotations),
        spec=SimpleNamespace(containers=[]),
    )
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name,
            namespace="volundr",
            labels=labels,
            annotations=annotations,
            creation_timestamp="2026-07-01T09:00:00+00:00",
        ),
        spec=SimpleNamespace(template=pod_template, replicas=replicas),
        status=SimpleNamespace(replicas=replicas, ready_replicas=ready_replicas),
    )


def _adapter_for(*deployments: SimpleNamespace) -> KubernetesResidentDiscoveryAdapter:
    return KubernetesResidentDiscoveryAdapter(
        namespace="volundr",
        apps_api=SimpleNamespace(
            list_namespaced_deployment=lambda **kwargs: SimpleNamespace(items=list(deployments))
        ),
    )


class TestKubernetesResidentDiscoveryAdapter:
    async def test_converts_labeled_deployment_to_resident(self) -> None:
        deployment = _deployment(
            labels={
                "niuu.world/kind": "resident",
                "niuu.world/persona": "product-steward",
            },
            annotations={
                "niuu.world/resident-name": "Muninn",
                "niuu.world/chat-endpoint": "ws://resident-muninn/session",
                "niuu.world/model": "claude-fable-5",
            },
        )

        residents = await _adapter_for(deployment).list_residents()

        assert len(residents) == 1
        resident = residents[0]
        assert resident.id == "resident-muninn"
        assert resident.resident_name == "Muninn"
        assert resident.persona_name == "product-steward"
        assert resident.status == "active"
        assert resident.model == "claude-fable-5"
        assert resident.chat_endpoint == "ws://resident-muninn/session"
        assert resident.location == "volundr/resident-muninn"
        assert resident.created_at == "2026-07-01T09:00:00+00:00"

    async def test_defaults_when_annotations_absent(self) -> None:
        residents = await _adapter_for(_deployment()).list_residents()

        resident = residents[0]
        assert resident.resident_name == "resident-muninn"
        assert resident.persona_name == ""
        assert resident.model == ""
        assert resident.chat_endpoint is None
        assert resident.updated_at is None

    async def test_skips_non_resident_deployments(self) -> None:
        warden = _deployment(name="mimir-warden", labels={"niuu.world/kind": "warden"})
        unlabeled = _deployment(name="plain-app", labels={})

        residents = await _adapter_for(warden, unlabeled).list_residents()

        assert residents == []

    async def test_skips_nameless_deployment(self) -> None:
        residents = await _adapter_for(_deployment(name="")).list_residents()

        assert residents == []

    async def test_scaled_to_zero_is_suspended(self) -> None:
        deployment = _deployment(replicas=0, ready_replicas=0)

        residents = await _adapter_for(deployment).list_residents()

        assert residents[0].status == "suspended"

    async def test_not_ready_is_idle(self) -> None:
        deployment = _deployment(replicas=1, ready_replicas=0)

        residents = await _adapter_for(deployment).list_residents()

        assert residents[0].status == "idle"

    async def test_unknown_desired_replicas_is_idle(self) -> None:
        deployment = _deployment(replicas=None, ready_replicas=0)

        residents = await _adapter_for(deployment).list_residents()

        assert residents[0].status == "idle"

    async def test_converts_rest_deployment_payload(self) -> None:
        adapter = KubernetesResidentDiscoveryAdapter(namespace="volundr")
        deployment = _objectify(
            {
                "metadata": {
                    "name": "resident-muninn",
                    "namespace": "volundr",
                    "creationTimestamp": "2026-07-01T09:00:00Z",
                    "labels": {"niuu.world/kind": "resident"},
                    "annotations": {"niuu.world/resident-name": "Muninn"},
                },
                "spec": {
                    "replicas": 1,
                    "template": {
                        "metadata": {
                            "labels": {"niuu.world/persona": "product-steward"},
                        },
                        "spec": {"containers": []},
                    },
                },
                "status": {"replicas": 1, "readyReplicas": 1},
            }
        )

        resident = adapter._deployment_to_resident(deployment)

        assert resident is not None
        assert resident.id == "resident-muninn"
        assert resident.resident_name == "Muninn"
        assert resident.persona_name == "product-steward"
        assert resident.status == "active"
        assert resident.created_at == "2026-07-01T09:00:00Z"
        assert resident.location == "volundr/resident-muninn"


class TestKubernetesListingPlumbing:
    """Shared deployment-listing behavior (no client library installed)."""

    async def test_no_token_mounted_returns_empty(self, tmp_path, monkeypatch) -> None:
        import ravn.adapters.kubernetes_deployments as kd

        monkeypatch.setattr(kd, "_SERVICE_ACCOUNT_ROOT", tmp_path)
        adapter = KubernetesResidentDiscoveryAdapter(namespace="volundr")

        assert await adapter.list_residents() == []

    @respx.mock
    async def test_incluster_rest_listing_maps_residents(self, tmp_path, monkeypatch) -> None:
        import ravn.adapters.kubernetes_deployments as kd

        (tmp_path / "token").write_text("sa-token", encoding="utf-8")
        monkeypatch.setattr(kd, "_SERVICE_ACCOUNT_ROOT", tmp_path)
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
        route = respx.get(
            "https://kubernetes.default.svc:443/apis/apps/v1/namespaces/volundr/deployments"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "metadata": {
                                "name": "resident-muninn",
                                "namespace": "volundr",
                                "creationTimestamp": "2026-07-01T09:00:00Z",
                                "labels": {"niuu.world/kind": "resident"},
                                "annotations": {"niuu.world/resident-name": "Muninn"},
                            },
                            "spec": {"replicas": 1},
                            "status": {"readyReplicas": 1},
                        },
                        {
                            "metadata": {
                                "name": "mimir-warden",
                                "namespace": "volundr",
                                "labels": {"niuu.world/kind": "warden"},
                            },
                            "spec": {"replicas": 1},
                            "status": {"readyReplicas": 1},
                        },
                    ]
                },
            )
        )
        adapter = KubernetesResidentDiscoveryAdapter(
            namespace="volundr",
            label_selector="niuu.world/kind=resident",
        )

        residents = await adapter.list_residents()

        assert [item.id for item in residents] == ["resident-muninn"]
        assert residents[0].status == "active"
        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer sa-token"
        assert "labelSelector=niuu.world" in str(request.url)

    @respx.mock
    async def test_incluster_rest_all_namespaces_error_returns_empty(
        self, tmp_path, monkeypatch
    ) -> None:
        import ravn.adapters.kubernetes_deployments as kd

        (tmp_path / "token").write_text("sa-token", encoding="utf-8")
        monkeypatch.setattr(kd, "_SERVICE_ACCOUNT_ROOT", tmp_path)
        monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
        monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
        respx.get("https://kubernetes.default.svc:443/apis/apps/v1/deployments").mock(
            return_value=httpx.Response(500)
        )
        adapter = KubernetesResidentDiscoveryAdapter()

        assert await adapter.list_residents() == []

    def test_load_config_prefers_explicit_mode(self) -> None:
        adapter = KubernetesResidentDiscoveryAdapter(
            in_cluster=False, kubeconfig="/tmp/kc", context="valhalla"
        )
        recorder = _ConfigRecorder()

        adapter._load_config(recorder)

        assert recorder.kube_config_calls == [{"config_file": "/tmp/kc", "context": "valhalla"}]
        assert recorder.incluster_calls == 0

    def test_load_config_in_cluster_true(self) -> None:
        recorder = _ConfigRecorder()

        KubernetesResidentDiscoveryAdapter(in_cluster=True)._load_config(recorder)

        assert recorder.incluster_calls == 1
        assert recorder.kube_config_calls == []

    def test_load_config_auto_falls_back_to_kubeconfig(self) -> None:
        recorder = _ConfigRecorder(incluster_raises=True)

        KubernetesResidentDiscoveryAdapter()._load_config(recorder)

        assert recorder.incluster_calls == 1
        assert recorder.kube_config_calls == [{"config_file": None, "context": None}]

    def test_load_config_auto_uses_incluster_when_available(self) -> None:
        recorder = _ConfigRecorder()

        KubernetesResidentDiscoveryAdapter()._load_config(recorder)

        assert recorder.incluster_calls == 1
        assert recorder.kube_config_calls == []

    async def test_client_listing_uses_all_namespaces_without_namespace(self) -> None:
        deployment = _deployment()
        adapter = KubernetesResidentDiscoveryAdapter(
            apps_api=SimpleNamespace(
                list_deployment_for_all_namespaces=lambda **kwargs: SimpleNamespace(
                    items=[deployment]
                )
            )
        )

        residents = await adapter.list_residents()

        assert [item.id for item in residents] == ["resident-muninn"]

    def test_creation_timestamp_from_datetime_object(self) -> None:
        from datetime import UTC, datetime

        adapter = KubernetesResidentDiscoveryAdapter()
        metadata = SimpleNamespace(
            creation_timestamp=datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC)
        )

        assert adapter._creation_timestamp(metadata) == "2026-07-01T09:00:00+00:00"

    def test_creation_timestamp_absent(self) -> None:
        adapter = KubernetesResidentDiscoveryAdapter()

        assert adapter._creation_timestamp(SimpleNamespace(creation_timestamp=None)) is None

    async def test_builds_client_from_kubernetes_library_when_installed(
        self, monkeypatch
    ) -> None:
        import sys
        from types import ModuleType

        deployment = _deployment()
        apps_api = SimpleNamespace(
            list_namespaced_deployment=lambda **kwargs: SimpleNamespace(items=[deployment])
        )
        fake_kubernetes = ModuleType("kubernetes")
        fake_kubernetes.client = SimpleNamespace(AppsV1Api=lambda: apps_api)
        fake_kubernetes.config = SimpleNamespace(load_incluster_config=lambda: None)
        monkeypatch.setitem(sys.modules, "kubernetes", fake_kubernetes)
        adapter = KubernetesResidentDiscoveryAdapter(namespace="volundr", in_cluster=True)

        residents = await adapter.list_residents()

        assert [item.id for item in residents] == ["resident-muninn"]

    async def test_port_protocol_default_raises(self) -> None:
        import pytest

        from ravn.ports.resident_discovery import ResidentDiscoveryPort

        with pytest.raises(NotImplementedError):
            await ResidentDiscoveryPort.list_residents(object())


class _ConfigRecorder:
    def __init__(self, *, incluster_raises: bool = False) -> None:
        self.incluster_calls = 0
        self.kube_config_calls: list[dict] = []
        self._incluster_raises = incluster_raises

    def load_incluster_config(self) -> None:
        self.incluster_calls += 1
        if self._incluster_raises:
            raise RuntimeError("not in cluster")

    def load_kube_config(self, *, config_file, context) -> None:
        self.kube_config_calls.append({"config_file": config_file, "context": context})


class TestCompositeResidentDiscoveryAdapter:
    async def test_dedupes_by_id_with_later_adapter_winning(self) -> None:
        first = _StaticResidentDiscovery([_resident(resident_name="Persisted")])
        second = _StaticResidentDiscovery([_resident(resident_name="Observed")])

        residents = await CompositeResidentDiscoveryAdapter([first, second]).list_residents()

        assert [(item.id, item.resident_name) for item in residents] == [
            ("resident-muninn", "Observed")
        ]

    async def test_tolerates_failing_adapter(self) -> None:
        composite = CompositeResidentDiscoveryAdapter(
            [_FailingResidentDiscovery(), _StaticResidentDiscovery([_resident()])]
        )

        residents = await composite.list_residents()

        assert [item.id for item in residents] == ["resident-muninn"]


class TestResidentDiscoveryConfig:
    def test_parses_adapter_json(self) -> None:
        config = ResidentDiscoveryConfig(
            adapters_json="""
            [
              {
                "adapter": "ravn.adapters.resident_discovery.kubernetes\
.KubernetesResidentDiscoveryAdapter",
                "namespace": "volundr"
              }
            ]
            """
        )

        assert len(config.adapters) == 1
        assert config.adapters[0].adapter_kwargs()["namespace"] == "volundr"

    def test_settings_carry_resident_discovery_section(self) -> None:
        settings = Settings()

        assert settings.resident_discovery.enabled is True
        assert settings.resident_discovery.adapters == []


class TestBuildResidentDiscovery:
    async def test_no_config_yields_empty_composite(self) -> None:
        discovery = build_resident_discovery(None)

        assert isinstance(discovery, CompositeResidentDiscoveryAdapter)
        assert await discovery.list_residents() == []

    async def test_disabled_yields_empty_composite(self) -> None:
        config = ResidentDiscoveryConfig(
            enabled=False,
            adapters_json=(
                '[{"adapter": "ravn.adapters.resident_discovery.kubernetes'
                '.KubernetesResidentDiscoveryAdapter"}]'
            ),
        )

        discovery = build_resident_discovery(config)

        assert isinstance(discovery, CompositeResidentDiscoveryAdapter)
        assert await discovery.list_residents() == []

    def test_builds_configured_kubernetes_adapter(self) -> None:
        config = ResidentDiscoveryConfig(
            adapters_json=(
                '[{"adapter": "ravn.adapters.resident_discovery.kubernetes'
                '.KubernetesResidentDiscoveryAdapter", "namespace": "volundr"}]'
            )
        )

        discovery = build_resident_discovery(config)

        assert isinstance(discovery, CompositeResidentDiscoveryAdapter)
        assert len(discovery._adapters) == 1
        assert isinstance(discovery._adapters[0], KubernetesResidentDiscoveryAdapter)


@respx.mock
def test_ravn_api_lists_discovered_standalone_residents(tmp_path) -> None:
    settings = Settings()
    forge_base = settings.gateway.platform.base_url.rstrip("/")
    respx.get(f"{forge_base}/api/v1/forge/sessions").mock(
        return_value=httpx.Response(200, json=[])
    )
    client = TestClient(
        create_app(
            warden_store=WardenStore(tmp_path),
            settings=settings,
            resident_discovery=_StaticResidentDiscovery([_resident()]),
        )
    )

    ravens = client.get("/api/v1/ravn/ravens")
    sessions = client.get("/api/v1/ravn/sessions")

    assert ravens.status_code == 200
    assert [raven["id"] for raven in ravens.json()] == ["resident-muninn"]
    assert ravens.json()[0]["deployment"] == "standalone"
    assert sessions.status_code == 200
    assert [session["status"] for session in sessions.json()] == ["running"]


class _StaticResidentDiscovery:
    def __init__(self, residents: list[StandaloneResident]) -> None:
        self._residents = residents

    async def list_residents(self) -> list[StandaloneResident]:
        return self._residents


class _FailingResidentDiscovery:
    async def list_residents(self) -> list[StandaloneResident]:
        raise RuntimeError("cluster unreachable")
