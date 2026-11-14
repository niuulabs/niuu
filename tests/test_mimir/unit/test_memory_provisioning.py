"""Per-org memory systems provisioned on an org's first use (``memory.provisioning``).

The gateway's side, against a fake deployment target: the static map wins, an org
without one is provisioned by a member and refused loudly while it is prepared, a
connector never starts one, failures raise, and the routes answer each case with its
own status.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from mimir.app import build_memory_systems, create_app
from mimir.config import MemoryGatewayConfig, MemoryProvisioningConfig, MimirServiceConfig
from mimir.exceptions import MimirConfigurationError
from mimir.memory.gateway import MemoryGateway
from mimir.memory.retention import RetentionSweeper
from mimir.memory.systems import (
    MemoryPreparingError,
    MemoryProvisioningFailedError,
    MemorySystems,
    ProvisionedSystems,
    TenantNotConfiguredError,
)
from mimir.ports.deployment import (
    MemoryProvisioningError,
    MemorySystemPhase,
    MemorySystemRecord,
    memory_system_key,
    memory_system_name,
)
from mimir.ports.layer_lifetimes import LayerLifetimes, Lifetimes
from niuu.cap_telemetry import degraded_counts, reset_cap_hits
from tests.test_mimir.unit.memory_fakes import (
    ADMIN_TOKEN,
    MCP,
    PROJECT_ID,
    SESSION_ID,
    SIGNING_KEY,
    FakeGBrain,
    FakeMemoryDeployment,
    context,
    gbrain_backend,
    session_token,
)

INITECH_BASE = "https://initech-brain.test"
INITECH_MCP = f"{INITECH_BASE}/mcp"
FORGE = "http://forge.test"
CONTEXT_URL = f"{FORGE}/api/v1/forge/sessions/{SESSION_ID}/memory-context"
_FAKE = "tests.test_mimir.unit.memory_fakes.FakeMemoryDeployment"


@pytest.fixture(autouse=True)
def _reset():
    reset_cap_hits()
    FakeMemoryDeployment.instances.clear()


@pytest.fixture
def brains():
    acme, initech = FakeGBrain(), FakeGBrain(base=INITECH_BASE)
    with respx.mock(assert_all_called=False) as router:
        acme.mount(router)
        initech.mount(router)
        yield acme, initech


def _provisioned(deployment: FakeMemoryDeployment, retry_after: float = 30.0):
    return ProvisionedSystems(
        deployment,
        build=lambda record: gbrain_backend(record.mcp_url),
        retry_after_seconds=retry_after,
    )


@pytest.fixture
def deployment() -> FakeMemoryDeployment:
    return FakeMemoryDeployment(mcp_url=INITECH_MCP)


@pytest.fixture
async def systems(brains, deployment):
    built = MemorySystems(
        {"acme-brain": gbrain_backend()},
        {"acme": "acme-brain"},
        provisioned=_provisioned(deployment),
    )
    yield built
    await built.close()


class TestTheRecord:
    def test_a_ready_record_must_carry_its_connection(self) -> None:
        with pytest.raises(ValueError, match="admin_token, signing_key"):
            MemorySystemRecord(
                tenant_id="initech",
                name="memory-x",
                phase=MemorySystemPhase.READY,
                message="ready",
                mcp_url=INITECH_MCP,
            )

    def test_secrets_stay_out_of_its_repr(self) -> None:
        record = MemorySystemRecord(
            tenant_id="initech",
            name="memory-x",
            phase=MemorySystemPhase.READY,
            message="ready",
            mcp_url=INITECH_MCP,
            admin_token=ADMIN_TOKEN,
            signing_key=SIGNING_KEY,
        )
        assert ADMIN_TOKEN not in repr(record)
        assert SIGNING_KEY not in repr(record)

    def test_names_are_stable_and_name_safe(self) -> None:
        assert memory_system_key("initech") == memory_system_key("initech")
        assert memory_system_key("initech") != memory_system_key("acme")
        assert memory_system_name("Ünïcode org / 7").startswith("memory-")
        assert len(memory_system_name("x" * 500)) == len("memory-") + 16
        with pytest.raises(MemoryProvisioningError, match="tenant id is empty"):
            memory_system_key("")


class TestResolving:
    async def test_the_static_map_wins_and_is_never_provisioned(self, systems, deployment) -> None:
        name, _ = await systems.for_tenant("acme")
        assert name == "acme-brain"
        assert deployment.provision_calls == []

    async def test_without_provisioning_an_unmapped_org_names_both_settings(self, brains) -> None:
        built = MemorySystems({"acme-brain": gbrain_backend()}, {"acme": "acme-brain"})
        with pytest.raises(TenantNotConfiguredError, match="memory.tenants.*memory.provisioning"):
            await built.for_tenant("initech")
        await built.close()

    async def test_the_first_use_starts_it_and_every_use_until_ready_is_refused(
        self, systems, deployment
    ) -> None:
        with pytest.raises(MemoryPreparingError, match="being prepared") as first:
            await systems.for_tenant("initech")
        assert first.value.retry_after == 30.0
        with pytest.raises(MemoryPreparingError, match="installing"):
            await systems.for_tenant("initech")
        assert deployment.created == 1
        assert systems.health()["provisioning"] == {"started": 1, "preparing": 1}

    async def test_a_ready_system_is_built_once_and_cached(self, systems, deployment) -> None:
        deployment.make_ready("initech")
        name, backend = await systems.for_tenant("initech")
        again = await systems.for_tenant("initech")
        assert name == memory_system_name("initech")
        assert again == (name, backend)
        assert deployment.provision_calls == ["initech"]
        assert systems.health()["provisioning"] == {"ready": 1}

    async def test_a_connector_never_starts_provisioning(self, systems, deployment) -> None:
        with pytest.raises(TenantNotConfiguredError, match="first use by a member"):
            await systems.for_tenant("initech", provision=False)
        assert deployment.provision_calls == []
        assert deployment.read_calls == ["initech"]
        deployment.make_ready("initech")
        name, _ = await systems.for_tenant("initech", provision=False)
        assert name == memory_system_name("initech")

    async def test_a_failed_provisioning_raises_with_its_remedy(self, systems, deployment) -> None:
        deployment.make_failed("initech", "Flux stopped: chart not found")
        with pytest.raises(MemoryProvisioningFailedError, match="chart not found"):
            await systems.for_tenant("initech")
        assert systems.health()["provisioning"] == {"failed": 1}

    async def test_a_target_that_cannot_be_asked_raises(self, systems, deployment) -> None:
        deployment.error = "forbidden: helmreleases"
        with pytest.raises(MemoryProvisioningFailedError, match="forbidden"):
            await systems.for_tenant("initech")
        with pytest.raises(MemoryProvisioningFailedError, match="forbidden"):
            await systems.for_tenant("initech", provision=False)

    async def test_a_provisioned_name_taken_by_a_configured_system_is_refused(
        self, brains, deployment
    ) -> None:
        taken = memory_system_name("initech")
        built = MemorySystems(
            {taken: gbrain_backend()}, {"acme": taken}, provisioned=_provisioned(deployment)
        )
        deployment.make_ready("initech")
        with pytest.raises(MemoryProvisioningFailedError, match="already uses"):
            await built.for_tenant("initech")
        await built.close()

    async def test_a_ready_system_that_cannot_be_built_is_refused(self, deployment) -> None:
        def broken(record):
            raise ValueError("bad mcp_url")

        provisioned = ProvisionedSystems(deployment, build=broken, retry_after_seconds=5)
        deployment.make_ready("initech")
        with pytest.raises(MemoryProvisioningFailedError, match="bad mcp_url"):
            await provisioned.resolve("initech", provision=True)

    async def test_concurrent_first_requests_ask_the_target_one_at_a_time(
        self, systems, deployment
    ) -> None:
        deployment.gate = asyncio.Event()
        calls = [asyncio.create_task(systems.for_tenant("initech")) for _ in range(5)]
        await asyncio.sleep(0)
        assert deployment.provision_calls == ["initech"]
        deployment.gate.set()
        results = await asyncio.gather(*calls, return_exceptions=True)
        assert all(isinstance(r, MemoryPreparingError) for r in results)
        assert deployment.created == 1
        assert systems.health()["provisioning"] == {"started": 1, "preparing": 4}

    async def test_concurrent_requests_after_it_is_ready_build_one_backend(
        self, systems, deployment
    ) -> None:
        deployment.make_ready("initech")
        results = await asyncio.gather(*(systems.for_tenant("initech") for _ in range(5)))
        assert len({id(backend) for _, backend in results}) == 1
        assert deployment.provision_calls == ["initech"]

    def test_a_target_that_does_not_provision_is_refused(self) -> None:
        class Plain(FakeMemoryDeployment):
            provisions_memory_systems = False

        with pytest.raises(MimirConfigurationError, match="does not provision"):
            ProvisionedSystems(Plain(), build=gbrain_backend, retry_after_seconds=1)
        with pytest.raises(MimirConfigurationError, match="positive"):
            ProvisionedSystems(FakeMemoryDeployment(), build=gbrain_backend, retry_after_seconds=0)

    async def test_closing_closes_the_target(self, brains, deployment) -> None:
        built = MemorySystems({}, {}, provisioned=_provisioned(deployment))
        deployment.make_ready("initech")
        await built.for_tenant("initech")
        await built.close()
        assert deployment.closed


class TestTheGateway:
    async def test_a_session_of_a_new_org_gets_its_own_brain_once_ready(
        self, brains, systems, deployment
    ) -> None:
        acme, initech = brains
        gateway = MemoryGateway(systems, default_result_count=10)
        with pytest.raises(MemoryPreparingError):
            await gateway.open(context(tenant="initech"))
        deployment.make_ready("initech")
        session = await gateway.open(context(tenant="initech"))
        assert session.view.system == memory_system_name("initech")
        assert [a["name"] for n, a in initech.calls if n == "sources_add"]
        assert not [a for n, a in acme.calls if n == "sources_add"]

    async def test_a_connector_import_into_an_org_without_a_system_is_refused(
        self, systems, deployment
    ) -> None:
        gateway = MemoryGateway(systems, default_result_count=10)
        with pytest.raises(TenantNotConfiguredError):
            await gateway.import_source(
                tenant_id="initech",
                layer="org",
                connector="linear",
                kind="ticket",
                title="LIN-1",
                content="hello",
                origin_url="https://linear.app/initech/LIN-1",
                external_id="LIN-1",
                upstream_updated_at=datetime(2026, 11, 13, tzinfo=UTC),
                fetched_at=datetime(2026, 11, 13, tzinfo=UTC),
            )
        assert deployment.provision_calls == []


class _NoLifetimes(LayerLifetimes):
    async def ended(self, sessions, runs) -> Lifetimes:
        return Lifetimes(sessions={}, runs={})

    async def close(self) -> None:
        return None


class TestRetention:
    async def test_the_sweep_covers_ready_provisioned_systems(self, systems, deployment) -> None:
        deployment.make_ready("initech")
        deployment.make_failed("globex", "stalled")
        sweeper = RetentionSweeper(
            systems, _NoLifetimes(), retention_days=14, interval_seconds=60, forget=None
        )
        reports = await sweeper.sweep()
        assert [r.system for r in reports] == ["acme-brain", memory_system_name("initech")]
        assert sweeper.failed_sweeps == 0

    async def test_a_list_that_cannot_be_read_is_counted_and_the_rest_swept(
        self, systems, deployment
    ) -> None:
        deployment.error = "forbidden"
        sweeper = RetentionSweeper(
            systems, _NoLifetimes(), retention_days=14, interval_seconds=60, forget=None
        )
        reports = await sweeper.sweep()
        assert [r.system for r in reports] == ["acme-brain"]
        assert sweeper.failed_sweeps == 1
        assert degraded_counts()["memory.retention_sweep"] == 1

    async def test_one_unusable_system_does_not_hide_the_others(self, brains, deployment) -> None:
        taken = memory_system_name("globex")
        built = MemorySystems(
            {taken: gbrain_backend()}, {"acme": taken}, provisioned=_provisioned(deployment)
        )
        deployment.make_ready("initech")
        deployment.make_ready("globex")
        provisioned, unusable = await built.provisioned()
        assert [name for name, _ in provisioned] == [memory_system_name("initech")]
        assert len(unusable) == 1 and "already uses" in str(unusable[0])
        await built.close()


class TestConfiguration:
    def test_the_deployment_needs_an_adapter(self) -> None:
        with pytest.raises(ValueError, match="no 'adapter'"):
            MemoryProvisioningConfig(deployment={"namespace": "memory"})

    def test_the_backend_cannot_set_the_connection(self) -> None:
        with pytest.raises(ValueError, match="admin_token, mcp_url"):
            MemoryProvisioningConfig(
                deployment={"adapter": _FAKE}, backend={"mcp_url": "x", "admin_token": "y"}
            )

    async def test_build_memory_systems_wires_the_target_and_backend_arguments(
        self, brains
    ) -> None:
        memory = MemoryGatewayConfig(
            provisioning={
                "deployment": {"adapter": _FAKE, "instance": "built", "mcp_url": INITECH_MCP},
                "retry_after_seconds": 7,
                "backend": {"page_size": 3},
            }
        )
        systems = build_memory_systems(memory)
        deployment = FakeMemoryDeployment.instances["built"]
        with pytest.raises(MemoryPreparingError) as preparing:
            await systems.for_tenant("initech")
        assert preparing.value.retry_after == 7
        deployment.make_ready("initech")
        _, backend = await systems.for_tenant("initech")
        assert backend._page_size == 3
        await systems.close()

    @pytest.mark.parametrize(
        ("adapter", "match"),
        [
            ("nope.Missing", "cannot be imported"),
            ("mimir.config.MimirServiceConfig", "not a KnowledgeDeploymentPort"),
        ],
    )
    def test_a_target_that_cannot_be_built_stops_the_service(self, adapter, match) -> None:
        memory = MemoryGatewayConfig(provisioning={"deployment": {"adapter": adapter}})
        with pytest.raises(MimirConfigurationError, match=match):
            build_memory_systems(memory)

    def test_unknown_target_arguments_stop_the_service(self) -> None:
        memory = MemoryGatewayConfig(
            provisioning={"deployment": {"adapter": _FAKE, "unknown": True}}
        )
        with pytest.raises(MimirConfigurationError, match="could not be built"):
            build_memory_systems(memory)


def _forge_answer(tenant: str) -> dict:
    return {
        "session_id": SESSION_ID,
        "launch_id": "launch-1",
        "owner_id": "ines",
        "tenant_id": tenant,
        "project_id": PROJECT_ID,
        "workflow_run_id": None,
        "project_memory": {"write_prefixes": ["decisions/"]},
        "objective": None,
        "project_brief": None,
        "since": None,
        "attached": [],
    }


class TestRoutes:
    @pytest.fixture
    def forge(self, brains):
        with respx.mock(assert_all_called=False) as router:
            for brain in brains:
                brain.mount(router)
            yield router.get(CONTEXT_URL).mock(
                return_value=httpx.Response(200, json=_forge_answer("initech"))
            )

    def _client(self, tmp_path: Path) -> TestClient:
        settings = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            backend={"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"},
            memory={
                "enabled": True,
                "forge_base_url": FORGE,
                "systems": [
                    {
                        "name": "acme-brain",
                        "adapter": "mimir.backends.gbrain.GBrainBackend",
                        "mcp_url": MCP,
                        "admin_token": ADMIN_TOKEN,
                        "signing_key": SIGNING_KEY,
                    }
                ],
                "tenants": {"acme": "acme-brain"},
                "provisioning": {
                    "deployment": {"adapter": _FAKE, "instance": "app", "mcp_url": INITECH_MCP},
                    "retry_after_seconds": 12.5,
                },
                "service_auth": {"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
            },
        )
        return TestClient(create_app(settings))

    def test_preparing_is_a_503_with_retry_after_then_the_tools_answer(
        self, tmp_path, forge
    ) -> None:
        auth = {"Authorization": f"Bearer {session_token()}"}
        with self._client(tmp_path) as client:
            deployment = FakeMemoryDeployment.instances["app"]
            preparing = client.get("/mimir/memory/tools", headers=auth)
            assert preparing.status_code == 503
            assert preparing.headers["retry-after"] == "13"
            assert "being prepared" in preparing.json()["detail"]
            assert client.get("/health").json()["memory"]["provisioning"] == {"started": 1}
            deployment.make_ready("initech")
            assert client.get("/mimir/memory/tools", headers=auth).status_code == 200
        assert deployment.created == 1

    def test_a_failed_provisioning_is_a_502_naming_the_cause(self, tmp_path, forge) -> None:
        auth = {"Authorization": f"Bearer {session_token()}"}
        with self._client(tmp_path) as client:
            FakeMemoryDeployment.instances["app"].make_failed("initech", "Stalled: no chart")
            failed = client.get("/mimir/memory/tools", headers=auth)
        assert failed.status_code == 502
        assert "no chart" in failed.json()["detail"]
        assert "retry-after" not in failed.headers

    def test_an_unconfigured_org_has_no_retry_after(self, tmp_path, forge) -> None:
        settings = MimirServiceConfig(
            path=str(tmp_path / "mimir"),
            backend={"adapter": "tests.test_mimir.unit.fakes.InMemoryMimirAdapter"},
            memory={
                "enabled": True,
                "forge_base_url": FORGE,
                "service_auth": {"adapter": "niuu.adapters.outbound.http_auth.NoAuthHeaderAdapter"},
            },
        )
        auth = {"Authorization": f"Bearer {session_token()}"}
        with TestClient(create_app(settings)) as client:
            refused = client.get("/mimir/memory/tools", headers=auth)
            assert client.get("/health").json()["memory"] == {"configured_systems": 0}
        assert refused.status_code == 503
        assert "retry-after" not in refused.headers
