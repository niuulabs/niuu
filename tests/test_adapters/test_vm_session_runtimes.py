"""A VM Forge runs one runtime, offers it read-only, and reports warm readiness."""

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from tests.test_adapters.test_file_execution_catalog import _yaml
from tests.test_adapters.test_vm_execution_lifecycle import (
    RecordingPlanRuntime,
    RecordingPreparation,
    _resolver,
)
from tests.test_adapters.test_vm_pod_manager import ReadyProvider, Repository
from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.adapters.outbound.ssh_openshell_vm_runtime import SshOpenShellVmRuntime
from volundr.adapters.outbound.ssh_vm_runtime import SshContainerVmRuntime
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.domain.compute import ComputePoolPolicy, MachineBootstrap
from volundr.domain.execution_catalog import ExecutionCatalogError, ExecutionSelectionError
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.compute_pool import ComputePoolService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.session_runtimes import (
    ReadinessState,
    SessionGrant,
    SessionRuntimeKind,
    requested_grants,
)
from volundr.domain.vm_runtime import VmRuntime


class KindRuntime(RecordingPlanRuntime):
    def __init__(self, name: str, kind: SessionRuntimeKind | None, grants=()) -> None:
        super().__init__(name)
        self._kind = kind
        self._grants = tuple(grants)

    @property
    def session_runtime(self):
        return self._kind

    @property
    def session_grants(self):
        return self._grants


def _catalog(tmp_path: Path) -> ExecutionPlanResolver:
    """The lifecycle catalog plus a second execution on the same host, ``vm-b``."""
    resolver = _resolver(tmp_path)
    execution = yaml.safe_load((tmp_path / "executions" / "vm.yaml").read_text(encoding="utf-8"))
    _yaml(tmp_path / "executions" / "vm-b.yaml", {**execution, "id": "vm-b"})
    return resolver


def _manager(resolver: ExecutionPlanResolver, runtimes: dict[str, VmRuntime], *, max_machines=2):
    plans = {plan.execution.id: plan for plan in resolver.plans()}
    repository = Repository()
    provider = ReadyProvider()
    leases = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=max_machines,
        bootstrap=MachineBootstrap(),
        bootstrap_store=MemoryCredentialStore(),
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=max_machines, poll_interval_seconds=0.001
    )
    manager.configure_compute(
        leases,
        repository,
        RecordingPlanRuntime("legacy"),
        MachineBootstrap(),
        pool_id="pool",
        max_machines=max_machines,
    )
    by_digest = {plans[name].plan_digest: runtime for name, runtime in runtimes.items()}
    manager.configure_execution(
        resolver,
        by_digest,
        {plan.plan_digest: RecordingPreparation() for plan in plans.values()},
    )
    return manager, leases, repository, provider, plans


def _legacy_manager(runtime: VmRuntime) -> VmPodManager:
    """A Forge without an execution catalog, running ``compute.runtime``."""
    repository = Repository()
    leases = ComputeLeaseService(
        repository,
        ReadyProvider(),
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=MemoryCredentialStore(),
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    manager = VmPodManager(
        profile="small", pool_id="pool", max_machines=2, poll_interval_seconds=0.001
    )
    manager.configure_compute(
        leases, repository, runtime, MachineBootstrap(), pool_id="pool", max_machines=2
    )
    return manager


def _session(manager: VmPodManager, plan, **config) -> Session:
    return Session(
        id=uuid4(),
        name="runtime choice",
        owner_id="owner",
        tenant_id="tenant",
        workload_config={"execution_profile": str(plan.execution), **config},
    )


async def test_the_default_executions_runtime_is_the_one_offer(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, *_ = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER, [SessionGrant.DOCKER]),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.OPENSHELL, [SessionGrant.SUDO]),
        },
    )

    [offer] = await manager.session_runtimes()

    assert offer.runtime is SessionRuntimeKind.CONTAINER
    assert offer.grants == (SessionGrant.DOCKER,)
    assert offer.readiness.state is ReadinessState.BOOT


async def test_a_default_runtime_that_declares_no_kind_offers_nothing(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, *_ = _manager(
        resolver,
        {"vm": KindRuntime("vm", None), "vm-b": KindRuntime("vm-b", SessionRuntimeKind.OPENSHELL)},
    )

    assert await manager.session_runtimes() == ()


async def test_a_forge_without_a_catalog_offers_its_configured_runtime():
    manager = _legacy_manager(KindRuntime("legacy", SessionRuntimeKind.HOST, [SessionGrant.SUDO]))

    [offer] = await manager.session_runtimes()

    assert offer.runtime is SessionRuntimeKind.HOST
    assert offer.grants == (SessionGrant.SUDO,)
    assert offer.readiness.state is ReadinessState.BOOT


async def test_a_forge_without_a_catalog_whose_runtime_declares_no_kind_offers_nothing():
    manager = _legacy_manager(KindRuntime("legacy", None))

    assert await manager.session_runtimes() == ()


async def test_an_uncomposed_pod_manager_refuses_to_say_what_it_runs():
    manager = VmPodManager(profile="small", pool_id="pool", max_machines=1)

    with pytest.raises(RuntimeError, match="VM runtime is not composed"):
        await manager.session_runtimes()


async def test_a_free_prepared_machine_makes_the_default_runtime_warm(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, leases, repository, provider, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.OPENSHELL),
        },
    )
    pool = ComputePoolService(
        repository,
        leases,
        provider,
        RecordingPlanRuntime("legacy-pool"),
        pool_id="pool",
        defaults=ComputePoolPolicy(profile="small", max_machines=2, warm_min=1),
        bootstrap=MachineBootstrap(),
        interval_seconds=0.001,
    )
    pool.configure_execution(
        resolver,
        {plan.plan_digest: KindRuntime("pool", None) for plan in plans.values()},
        {plan.plan_digest: RecordingPreparation() for plan in plans.values()},
    )
    manager.configure_pool(pool)
    await pool.maintain()
    await pool.maintain()

    [offer] = await manager.session_runtimes()

    assert (offer.readiness.state, offer.readiness.warm) == (ReadinessState.WARM, 1)

    # A session takes the spare's only slot: it is in service, not warm.
    plan = plans["vm"]
    session = _session(manager, plan, _compute_execution=manager.execution_reference(plan))
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert len(repository.leases) == 1

    [offer] = await manager.session_runtimes()

    assert (offer.readiness.state, offer.readiness.warm) == (ReadinessState.BOOT, 0)


async def test_a_full_pool_names_where_capacity_is_raised(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, _, _, _, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", None),
        },
        max_machines=1,
    )
    plan = plans["vm"]
    session = _session(manager, plan, _compute_execution=manager.execution_reference(plan))
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    [offer] = await manager.session_runtimes()

    assert offer.readiness.state is ReadinessState.FULL
    assert offer.readiness.warm == 0
    assert "pool capacity" in offer.readiness.remedy


async def test_grants_the_selected_runtime_does_not_offer_are_refused(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, _, _, _, plans = _manager(
        resolver,
        {
            "vm": KindRuntime("vm", SessionRuntimeKind.CONTAINER),
            "vm-b": KindRuntime("vm-b", SessionRuntimeKind.HOST, [SessionGrant.SUDO]),
        },
    )

    with pytest.raises(ExecutionSelectionError, match=r"does not offer runtime_grants \['sudo'\]"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants=["sudo"]))
    with pytest.raises(ExecutionSelectionError, match=r"\['docker'\]"):
        await manager.resolve_execution(
            _session(manager, plans["vm-b"], runtime_grants=["sudo", "docker"])
        )
    plan = await manager.resolve_execution(
        _session(manager, plans["vm-b"], runtime_grants=["sudo"])
    )
    assert plan.execution.id == "vm-b"


async def test_malformed_grants_are_a_selection_error(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, _, _, _, plans = _manager(
        resolver,
        {"vm": KindRuntime("vm", None), "vm-b": KindRuntime("vm-b", None)},
    )

    with pytest.raises(ExecutionSelectionError, match="list of grant names"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants="sudo"))
    with pytest.raises(ExecutionSelectionError, match="known grants: sudo, docker"):
        await manager.resolve_execution(_session(manager, plans["vm"], runtime_grants=["root"]))


async def test_grants_on_a_legacy_allocation_are_refused(tmp_path: Path):
    resolver = _catalog(tmp_path)
    manager, leases, _, _, _ = _manager(
        resolver,
        {"vm": KindRuntime("vm", None), "vm-b": KindRuntime("vm-b", None)},
    )
    session = Session(
        id=uuid4(),
        name="legacy",
        owner_id="owner",
        tenant_id="tenant",
        workload_config={"runtime_grants": ["docker"]},
    )
    await leases.acquire(
        session_id=session.id,
        owner_id="owner",
        tenant_id="tenant",
        profile="small",
        bootstrap=MachineBootstrap(),
    )

    with pytest.raises(ExecutionSelectionError, match="does not offer"):
        await manager.resolve_execution(session)


def test_requested_grants_reads_absent_as_none():
    assert requested_grants({}) == frozenset()
    assert requested_grants({"runtime_grants": ["docker"]}) == {SessionGrant.DOCKER}


def test_a_catalog_that_still_lists_launch_executions_must_drop_them(tmp_path: Path):
    _catalog(tmp_path)
    catalog_path = tmp_path / "catalog.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    catalog["launch_executions"] = [{"id": "vm", "revision": "2026-09-18"}]
    _yaml(catalog_path, catalog)

    with pytest.raises(ExecutionCatalogError, match="Remove launch_executions from catalog.yaml"):
        FileExecutionCatalog(tmp_path).load()


def test_vm_runtime_adapters_declare_what_sessions_run_in():
    assert SshContainerVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.CONTAINER
    assert SshOpenShellVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.OPENSHELL
    assert VmRuntime.session_runtime.fget(None) is None
    assert VmRuntime.session_grants.fget(None) == ()


def test_the_openshell_gateway_runtime_runs_sessions_in_openshell():
    pytest.importorskip("openshell")
    from volundr.adapters.outbound.openshell_vm_runtime import OpenShellVmRuntime

    assert OpenShellVmRuntime.session_runtime.fget(None) is SessionRuntimeKind.OPENSHELL
