"""Resolved execution plans stay pinned across VM warm/cold lifecycle operations."""

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import yaml

from niuu.adapters.memory_credential_store import MemoryCredentialStore
from niuu.ports.session_proxy import SessionProxyTarget
from tests.test_adapters.test_file_execution_catalog import _catalog, _yaml
from tests.test_adapters.test_vm_pod_manager import ReadyProvider, Repository
from volundr.adapters.outbound.file_execution_catalog import FileExecutionCatalog
from volundr.adapters.outbound.vm_pod_manager import VmPodManager
from volundr.domain.compute import ComputePoolPolicy, MachineBootstrap
from volundr.domain.execution_catalog import ExecutionCatalogError
from volundr.domain.host_preparation import HostPreparation, HostPreparationResult
from volundr.domain.models import PodSpecAdditions, Session, SessionSpec, SessionStatus
from volundr.domain.services.compute_leases import ComputeLeaseService
from volundr.domain.services.compute_pool import ComputePoolService
from volundr.domain.services.execution_catalog import ExecutionPlanResolver
from volundr.domain.vm_runtime import VmRuntime


class RecordingPreparation(HostPreparation):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def machine_bootstrap(self, recipe, defaults):
        self.calls.append("machine_bootstrap")
        return defaults.model_copy(update={"commands": (("machine-only",),)})

    async def ensure(self, lease, bootstrap, recipe):
        self.calls.append("ensure")
        assert bootstrap.commands == (("machine-only",),)
        return HostPreparationResult(
            recipe_digest=recipe.digest,
            stages=(),
            facts={"architecture": "amd64", "cgroup": "v2"},
        )

    async def observe(self, lease, bootstrap, recipe):
        self.calls.append("observe")
        return HostPreparationResult(
            recipe_digest=recipe.digest,
            stages=(),
            facts={"architecture": "amd64", "cgroup": "v2"},
        )

    async def close(self):
        self.calls.append("close")


class RecordingPlanRuntime(VmRuntime):
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[str] = []

    def machine_bootstrap(self, defaults):
        raise AssertionError("catalog lifecycle uses HostPreparation machine bootstrap")

    def session_bootstrap(self, session, spec, machine):
        self.calls.append("session_bootstrap")
        return MachineBootstrap(commands=(("session-only",),))

    def bootstrap(self, session, spec, defaults):
        raise AssertionError("catalog lifecycle uses split machine/session bootstrap")

    async def warm(self, lease, bootstrap):
        self.calls.append("warm")

    async def prepare(self, lease, bootstrap):
        self.calls.append("prepare")

    async def start(self, lease, bootstrap):
        self.calls.append("start")

    async def target(self, lease, bootstrap):
        self.calls.append("target")
        return SessionProxyTarget(f"http://{self.name}", self.name, 9000)

    async def ready(self, lease, bootstrap):
        self.calls.append("ready")
        return True

    async def stop(self, lease, bootstrap):
        self.calls.append("stop")

    async def close(self):
        self.calls.append("close")


def _resolver(tmp_path: Path) -> ExecutionPlanResolver:
    root, _ = _catalog(tmp_path)
    machine_path = root / "machines" / "ubuntu.yaml"
    machine = yaml.safe_load(machine_path.read_text(encoding="utf-8"))
    machine["provider_profile"] = "small"
    _yaml(machine_path, machine)
    execution_path = root / "executions" / "vm.yaml"
    execution = yaml.safe_load(execution_path.read_text(encoding="utf-8"))
    execution["pool_id"] = "pool"
    execution["provider_binding"] = "provider"
    _yaml(execution_path, execution)
    return ExecutionPlanResolver(FileExecutionCatalog(root))


def _setup(tmp_path: Path):
    resolver = _resolver(tmp_path)
    plan = resolver.resolve()
    repository = Repository()
    provider = ReadyProvider()
    store = MemoryCredentialStore()
    leases = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    legacy = RecordingPlanRuntime("legacy")
    runtime = RecordingPlanRuntime("pinned")
    preparation = RecordingPreparation()
    manager = VmPodManager(
        profile="small",
        pool_id="pool",
        max_machines=2,
        poll_interval_seconds=0.001,
    )
    manager.configure_compute(
        leases,
        repository,
        legacy,
        MachineBootstrap(),
        pool_id="pool",
        max_machines=2,
    )
    manager.configure_execution(
        resolver,
        {plan.plan_digest: runtime},
        {plan.plan_digest: preparation},
    )
    session = Session(
        id=uuid4(),
        name="catalog VM",
        owner_id="owner",
        tenant_id="tenant",
        workload_config={
            "execution_profile": str(plan.execution),
            "_compute_execution": manager.execution_reference(plan),
        },
    )
    return manager, leases, repository, provider, runtime, preparation, resolver, plan, session


async def test_cold_start_persists_plan_and_never_sends_session_bootstrap_to_provider(
    tmp_path: Path,
):
    manager, _, repository, provider, runtime, preparation, _, plan, session = _setup(tmp_path)
    requests = []
    create = provider.create

    async def recording_create(request):
        requests.append(request)
        return await create(request)

    provider.create = recording_create
    spec = SessionSpec(values={}, pod_spec=PodSpecAdditions())

    await manager.start(session, spec)

    lease = next(iter(repository.leases.values()))
    assert lease.execution_plan == plan
    assert lease.host_preparation_result["recipe_digest"] == plan.host_recipe_digest
    assert requests[0].bootstrap.commands == (("machine-only",),)
    assert (await manager.execution_for(session)).plan_digest == plan.plan_digest
    assert preparation.calls == ["machine_bootstrap", "ensure"]
    assert runtime.calls[:3] == ["session_bootstrap", "prepare", "start"]
    assert await manager.status(session) == SessionStatus.RUNNING


async def test_warm_and_cold_paths_share_the_pinned_host_contract(tmp_path: Path):
    manager, leases, repository, provider, runtime, preparation, resolver, plan, session = _setup(
        tmp_path
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
        {plan.plan_digest: runtime},
        {plan.plan_digest: preparation},
    )
    manager.configure_pool(pool)

    await pool.maintain()
    await pool.maintain()
    spare = next(iter(repository.leases.values()))
    assert spare.execution_plan == plan
    assert spare.host_preparation_result["recipe_digest"] == plan.host_recipe_digest
    creates = len(provider.created)

    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    bound = await repository.active_for_session("pool", session.id)
    assert bound.id == spare.id
    assert bound.execution_plan == plan
    assert len(provider.created) == creates
    assert preparation.calls.count("ensure") == 1
    assert preparation.calls.count("observe") == 1


async def test_changed_provider_binding_blocks_cleanup_without_mutating_provider(tmp_path: Path):
    manager, _, repository, provider, _, _, _, _, session = _setup(tmp_path)
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    lease = next(iter(repository.leases.values()))
    changed = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=2,
        bootstrap=MachineBootstrap(),
        bootstrap_store=MemoryCredentialStore(),
        provider_binding="provider",
        provider_fingerprint="provider-config-v2",
    )

    with pytest.raises(RuntimeError, match="provider binding"):
        await changed.release(lease.id)

    assert lease.id in provider.machines
    assert repository.leases[lease.id].state != "released"


async def test_changed_default_keeps_active_lease_on_its_pinned_runtime(tmp_path: Path):
    manager, _, repository, provider, runtime, _, _, _, session = _setup(tmp_path)
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    execution_path = tmp_path / "executions" / "vm.yaml"
    second = yaml.safe_load(execution_path.read_text(encoding="utf-8"))
    second["id"] = "next-default"
    _yaml(tmp_path / "executions" / "next-default.yaml", second)
    catalog_path = tmp_path / "catalog.yaml"
    catalog = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    catalog["default_execution"] = {"id": "next-default", "revision": "2026-09-18"}
    _yaml(catalog_path, catalog)

    assert await manager.status(session) == SessionStatus.RUNNING
    assert await manager.stop(session)

    assert "ready" in runtime.calls
    assert "stop" in runtime.calls
    assert not provider.machines
    assert next(iter(repository.leases.values())).execution_plan.execution.id == "vm"


async def test_released_pin_resolves_for_cleanup_after_provider_profile_removal(tmp_path: Path):
    manager, _, _, provider, _, _, _, plan, session = _setup(tmp_path)
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    assert await manager.stop(session)
    provider.profiles = AsyncMock(return_value=())

    assert (await manager.execution_for(session)).plan_digest == plan.plan_digest
    with pytest.raises(
        ExecutionCatalogError, match="incompatible with the configured compute provider"
    ):
        await manager.resolve_execution(session)


async def test_changed_pinned_runtime_config_blocks_stop_and_preserves_machine(tmp_path: Path):
    manager, _, repository, provider, runtime, _, _, _, session = _setup(tmp_path)
    await manager.start(session, SessionSpec(values={}, pod_spec=PodSpecAdditions()))
    runtime_path = tmp_path / "runtimes" / "openshell.yaml"
    configured = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    configured["capabilities"].append("changed-after-allocation")
    _yaml(runtime_path, configured)

    with pytest.raises(RuntimeError, match="changed"):
        await manager.stop(session)

    lease = next(iter(repository.leases.values()))
    assert "stop" not in runtime.calls
    assert lease.id in provider.machines
    assert repository.leases[lease.id].state != "released"


async def test_catalog_enablement_preserves_active_legacy_lease(tmp_path: Path):
    manager, leases, _, _, _, _, _, _, session = _setup(tmp_path)
    await leases.acquire(
        session_id=session.id,
        owner_id=session.owner_id,
        tenant_id=session.tenant_id,
        profile="small",
        bootstrap=MachineBootstrap(),
    )
    legacy_session = session.model_copy(update={"workload_config": {}})

    assert await manager.resolve_execution(legacy_session) is None
    with pytest.raises(ValueError, match="active legacy"):
        await manager.resolve_execution(
            legacy_session.model_copy(
                update={"workload_config": {"execution_profile": "vm@2026-09-18"}}
            )
        )


async def test_session_bootstrap_is_durable_before_first_provider_call(tmp_path: Path):
    resolver = _resolver(tmp_path)
    plan = resolver.resolve()
    repository = Repository()
    provider = ReadyProvider()
    store = MemoryCredentialStore()
    leases = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    machine_bootstrap = MachineBootstrap(commands=(("machine-only",),))
    session_bootstrap = MachineBootstrap(commands=(("session-only",),))

    async def interrupted_create(request):
        lease = repository.leases[request.allocation_id]
        machine_value = await store.get_value("compute", lease.bootstrap_owner, lease.bootstrap_ref)
        session_value = await store.get_value(
            "compute", lease.bootstrap_owner, lease.session_bootstrap_ref
        )
        assert MachineBootstrap.model_validate_json(machine_value["bootstrap"]) == machine_bootstrap
        assert MachineBootstrap.model_validate_json(session_value["bootstrap"]) == session_bootstrap
        assert request.bootstrap == machine_bootstrap
        raise RuntimeError("controller interrupted after request")

    provider.create = interrupted_create

    with pytest.raises(RuntimeError, match="interrupted"):
        await leases.acquire(
            session_id=uuid4(),
            owner_id="owner",
            tenant_id="tenant",
            profile="small",
            bootstrap=machine_bootstrap,
            session_bootstrap=session_bootstrap,
            execution_plan=plan,
        )

    lease = next(iter(repository.leases.values()))
    assert lease.session_bootstrap_ref is not None
    assert await leases.bootstrap_for(lease) == session_bootstrap


async def test_catalog_start_rejects_session_without_resolved_pin(tmp_path: Path):
    manager, _, repository, _, _, _, _, plan, session = _setup(tmp_path)
    unpinned = session.model_copy(
        update={"workload_config": {"execution_profile": str(plan.execution)}}
    )

    with pytest.raises(ValueError, match="resolved execution pin"):
        await manager.start(unpinned, SessionSpec(values={}, pod_spec=PodSpecAdditions()))

    assert not repository.leases


async def test_reconcile_never_creates_while_referenced_session_bootstrap_is_missing(
    tmp_path: Path,
):
    plan = _resolver(tmp_path).resolve()
    repository = Repository()
    provider = ReadyProvider()
    store = MemoryCredentialStore()
    leases = ComputeLeaseService(
        repository,
        provider,
        pool_id="pool",
        max_machines=1,
        bootstrap=MachineBootstrap(),
        bootstrap_store=store,
        provider_binding="provider",
        provider_fingerprint="provider-config-v1",
    )
    store_value = store.store

    async def interrupted_store(namespace, owner, name, secret_type, value):
        if "-session-" in name:
            raise RuntimeError("interrupted before session bootstrap write")
        await store_value(namespace, owner, name, secret_type, value)

    store.store = interrupted_store
    with pytest.raises(RuntimeError, match="interrupted"):
        await leases.acquire(
            session_id=uuid4(),
            owner_id="owner",
            tenant_id="tenant",
            profile="small",
            bootstrap=MachineBootstrap(commands=(("machine-only",),)),
            session_bootstrap=MachineBootstrap(commands=(("session-only",),)),
            execution_plan=plan,
        )
    lease = next(iter(repository.leases.values()))
    assert not provider.created

    with pytest.raises(RuntimeError, match="Session bootstrap is missing"):
        await leases.reconcile(lease.id)

    assert not provider.created
