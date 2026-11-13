"""The Flux target provisions one gbrain HelmRelease per org, with its seal, once.

Against an in-memory Kubernetes whose creates are atomic (a second create of one
name is a 409), as the API server's are.
"""

from __future__ import annotations

import asyncio
import base64
import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

from mimir.adapters.flux import MEMORY_MANAGED_BY, FluxKnowledgeDeploymentAdapter, _image
from mimir.ports.deployment import (
    MemoryProvisioningError,
    MemorySystemPhase,
    memory_system_name,
)

NS = "memory"
IMAGE = "ghcr.io/niuulabs/gbrain:dev-abc@sha256:0123"
TENANT = "initech"
NAME = memory_system_name(TENANT)


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


class FakeKubernetes:
    """HelmReleases, Secrets and Services of one namespace."""

    def __init__(self) -> None:
        self.releases: dict[str, dict[str, Any]] = {}
        self.secrets: dict[str, dict[str, Any]] = {}
        self.services: dict[str, int] = {}
        self.release_creates = 0
        self.secret_creates = 0
        self.fail_with: ApiException | None = None

    # CustomObjectsApi
    async def create_namespaced_custom_object(self, group, version, namespace, plural, body):
        await asyncio.sleep(0)  # let a concurrent caller interleave
        self._maybe_fail()
        name = body["metadata"]["name"]
        if name in self.releases:
            raise ApiException(status=409, reason="AlreadyExists")
        self.release_creates += 1
        created = copy.deepcopy(body)
        created["metadata"]["generation"] = 1
        self.releases[name] = created
        return copy.deepcopy(created)

    async def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        self._maybe_fail()
        if name not in self.releases:
            raise ApiException(status=404, reason="NotFound")
        return copy.deepcopy(self.releases[name])

    async def list_namespaced_custom_object(
        self, group, version, namespace, plural, label_selector=""
    ):
        self._maybe_fail()
        key, _, value = label_selector.partition("=")
        return {
            "items": [
                copy.deepcopy(r)
                for r in self.releases.values()
                if r["metadata"]["labels"].get(key) == value
            ]
        }

    # CoreV1Api
    async def create_namespaced_secret(self, namespace, body):
        await asyncio.sleep(0)
        self._maybe_fail()
        name = body["metadata"]["name"]
        if name in self.secrets:
            raise ApiException(status=409, reason="AlreadyExists")
        self.secret_creates += 1
        self.secrets[name] = copy.deepcopy(body)

    async def read_namespaced_secret(self, name, namespace):
        self._maybe_fail()
        if name not in self.secrets:
            raise ApiException(status=404, reason="NotFound")
        body = self.secrets[name]
        return SimpleNamespace(
            data=body.get("data"),
            metadata=SimpleNamespace(annotations=body["metadata"].get("annotations")),
        )

    async def read_namespaced_service(self, name, namespace):
        if name not in self.services:
            raise ApiException(status=404, reason="NotFound")
        return SimpleNamespace(
            spec=SimpleNamespace(ports=[SimpleNamespace(port=self.services[name])])
        )

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    # The chart and Flux, done
    def install(self, name: str = NAME, *, admin: str = "owner-token") -> None:
        release = self.releases[name]
        release["status"] = {
            "observedGeneration": 1,
            "conditions": [
                {"type": "Ready", "status": "True", "message": "Helm install succeeded"}
            ],
        }
        if admin:
            self.secrets[f"{name}-gbrain-admin"] = {
                "metadata": {"name": f"{name}-gbrain-admin"},
                "data": {"GBRAIN_ADMIN_BOOTSTRAP_TOKEN": _b64(admin)},
            }
        self.services[f"{name}-gbrain"] = 3131


@pytest.fixture
def kube():
    fake = FakeKubernetes()
    with patch("kubernetes_asyncio.client.CoreV1Api", return_value=fake):
        yield fake


def _target(kube: FakeKubernetes, **kwargs) -> FluxKnowledgeDeploymentAdapter:
    target = FluxKnowledgeDeploymentAdapter(
        namespace=NS,
        source_name="niuu",
        chart_versions={"gbrain": "2.0.0-dev.610"},
        images={"gbrain": IMAGE},
        cluster="ymir",
        storage_class="harvester-data",
        **kwargs,
    )

    async def api():
        return kube

    target._get_api = api
    return target


class TestProvisioning:
    async def test_the_first_call_makes_the_seal_then_the_release(self, kube) -> None:
        record = await _target(kube).provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.PREPARING
        assert record.started
        release = kube.releases[NAME]
        assert release["metadata"]["labels"]["niuu.world/managed-by"] == MEMORY_MANAGED_BY
        assert release["metadata"]["annotations"]["niuu.world/tenant-id"] == TENANT
        values = release["spec"]["values"]
        assert values["engine"] == "postgres"
        assert values["postgres"] == {"enabled": True, "storageClass": "harvester-data"}
        assert values["connection"] == {"enabled": False}
        assert "existingSecret" not in values
        assert values["image"] == {
            "repository": "ghcr.io/niuulabs/gbrain",
            "tag": "dev-abc@sha256:0123",
        }
        assert values["niuu"] == {"cluster": "ymir", "instanceId": NAME, "tenantId": TENANT}
        assert release["spec"]["chart"]["spec"]["version"] == "2.0.0-dev.610"
        seal = kube.secrets[f"{NAME}-memory-seal"]
        assert seal["metadata"]["annotations"]["niuu.world/tenant-id"] == TENANT
        assert len(base64.b64decode(seal["data"]["key"])) == 64

    async def test_later_calls_read_it_and_never_make_another(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        again = await target.provision_memory_system(TENANT)
        assert again.phase is MemorySystemPhase.PREPARING
        assert not again.started
        assert kube.release_creates == 1
        assert kube.secret_creates == 1

    async def test_two_replicas_at_once_provision_once(self, kube) -> None:
        first, second = _target(kube), _target(kube)
        records = await asyncio.gather(
            first.provision_memory_system(TENANT), second.provision_memory_system(TENANT)
        )
        assert kube.release_creates == 1
        assert kube.secret_creates == 1
        assert sorted(r.started for r in records) == [False, True]
        kube.install()
        ready = await asyncio.gather(
            first.provision_memory_system(TENANT), second.memory_system(TENANT)
        )
        assert {r.signing_key for r in ready} == {
            base64.b64decode(kube.secrets[f"{NAME}-memory-seal"]["data"]["key"]).decode()
        }

    async def test_ready_gives_the_connection(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.install()
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.READY
        assert record.mcp_url == f"http://{NAME}-gbrain.{NS}.svc.cluster.local:3131/mcp"
        assert record.admin_token == "owner-token"
        assert record.signing_key

    async def test_an_older_generation_is_still_preparing(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.install()
        kube.releases[NAME]["metadata"]["generation"] = 2
        record = await target.memory_system(TENANT)
        assert record.phase is MemorySystemPhase.PREPARING

    async def test_a_stalled_release_failed(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.releases[NAME]["status"] = {
            "conditions": [
                {"type": "Ready", "status": "False", "message": "install failed"},
                {"type": "Stalled", "status": "True", "reason": "RetriesExceeded", "message": "x"},
            ]
        }
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "RetriesExceeded" in record.message
        assert "flux reconcile" in record.message

    async def test_a_release_whose_seal_is_gone_failed_and_no_seal_is_made(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        del kube.secrets[f"{NAME}-memory-seal"]
        record = await target.provision_memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "unattested" in record.message
        assert f"{NAME}-memory-seal" not in kube.secrets

    async def test_a_seal_left_by_a_stopped_attempt_is_kept(self, kube) -> None:
        kube.secrets[f"{NAME}-memory-seal"] = {
            "metadata": {"annotations": {"niuu.world/tenant-id": TENANT}},
            "data": {"key": _b64("earlier-key")},
        }
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.install()
        record = await target.memory_system(TENANT)
        assert record.signing_key == "earlier-key"

    async def test_an_installed_release_without_its_owner_token_failed(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.install(admin="")
        record = await target.memory_system(TENANT)
        assert record.phase is MemorySystemPhase.FAILED
        assert "GBRAIN_ADMIN_BOOTSTRAP_TOKEN" in record.message

    async def test_another_orgs_release_under_the_name_is_refused(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        kube.releases[NAME]["metadata"]["annotations"]["niuu.world/tenant-id"] = "globex"
        with pytest.raises(MemoryProvisioningError, match="collide"):
            await target.provision_memory_system(TENANT)

    async def test_kubernetes_refusing_raises(self, kube) -> None:
        kube.fail_with = ApiException(status=403, reason="Forbidden")
        target = _target(kube)
        with pytest.raises(MemoryProvisioningError, match="Forbidden"):
            await target.provision_memory_system(TENANT)
        with pytest.raises(MemoryProvisioningError, match="Forbidden"):
            await target.memory_system(TENANT)
        with pytest.raises(MemoryProvisioningError, match="Forbidden"):
            await target.memory_systems()

    async def test_without_a_chart_and_image_it_raises(self, kube) -> None:
        target = FluxKnowledgeDeploymentAdapter(
            namespace=NS, source_name="niuu", chart_versions={}, images={}
        )

        async def api():
            return kube

        target._get_api = api
        with pytest.raises(MemoryProvisioningError, match="chart_versions.gbrain"):
            await target.provision_memory_system(TENANT)

    async def test_nothing_provisioned_reads_as_none(self, kube) -> None:
        assert await _target(kube).memory_system(TENANT) is None
        assert kube.release_creates == 0


class TestValuesAndListing:
    async def test_memory_values_merge_over_the_defaults(self, kube) -> None:
        target = _target(
            kube,
            memory_values={"postgres": {"instances": 1, "size": "10Gi"}, "resources": {"a": 1}},
            gbrain_config={"provider_base_urls": {"x": "y"}},
        )
        await target.provision_memory_system(TENANT)
        values = kube.releases[NAME]["spec"]["values"]
        assert values["postgres"] == {
            "enabled": True,
            "storageClass": "harvester-data",
            "instances": 1,
            "size": "10Gi",
        }
        assert values["resources"] == {"a": 1}
        assert values["config"] == {"provider_base_urls": {"x": "y"}}

    @pytest.mark.parametrize("owned", ["existingSecret", "niuu", "image"])
    def test_values_the_deployment_owns_are_refused(self, owned) -> None:
        with pytest.raises(ValueError, match=owned):
            FluxKnowledgeDeploymentAdapter(
                namespace=NS,
                source_name="niuu",
                chart_versions={},
                images={},
                memory_values={owned: "shared"},
            )

    async def test_memory_systems_lists_only_memory_releases(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        await target.provision_memory_system("globex")
        kube.install()
        kube.releases["knowledge-x"] = {
            "metadata": {"name": "knowledge-x", "labels": {"niuu.world/managed-by": "mimir"}}
        }
        records = {r.tenant_id: r.phase for r in await target.memory_systems()}
        assert records == {TENANT: MemorySystemPhase.READY, "globex": MemorySystemPhase.PREPARING}
        assert (await target.list_deployments(tenant_id=TENANT))["releases"] == []

    async def test_a_memory_release_without_an_org_is_refused(self, kube) -> None:
        target = _target(kube)
        await target.provision_memory_system(TENANT)
        del kube.releases[NAME]["metadata"]["annotations"]["niuu.world/tenant-id"]
        with pytest.raises(MemoryProvisioningError, match="not made by the gateway"):
            await target.memory_systems()


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ghcr.io/niuulabs/gbrain:dev-1", ("ghcr.io/niuulabs/gbrain", "dev-1")),
        ("localhost:5000/gbrain:v1", ("localhost:5000/gbrain", "v1")),
        ("ghcr.io/x/gbrain:t@sha256:ab", ("ghcr.io/x/gbrain", "t@sha256:ab")),
    ],
)
def test_image_references_keep_their_digest(reference, expected) -> None:
    assert _image(reference) == expected


@pytest.mark.parametrize("reference", ["ghcr.io/x/gbrain", "localhost:5000/gbrain"])
def test_an_image_without_a_tag_is_refused(reference) -> None:
    with pytest.raises(ValueError, match="no tag"):
        _image(reference)
